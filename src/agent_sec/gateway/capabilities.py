"""Minimal macaroon-style capability tokens.

Tokens are HMAC-SHA256 chains. ``mint`` binds an identifier to a root key.
``add_caveat`` appends one equality constraint and re-keys the signature with
the previous digest, so caveats can be added but not removed or reordered
without detection. ``verify`` checks the chain and then requires every caveat
to hold. There is no third-party macaroon library.

A caveat is the exact string ``"<field> = <value>"``, for example
``"tool = read_file"`` or ``"action = disclose_secret"``. Caveats are
conjunctive: adding one only narrows the set of requests that pass.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapabilityToken:
    """An identifier, the caveats chained onto it, and the hex HMAC signature."""

    identifier: str
    caveats: tuple[str, ...]
    signature: str


def _mac(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def mint(root_key: bytes, identifier: str) -> CapabilityToken:
    """Mint an unattenuated token. Any request is allowed until a caveat is added."""
    signature = _mac(root_key, identifier.encode("utf-8"))
    return CapabilityToken(identifier=identifier, caveats=(), signature=signature.hex())


def add_caveat(token: CapabilityToken, caveat: str) -> CapabilityToken:
    """Return a new token whose signature covers ``caveat`` after the existing chain.

    The input token is not modified. The new token accepts a subset of the
    requests the parent accepts.
    """
    if caveat == "":
        raise ValueError("caveat must be a non-empty string")
    signature = _mac(bytes.fromhex(token.signature), caveat.encode("utf-8"))
    return CapabilityToken(
        identifier=token.identifier,
        caveats=token.caveats + (caveat,),
        signature=signature.hex(),
    )


def _caveats_hold(caveats: tuple[str, ...], request: Mapping[str, Any]) -> bool:
    for caveat in caveats:
        field, separator, value = caveat.partition(" = ")
        if separator == "" or field == "" or value == "":
            return False
        if str(request.get(field, "")) != value:
            return False
    return True


def verify(token: CapabilityToken, root_key: bytes, request: Mapping[str, Any]) -> bool:
    """Return whether ``token`` is authentic and every caveat matches ``request``."""
    expected = _mac(root_key, token.identifier.encode("utf-8"))
    for caveat in token.caveats:
        expected = _mac(expected, caveat.encode("utf-8"))
    try:
        actual = bytes.fromhex(token.signature)
    except ValueError:
        return False
    if len(actual) != len(expected) or not hmac.compare_digest(expected, actual):
        return False
    return _caveats_hold(token.caveats, request)
