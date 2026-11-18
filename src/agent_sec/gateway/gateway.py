"""Policy gateway.

Intercepts tool calls and outgoing messages. Runs the enabled defenses in
order. The first deny wins. With no defenses, every action is allowed. Every
decision is appended to the run log.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from agent_sec.logging_utils import JsonlRunLogger

_TEXT_PREVIEW = 500


@dataclass
class ToolCallRequest:
    """A tool call the target wants to execute."""

    run_id: str
    step: int
    tool: str
    arguments: dict[str, Any]


@dataclass
class OutgoingMessage:
    """Plain text the target wants to send out."""

    run_id: str
    step: int
    text: str
    recipient: str


@dataclass
class Decision:
    """Allow or deny, with the defense that produced the verdict."""

    allowed: bool
    reason: str
    defense: str


class Defense(Protocol):
    """One policy check. ``on_tools_listed`` defaults to returning its input."""

    name: str

    def on_tools_listed(self, tools: list[Any]) -> list[Any]:
        """Filter or annotate the tool catalog. The default keeps every tool."""
        return tools

    def check_tool_call(self, req: ToolCallRequest) -> Decision:
        """Decide whether a tool call may run."""
        ...

    def check_outgoing_message(self, msg: OutgoingMessage) -> Decision:
        """Decide whether an outgoing message may be delivered."""
        ...


class PolicyGateway:
    """Run defenses in order and log each verdict."""

    def __init__(self, defenses: Sequence[Defense], logger: JsonlRunLogger) -> None:
        self.defenses = list(defenses)
        self.logger = logger

    def filter_tools(self, tools: Sequence[Any]) -> list[Any]:
        """Chain ``on_tools_listed``. A defense without the hook leaves the list as it is."""
        current = list(tools)
        for defense in self.defenses:
            hook = getattr(defense, "on_tools_listed", None)
            if hook is None:
                continue
            current = list(hook(current))
        return current

    def check_tool_call(self, req: ToolCallRequest) -> Decision:
        """Return the first deny, or the last allow when every defense permits the call."""
        return self._decide(req, "check_tool_call", "tool_call")

    def check_outgoing_message(self, msg: OutgoingMessage) -> Decision:
        """Return the first deny, or the last allow when every defense permits the message."""
        return self._decide(msg, "check_outgoing_message", "outgoing_message")

    def _decide(
        self, item: ToolCallRequest | OutgoingMessage, method_name: str, kind: str
    ) -> Decision:
        if not self.defenses:
            decision = Decision(True, "no defenses enabled", "gateway")
            self._log(item, kind, decision)
            return decision
        decision = Decision(True, "allowed", "gateway")
        for defense in self.defenses:
            decision = getattr(defense, method_name)(item)
            self._log(item, kind, decision)
            if not decision.allowed:
                return decision
        return decision

    def _log(self, item: ToolCallRequest | OutgoingMessage, kind: str, decision: Decision) -> None:
        payload: dict[str, Any] = {
            "kind": kind,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "defense": decision.defense,
            "step": item.step,
        }
        if isinstance(item, ToolCallRequest):
            payload["tool"] = item.tool
        else:
            payload["recipient"] = item.recipient
            payload["text"] = item.text[:_TEXT_PREVIEW]
        self.logger.log(item.run_id, "gateway", "decision", payload)
