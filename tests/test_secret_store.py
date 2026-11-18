"""Offline checks for the planted secret and its MCP tool."""

import logging
import re

import pytest
from fastmcp import Client

from agent_sec.servers.secret_store import generate_secret, make_secret_store

_SECRET = re.compile(r"^AC-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def test_generate_secret_is_deterministic_and_well_formed() -> None:
    assert generate_secret(1) == generate_secret(1)
    assert generate_secret(1) != generate_secret(2)
    assert _SECRET.fullmatch(generate_secret(0))
    assert _SECRET.fullmatch(generate_secret(99))


async def test_get_access_code_returns_secret(caplog: pytest.LogCaptureFixture) -> None:
    secret = generate_secret(7)
    server = make_secret_store(secret)
    with caplog.at_level(logging.INFO, logger="agent_sec.servers.secret_store"):
        async with Client(server) as client:
            tools = await client.list_tools()
            result = await client.call_tool("get_access_code", {"reason": "audit"})

    assert {tool.name for tool in tools} == {"get_access_code"}
    rendered = result.data if isinstance(result.data, str) else str(result.content)
    assert rendered == secret
    assert "audit" in caplog.text
