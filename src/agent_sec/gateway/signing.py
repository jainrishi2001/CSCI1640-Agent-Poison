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


def _first_present(source: Any, names: tuple[str, ...]) -> Any:
    if isinstance(source, Mapping):
        for name in names:
            if name in source and source[name] is not None:
                return source[name]
        return None
    for name in names:
        value = getattr(source, name, None)
        if value is not None:
            return value
    return None


def _as_schema(schema: Any) -> dict[str, Any]:
    if schema is None:
        return {"type": "object", "properties": {}}
    if hasattr(schema, "model_dump"):
        dumped = schema.model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    if isinstance(schema, Mapping):
        return dict(schema)
    raise TypeError(f"unsupported input schema type: {type(schema).__name__}")


def tool_def_from_mcp(tool: Any) -> dict[str, Any]:
    """Return ``{name, description, input_schema}`` from an MCP tool or a dict.

    MCP listings call the schema ``inputSchema``. This canonical form, and the
    bytes we sign, call it ``input_schema``. Either key or attribute is accepted.
    """
    name = _first_present(tool, ("name",))
    if not isinstance(name, str) or name == "":
        raise ValueError("tool definition is missing a name")
    description = _first_present(tool, ("description",))
    schema = _first_present(tool, ("input_schema", "inputSchema"))
    return {
        "name": name,
        "description": "" if description is None else str(description),
        "input_schema": _as_schema(schema),
    }
