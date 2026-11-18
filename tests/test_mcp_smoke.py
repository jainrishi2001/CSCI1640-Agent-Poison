"""In-memory smoke test for the benign FastMCP server.

Uses ``fastmcp.Client`` on the server object, which selects the in-memory
transport. No subprocess and no network.
"""

from fastmcp import Client

from agent_sec.servers.benign import mcp


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
