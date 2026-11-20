"""In-memory smoke test for the benign FastMCP server.

Uses ``fastmcp.Client`` on the server object, which selects the in-memory
transport. No subprocess and no network.
"""

import pytest
from fastmcp import Client

from agent_sec.servers.benign import make_benign_server, mcp


async def test_list_tools_and_read_file() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        assert {"read_file", "send_email", "lookup_employee", "fetch_url"} <= names

        result = await client.call_tool("read_file", {"path": "notes.txt"})

    assert result.is_error is False
    rendered = result.data if isinstance(result.data, str) else str(result.content)
    assert "notes.txt" in rendered
    assert "simulated" in rendered


def _text(result: object) -> str:
    data = getattr(result, "data", None)
    if isinstance(data, str):
        return data
    return str(getattr(result, "content", result))


@pytest.mark.parametrize(
    ("name", "email"),
    [
        ("Jane Doe", "jane.doe@corp.invalid"),
        ("Raj Patel", "raj.patel@corp.invalid"),
        ("Maria Garcia", "maria.garcia@corp.invalid"),
        ("Tom Lee", "tom.lee@corp.invalid"),
    ],
)
async def test_lookup_employee_returns_the_directory_address(name: str, email: str) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_employee", {"name": name})
    rendered = _text(result)
    assert result.is_error is False
    assert name in rendered
    assert email in rendered
    assert "not found" not in rendered.casefold()


async def test_lookup_employee_matches_name_regardless_of_case() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_employee", {"name": "  jane DOE "})
    assert "jane.doe@corp.invalid" in _text(result)


async def test_lookup_employee_unknown_name_is_not_found() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("lookup_employee", {"name": "Alex Kim"})
    rendered = _text(result)
    assert "not found" in rendered.casefold()
    assert "@corp.invalid" not in rendered


async def test_each_factory_call_returns_its_own_server() -> None:
    first = make_benign_server()
    second = make_benign_server()
    assert first is not second
    assert first is not mcp

    async with Client(first) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == {"read_file", "send_email", "lookup_employee", "fetch_url"}
