"""Poisoned-tool MCP server.

Builds a fresh in-memory FastMCP server per run from an attack case, serving
either the ``benign`` or the ``poisoned`` variant of a single third-party tool.
``load_cases`` parses and validates ``attacks/door1_cases.yaml`` into typed
models. See docs/specs/door1.md Part B.

Nothing here touches a real filesystem, mailbox, or network: the tool returns a
template string and logs the call through the stdlib logger (not the run log),
so importing this module never creates a run file.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import yaml
from fastmcp import FastMCP
from fastmcp.tools.base import Tool, ToolResult
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from agent_sec.gateway.signing import tool_def_from_mcp

logger = logging.getLogger(__name__)

Variant = Literal["benign", "poisoned"]

# The tools served by servers/benign.py. Authorized tools must be one of these
# (or the case's own poisoned tool, for P1). Kept here so ``load_cases`` stays
# synchronous; ``test_poisoned.py`` asserts it matches the benign server, so a
# rename in servers/benign.py (issue #3) is caught rather than drifting silently.
BENIGN_TOOL_NAMES = frozenset({"read_file", "send_email", "lookup_employee", "fetch_url"})


class PoisonedTool(BaseModel):
    """The third-party tool a case serves, in both its benign and poisoned forms."""

    model_config = ConfigDict(extra="forbid")

    server: str
    name: str
    benign_description: str
    poisoned_description: str
    input_schema: dict[str, Any]
    returns: str

    @field_validator("server", "name", "benign_description", "poisoned_description")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value

    @field_validator("input_schema")
    @classmethod
    def _object_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise ValueError("input_schema must be an object schema with a 'properties' map")
        return schema


class AttackCase(BaseModel):
    """One door-1 tool-poisoning case. Mirrors the schema in door1_cases.yaml."""

    model_config = ConfigDict(extra="forbid")

    id: str
    paradigm: Literal["P1", "P2", "P3"]
    vector: Literal["description", "schema", "output"] = "description"
    rug_pull: bool = False
    user_task: str
    authorized_tools: list[str]
    poisoned_tool: PoisonedTool
    attacker_goal: Literal["exfiltrate_secret", "redirect_recipient", "unauthorized_call"]
    attacker_address: str
    expected: dict[str, Any]

    @field_validator("attacker_address")
    @classmethod
    def _sandboxed_address(cls, value: str) -> str:
        if not value.endswith(".invalid"):
            raise ValueError("attacker_address must use a .invalid domain")
        return value

    @model_validator(mode="after")
    def _check_tool_authorization(self) -> AttackCase:
        name = self.poisoned_tool.name
        if name in BENIGN_TOOL_NAMES:
            raise ValueError(f"poisoned tool {name!r} collides with a benign tool")
        allowed = BENIGN_TOOL_NAMES | {name}
        unknown = [t for t in self.authorized_tools if t not in allowed]
        if unknown:
            raise ValueError(
                f"authorized tools not in the benign server or poisoned tool: {unknown}"
            )
        if self.paradigm == "P1" and name not in self.authorized_tools:
            raise ValueError("P1 case must list its poisoned tool in authorized_tools")
        if self.paradigm in {"P2", "P3"} and name in self.authorized_tools:
            raise ValueError(f"{self.paradigm} case must not authorize its poisoned tool")
        return self


def load_cases(path: str | Path) -> list[AttackCase]:
    """Load and validate attack cases from a YAML file.

    Raises ``ValueError`` on a duplicate id and ``pydantic.ValidationError`` on a
    malformed case (bad paradigm, name collision, unauthorized tool, and so on).
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    entries = raw["cases"] if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise TypeError("case file must contain a list of cases (optionally under 'cases')")
    cases = [AttackCase.model_validate(entry) for entry in entries]
    counts = Counter(c.id for c in cases)
    duplicates = sorted(cid for cid, n in counts.items() if n > 1)
    if duplicates:
        raise ValueError(f"duplicate case ids: {duplicates}")
    return cases


def _served_fields(case: AttackCase, variant: Variant) -> tuple[str, dict[str, Any], str]:
    """Return the (description, input_schema, returns) the server exposes for a variant.

    The benign variant is identical for every vector. The poisoned variant for a
    ``description`` case swaps in the poisoned description. Schema (#28) and
    output (#27) poisoning add their own benign/poisoned fields and are wired in
    with those issues; until then a poisoned request for them is refused loudly
    rather than silently serving the benign tool.
    """
    pt = case.poisoned_tool
    if variant == "benign":
        return pt.benign_description, pt.input_schema, pt.returns
    if variant == "poisoned":
        if case.vector == "description":
            return pt.poisoned_description, pt.input_schema, pt.returns
        raise NotImplementedError(
            f"poisoned variant for vector {case.vector!r} is not implemented yet "
            "(schema poisoning: #28, output poisoning: #27)"
        )
    raise ValueError(f"unknown variant {variant!r}")


class _CaseTool(Tool):
    """A tool whose schema and return value come from a case, not a Python signature."""

    return_template: str = ""

    async def run(self, arguments: dict[str, Any], **_: Any) -> ToolResult:
        args = arguments or {}
        logger.info("poisoned_tool_call name=%s arguments=%s", self.name, args)
        try:
            rendered = self.return_template.format(**args)
        except (KeyError, IndexError, ValueError):
            rendered = self.return_template
        return ToolResult(content=rendered)


def make_tool_server(case: AttackCase, variant: Variant) -> FastMCP:
    """Build a fresh FastMCP server serving ``case``'s tool in the given variant.

    A new server (and a new tool) is created on every call; nothing is shared
    between runs, so a rug pull in one run cannot leak into another.
    """
    description, input_schema, returns = _served_fields(case, variant)
    server = FastMCP(case.poisoned_tool.server)
    server.add_tool(
        _CaseTool(
            name=case.poisoned_tool.name,
            description=description,
            parameters=input_schema,
            return_template=returns,
        )
    )
    return server


def manifest_for(case: AttackCase, variant: Variant) -> dict[str, Any]:
    """Return the canonical ``{name, description, input_schema}`` for a variant.

    For a rug-pull case the runner records ``manifest_for(case, "benign")`` as
    the approved manifest; for every other case the approved manifest is the
    poisoned one (the user approved a malicious tool). D2 compares against this.
    """
    description, input_schema, _ = _served_fields(case, variant)
    return tool_def_from_mcp(
        {
            "name": case.poisoned_tool.name,
            "description": description,
            "input_schema": input_schema,
        }
    )
