"""Target agent loop with a scripted model. No network."""

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastmcp import FastMCP

from agent_sec.agent.target import TargetAgent
from agent_sec.gateway.gateway import Decision, PolicyGateway
from agent_sec.logging_utils import JsonlRunLogger


class FakeLLM:
    """Return scripted responses and record each call."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, messages, tools=None, model=None, temperature=None, **_kwargs):
        self.calls.append(
            {"messages": messages, "tools": tools, "model": model, "temperature": temperature}
        )
        if not self.responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        return self.responses.pop(0)


class DenyDefense:
    """Test defense. Denies named tools and, optionally, every outgoing message."""

    def __init__(
        self, name: str, deny_tools: set[str] | None = None, deny_outgoing: bool = False
    ) -> None:
        self.name = name
        self.deny_tools = deny_tools or set()
        self.deny_outgoing = deny_outgoing

    def on_tools_listed(self, tools: list) -> list:
        return tools

    def check_tool_call(self, req) -> Decision:
        if req.tool in self.deny_tools:
            return Decision(False, f"{req.tool} is not allowed", self.name)
        return Decision(True, "allowed", self.name)

    def check_outgoing_message(self, msg) -> Decision:
        if self.deny_outgoing:
            return Decision(False, "outgoing text is blocked", self.name)
        return Decision(True, "allowed", self.name)


def _tool_response(
    name: str, arguments: dict | None, call_id: str, *, raw: str | None = None, tokens: int = 10
):
    payload = raw if raw is not None else json.dumps(arguments or {})
    function = SimpleNamespace(name=name, arguments=payload)
    call = SimpleNamespace(id=call_id, type="function", function=function)
    message = SimpleNamespace(role="assistant", content=None, tool_calls=[call])
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=tokens)
    return SimpleNamespace(choices=[choice], usage=usage, call_stats=None)


def _text_response(text: str, tokens: int = 4):
    message = SimpleNamespace(role="assistant", content=text, tool_calls=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=tokens)
    return SimpleNamespace(choices=[choice], usage=usage, call_stats=None)


def _server(executed: list[str]) -> FastMCP:
    mcp = FastMCP("office")

    @mcp.tool
    def lookup_employee(name: str) -> str:
        """Return a simulated employee record."""
        executed.append(f"lookup:{name}")
        return f"record for {name}"

    @mcp.tool
    def send_email(to: str, subject: str, body: str) -> str:
        """Pretend to send mail."""
        executed.append(f"email:{to}:{subject}:{body}")
        return "queued"

    return mcp


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _agent(
    tmp_path: Path, server: FastMCP, llm: FakeLLM, defenses: list | None = None, max_steps: int = 6
):
    log_path = tmp_path / "run.jsonl"
    gateway = PolicyGateway(defenses or [], JsonlRunLogger(log_path))
    agent = TargetAgent(
        servers=[server],
        gateway=gateway,
        model="fake-model",
        system_prompt="Use the tools.",
        max_steps=max_steps,
        logger=JsonlRunLogger(log_path),
        llm_fn=llm,
        temperature=0,
    )
    return agent, log_path


async def test_tools_run_in_order_and_final_text_is_returned(tmp_path: Path) -> None:
    executed: list[str] = []
    llm = FakeLLM(
        [
            _tool_response("lookup_employee", {"name": "Jane Doe"}, "c1"),
            _tool_response(
                "send_email",
                {"to": "jane@example.invalid", "subject": "Welcome", "body": "Welcome aboard."},
                "c2",
            ),
            _text_response("Sent Jane Doe a welcome email."),
        ]
    )
    agent, log_path = _agent(tmp_path, _server(executed), llm)
    result = await agent.respond(
        [{"role": "user", "content": "Look up Jane Doe and email her."}],
        "run-1",
    )

    assert executed == [
        "lookup:Jane Doe",
        "email:jane@example.invalid:Welcome:Welcome aboard.",
    ]
    assert [call["name"] for call in result.tool_calls] == ["lookup_employee", "send_email"]
    assert result.final_text == "Sent Jane Doe a welcome email."
    assert result.hit_max_steps is False
    assert result.steps == 3
    assert result.total_tokens == 24
    names = [
        event["payload"]["tool"]
        for event in _events(log_path)
        if event["role"] == "target" and event["event_type"] == "tool_call"
    ]
    assert names == ["lookup_employee", "send_email"]
    final = [event for event in _events(log_path) if event["event_type"] == "final_message"]
    assert final[-1]["payload"]["text"] == "Sent Jane Doe a welcome email."


async def test_denied_tool_is_not_executed(tmp_path: Path) -> None:
    executed: list[str] = []
    llm = FakeLLM(
        [
            _tool_response(
                "send_email",
                {"to": "jane@example.invalid", "subject": "Hi", "body": "Hi"},
                "c1",
            ),
            _text_response("I could not send the email."),
        ]
    )
    defense = DenyDefense("block-mail", deny_tools={"send_email"})
    agent, _log_path = _agent(tmp_path, _server(executed), llm, defenses=[defense])
    result = await agent.respond([{"role": "user", "content": "Email Jane."}], "run-2")

    assert executed == []
    assert result.tool_calls[0]["executed"] is False
    tool_messages = [
        message for message in llm.calls[1]["messages"] if message.get("role") == "tool"
    ]
    assert tool_messages
    assert tool_messages[0]["content"].startswith("DENIED:")
    assert "send_email is not allowed" in tool_messages[0]["content"]


async def test_max_steps_stops_the_loop(tmp_path: Path) -> None:
    executed: list[str] = []
    llm = FakeLLM(
        [_tool_response("lookup_employee", {"name": "Jane Doe"}, f"c{i}") for i in range(5)]
    )
    agent, _log_path = _agent(tmp_path, _server(executed), llm, max_steps=2)
    result = await agent.respond([{"role": "user", "content": "Keep looking."}], "run-3")

    assert result.hit_max_steps is True
    assert result.steps == 2
    assert len(llm.calls) == 2
    assert len(executed) == 2


async def test_denied_outgoing_message_is_replaced(tmp_path: Path) -> None:
    executed: list[str] = []
    llm = FakeLLM([_text_response("the secret is out")])
    defense = DenyDefense("block-text", deny_outgoing=True)
    agent, log_path = _agent(tmp_path, _server(executed), llm, defenses=[defense])
    result = await agent.respond([{"role": "user", "content": "Say something."}], "run-4")

    assert result.final_text == "[message blocked by policy]"
    assert executed == []
    decisions = [event for event in _events(log_path) if event["event_type"] == "decision"]
    assert decisions[-1]["payload"]["allowed"] is False
    assert decisions[-1]["payload"]["defense"] == "block-text"


async def test_malformed_tool_arguments_are_returned_to_the_model(tmp_path: Path) -> None:
    executed: list[str] = []
    llm = FakeLLM(
        [
            _tool_response("send_email", None, "c1", raw="{not json"),
            _text_response("I will not retry that call."),
        ]
    )
    agent, _log_path = _agent(tmp_path, _server(executed), llm)
    result = await agent.respond([{"role": "user", "content": "Email Jane."}], "run-5")

    assert executed == []
    assert result.final_text == "I will not retry that call."
    tool_messages = [
        message for message in llm.calls[1]["messages"] if message.get("role") == "tool"
    ]
    assert "malformed tool arguments" in tool_messages[0]["content"]


class SleepingLLM:
    """Block inside the model call so a concurrency test can see overlapping turns."""

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.intervals: list[tuple[float, float]] = []
        self._lock = threading.Lock()

    def __call__(self, messages, tools=None, model=None, temperature=None, **_kwargs):
        started = time.perf_counter()
        time.sleep(self.delay_s)
        finished = time.perf_counter()
        with self._lock:
            self.intervals.append((started, finished))
        return _text_response("done")


async def test_two_agents_respond_calls_overlap(tmp_path: Path) -> None:
    llm = SleepingLLM(0.3)
    agents = []
    for name in ("a", "b"):
        agent, _log_path = _agent(tmp_path / name, _server([]), llm)
        agents.append(agent)

    results = await asyncio.gather(
        agents[0].respond([{"role": "user", "content": "Say A."}], "run-a"),
        agents[1].respond([{"role": "user", "content": "Say B."}], "run-b"),
    )

    assert [result.final_text for result in results] == ["done", "done"]
    assert len(llm.intervals) == 2
    (start_a, end_a), (start_b, end_b) = llm.intervals
    assert start_a < end_b and start_b < end_a
    for name in ("a", "b"):
        modes = [
            event["payload"]["value"]
            for event in _events(tmp_path / name / "run.jsonl")
            if event["event_type"] == "system_role_mode"
        ]
        assert modes == ["system"]
