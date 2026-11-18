"""Ed25519 signatures and SHA-256 pins for tool manifests.

The signed payload is the canonical JSON object ``{name, description,
input_schema}`` with sorted keys. Extra fields on a tool definition are not
covered by the signature.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical_manifest(tool_def: Mapping[str, Any]) -> bytes:
    """Encode the signed subset of a tool definition as canonical JSON bytes."""
    payload = {
        "name": tool_def["name"],
        "description": tool_def["description"],
        "input_schema": tool_def["input_schema"],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return encoded.encode("utf-8")


def sign_manifest(tool_def: Mapping[str, Any], private_key: Ed25519PrivateKey) -> bytes:
    """Sign the canonical manifest. Returns the raw Ed25519 signature."""
    return private_key.sign(canonical_manifest(tool_def))


def verify_manifest(
    tool_def: Mapping[str, Any],
    signature: bytes,
    public_key: Ed25519PublicKey,
) -> bool:
    """Return whether ``signature`` matches the canonical manifest."""
    try:
        public_key.verify(signature, canonical_manifest(tool_def))
    except InvalidSignature:
        return False
    return True


def sha256_pin(tool_def: Mapping[str, Any]) -> str:
    """Return the lowercase hex SHA-256 of the canonical manifest."""
    return hashlib.sha256(canonical_manifest(tool_def)).hexdigest()


def verify_pin(tool_def: Mapping[str, Any], pin: str) -> bool:
    """Return whether ``pin`` is the SHA-256 of the canonical manifest."""
    actual = sha256_pin(tool_def)
    candidate = pin.strip().lower()
    if len(candidate) != len(actual):
        return False
    return hmac.compare_digest(actual, candidate)
