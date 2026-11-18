"""Target agent.

Connects to one or more in-memory FastMCP servers, shows the gateway-filtered
tool list to the model, and executes tool calls only after the gateway allows
them. ``respond`` runs a single turn of up to ``max_steps`` model calls so a
later conversation loop can invoke it once per attacker message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from fastmcp import Client, FastMCP

from agent_sec.gateway.gateway import OutgoingMessage, PolicyGateway, ToolCallRequest
from agent_sec.gateway.signing import tool_def_from_mcp
from agent_sec.logging_utils import JsonlRunLogger

logger = logging.getLogger(__name__)

_MAX_TOOL_RESULT_CHARS = 2000
_BLOCKED_TEXT = "[message blocked by policy]"


@dataclass
class TurnResult:
    """One target turn: the text to deliver, the updated history, and counters."""

    final_text: str
    messages: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    steps: int
    total_tokens: int
    latency_s: float
    hit_max_steps: bool


class TargetAgent:
    """LLM tool-calling loop routed through a policy gateway."""

    def __init__(
        self,
        servers: list[FastMCP],
        gateway: PolicyGateway,
        model: str,
        system_prompt: str,
        max_steps: int,
        logger: JsonlRunLogger,
        llm_fn: Callable[..., Any] | None = None,
        temperature: float | None = None,
    ) -> None:
        self.servers = list(servers)
        self.gateway = gateway
        self.model = model
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.logger = logger
        self.temperature = temperature
        if llm_fn is None:
            from agent_sec.llm import chat

            llm_fn = chat
        self.llm_fn = llm_fn

    async def respond(self, messages: list[dict[str, Any]], run_id: str) -> TurnResult:
        """Run one turn from the full message history and return the updated transcript."""
        started = time.perf_counter()
        async with AsyncExitStack() as stack:
            clients = await self._connect(stack)
            tools, router = await self._catalog(clients)
            visible = self.gateway.filter_tools(tools)
            visible_names = {_tool_name(tool) for tool in visible}
            router = {name: client for name, client in router.items() if name in visible_names}
            openai_tools = [_openai_tool(tool) for tool in visible]
            return await self._loop(
                messages=list(messages),
                run_id=run_id,
                router=router,
                openai_tools=openai_tools,
                started=started,
            )

    async def _connect(self, stack: AsyncExitStack) -> list[Any]:
        clients: list[Any] = []
        for server in self.servers:
            client = Client(server)
            await stack.enter_async_context(client)
            clients.append(client)
        return clients

    async def _catalog(self, clients: Sequence[Any]) -> tuple[list[Any], dict[str, Any]]:
        tools: list[Any] = []
        router: dict[str, Any] = {}
        for client in clients:
            for tool in await client.list_tools():
                name = _tool_name(tool)
                if name in router:
                    logger.warning("duplicate tool name %s; keeping the first server", name)
                    continue
                router[name] = client
                tools.append(tool)
        return tools, router

    async def _loop(
        self,
        messages: list[dict[str, Any]],
        run_id: str,
        router: dict[str, Any],
        openai_tools: list[dict[str, Any]],
        started: float,
    ) -> TurnResult:
        working = self._with_system(messages)
        tool_calls: list[dict[str, Any]] = []
        total_tokens = 0
        steps = 0
        for index in range(self.max_steps):
            steps = index + 1
            response = await asyncio.to_thread(
                self.llm_fn,
                messages=working,
                tools=openai_tools or None,
                model=self.model,
                temperature=self.temperature,
            )
            usage_tokens = _usage_tokens(response)
            total_tokens += usage_tokens
            message = response.choices[0].message
            calls = list(message.tool_calls or [])
            self._log_llm_call(run_id, steps, response, calls, usage_tokens)
            if not calls:
                text = message.content or ""
                working.append({"role": "assistant", "content": text})
                final_text, blocked = self._deliver(run_id, steps, text)
                if blocked:
                    working[-1]["content"] = final_text
                self.logger.log(
                    run_id,
                    "target",
                    "final_message",
                    {"step": steps, "text": final_text, "blocked": blocked},
                )
                return TurnResult(
                    final_text=final_text,
                    messages=working,
                    tool_calls=tool_calls,
                    steps=steps,
                    total_tokens=total_tokens,
                    latency_s=time.perf_counter() - started,
                    hit_max_steps=False,
                )
            working.append(_assistant_tool_message(message.content, calls))
            for call in calls:
                record, tool_message = await self._handle_tool_call(run_id, steps, call, router)
                tool_calls.append(record)
                working.append(tool_message)
        self.logger.log(run_id, "target", "max_steps", {"step": steps, "max_steps": self.max_steps})
        return TurnResult(
            final_text="",
            messages=working,
            tool_calls=tool_calls,
            steps=steps,
            total_tokens=total_tokens,
            latency_s=time.perf_counter() - started,
            hit_max_steps=True,
        )

    def _with_system(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.system_prompt:
            return messages
        if any(message.get("role") == "system" for message in messages):
            return messages
        return [{"role": "system", "content": self.system_prompt}, *messages]

    def _deliver(self, run_id: str, step: int, text: str) -> tuple[str, bool]:
        decision = self.gateway.check_outgoing_message(
            OutgoingMessage(run_id=run_id, step=step, text=text, recipient="user")
        )
        if decision.allowed:
            return text, False
        return _BLOCKED_TEXT, True

    def _log_llm_call(
        self,
        run_id: str,
        step: int,
        response: Any,
        calls: Sequence[Any],
        usage_tokens: int,
    ) -> None:
        stats = getattr(response, "call_stats", None)
        mode = getattr(stats, "system_role_mode", None)
        if mode not in {"system", "folded"}:
            mode = "system"
        self.logger.log(
            run_id,
            "target",
            "system_role_mode",
            {"step": step, "value": mode},
        )
        self.logger.log(
            run_id,
            "target",
            "llm_call",
            {
                "step": step,
                "model": self.model,
                "tokens": usage_tokens,
                "latency_s": getattr(stats, "latency_s", None),
                "waited_s": getattr(stats, "waited_s", None),
                "cost_usd": getattr(stats, "cost_usd", None),
                "tool_names": [_call_name(call) for call in calls],
            },
        )

    async def _handle_tool_call(
        self,
        run_id: str,
        step: int,
        call: Any,
        router: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        call_id = str(getattr(call, "id", "") or "")
        name = _call_name(call)
        arguments, parse_error = _parse_arguments(call)
        self.logger.log(
            run_id,
            "target",
            "tool_call",
            {
                "step": step,
                "tool": name,
                "tool_call_id": call_id,
                "arguments": arguments,
                "parse_error": parse_error,
            },
        )
        if parse_error is not None:
            content = f"ERROR: malformed tool arguments: {parse_error}"
            record = {
                "name": name,
                "arguments": None,
                "tool_call_id": call_id,
                "allowed": False,
                "executed": False,
            }
            self._log_tool_result(run_id, step, name, call_id, content, executed=False)
            return record, _tool_result_message(call_id, name, content)
        request = ToolCallRequest(run_id=run_id, step=step, tool=name, arguments=arguments or {})
        decision = self.gateway.check_tool_call(request)
        if not decision.allowed:
            content = f"DENIED: {decision.reason}"
            record = {
                "name": name,
                "arguments": arguments,
                "tool_call_id": call_id,
                "allowed": False,
                "executed": False,
            }
            self._log_tool_result(run_id, step, name, call_id, content, executed=False)
            return record, _tool_result_message(call_id, name, content)
        client = router.get(name)
        if client is None:
            content = f"ERROR: unknown tool {name}"
            executed = False
        else:
            try:
                result = await client.call_tool(name, arguments)
                content = _truncate(_render_tool_result(result))
                executed = True
            except Exception as exc:
                logger.exception("tool %s failed", name)
                content = _truncate(f"ERROR: {exc}")
                executed = False
        record = {
            "name": name,
            "arguments": arguments,
            "tool_call_id": call_id,
            "allowed": True,
            "executed": executed,
        }
        self._log_tool_result(run_id, step, name, call_id, content, executed=executed)
        return record, _tool_result_message(call_id, name, content)

    def _log_tool_result(
        self,
        run_id: str,
        step: int,
        name: str,
        call_id: str,
        content: str,
        executed: bool,
    ) -> None:
        self.logger.log(
            run_id,
            "target",
            "tool_result",
            {
                "step": step,
                "tool": name,
                "tool_call_id": call_id,
                "content": content,
                "executed": executed,
            },
        )


def _tool_name(tool: Any) -> str:
    name = tool["name"] if isinstance(tool, dict) else tool.name
    return str(name)


def _openai_tool(tool: Any) -> dict[str, Any]:
    canonical = tool_def_from_mcp(tool)
    return {
        "type": "function",
        "function": {
            "name": canonical["name"],
            "description": canonical["description"],
            "parameters": canonical["input_schema"],
        },
    }


def _assistant_tool_message(content: Any, calls: Sequence[Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [_serialize_tool_call(call) for call in calls],
    }


def _serialize_tool_call(call: Any) -> dict[str, Any]:
    function = call.function
    raw = function.arguments
    arguments = raw if isinstance(raw, str) else json.dumps(raw)
    return {
        "id": str(getattr(call, "id", "") or ""),
        "type": "function",
        "function": {"name": str(function.name), "arguments": arguments},
    }


def _call_name(call: Any) -> str:
    function = getattr(call, "function", None)
    name = getattr(function, "name", None)
    return "" if name is None else str(name)


def _parse_arguments(call: Any) -> tuple[dict[str, Any] | None, str | None]:
    function = getattr(call, "function", None)
    raw = getattr(function, "arguments", None)
    if raw is None:
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    if not isinstance(raw, str):
        return None, f"expected a JSON object string, got {type(raw).__name__}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(parsed, dict):
        return None, "tool arguments must be a JSON object"
    return parsed, None


def _tool_result_message(call_id: str, name: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def _render_tool_result(result: Any) -> str:
    data = getattr(result, "data", None)
    if isinstance(data, str):
        rendered = data
    elif data is not None:
        rendered = json.dumps(data, default=str)
    else:
        parts: list[str] = []
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
        rendered = "\n".join(parts)
    if getattr(result, "is_error", False):
        return f"ERROR: {rendered}"
    return rendered


def _truncate(text: str) -> str:
    if len(text) <= _MAX_TOOL_RESULT_CHARS:
        return text
    return text[:_MAX_TOOL_RESULT_CHARS]


def _usage_tokens(response: Any) -> int:
    stats = getattr(response, "call_stats", None)
    if stats is not None and getattr(stats, "total_tokens", None) is not None:
        return int(stats.total_tokens)
    usage = getattr(response, "usage", None)
    total = getattr(usage, "total_tokens", None)
    if total is None:
        return 0
    return int(total)
