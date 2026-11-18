"""Minimal macaroon-style capability tokens.

Tokens are HMAC-SHA256 chains. ``mint`` binds an identifier to a root key.
``add_caveat`` appends one constraint and re-keys the signature with the
previous digest, so caveats can be added but not removed or reordered without
detection. ``verify`` checks the chain and then requires every caveat to hold.
There is no third-party macaroon library.

Caveats are conjunctive and only narrow access. Two forms are recognized:

- ``"<field> = <value>"``, for example ``"action = disclose_secret"``
- ``"<field> in <v1>,<v2>,..."``, for example ``"tool in read_file,send_email"``

``mint`` is the low-level constructor and can return an unattenuated token.
Task code should call ``mint_for_task``, which always adds a ``tool in`` caveat.
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
    """Mint an unattenuated token. Any authentic request is allowed until a caveat is added.

    Prefer ``mint_for_task`` when issuing a token for a task. This constructor
    stays available so caveats can be chained in tests and inside ``mint_for_task``.
    """
    signature = _mac(root_key, identifier.encode("utf-8"))
    return CapabilityToken(identifier=identifier, caveats=(), signature=signature.hex())


def mint_for_task(
    root_key: bytes,
    task_id: str,
    allowed_tools: list[str],
    allow_disclose_secret: bool,
) -> CapabilityToken:
    """Mint a task token that always carries a ``tool in`` caveat.

    ``allow_disclose_secret`` appends ``action = disclose_secret`` when true.
    That caveat is conjunctive, like every other caveat: a request must then
    include ``action=disclose_secret`` in addition to naming an allowed tool.
    An empty ``allowed_tools`` list encodes ``tool in`` with no members, which
    matches no request.
    """
    for tool_name in allowed_tools:
        if tool_name == "" or "," in tool_name or any(char.isspace() for char in tool_name):
            raise ValueError(f"tool name {tool_name!r} cannot be encoded in a set caveat")
    token = add_caveat(mint(root_key, task_id), "tool in " + ",".join(allowed_tools))
    if allow_disclose_secret:
        token = add_caveat(token, "action = disclose_secret")
    return token


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


def _parse_caveat(caveat: str) -> tuple[str, str, str | tuple[str, ...]] | None:
    """Parse one caveat. Equality is tried only when the field is a single token."""
    if " = " in caveat:
        field, value = caveat.split(" = ", 1)
        field = field.strip()
        if field and " " not in field and value != "":
            return ("eq", field, value)
    if " in " in caveat:
        field, raw = caveat.split(" in ", 1)
        field = field.strip()
        if field and " " not in field:
            members = tuple(part.strip() for part in raw.split(",") if part.strip())
            return ("in", field, members)
    return None


def _caveats_hold(caveats: tuple[str, ...], request: Mapping[str, Any]) -> bool:
    for caveat in caveats:
        parsed = _parse_caveat(caveat)
        if parsed is None:
            return False
        kind, field, expected = parsed
        actual = str(request.get(field, ""))
        if kind == "eq":
            if actual != expected:
                return False
        elif actual not in expected:
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
