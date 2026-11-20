"""FastMCP server of simulated benign tools.

``read_file``, ``send_email``, ``lookup_employee``, and ``fetch_url`` return
canned strings and write a log line. They do not read the filesystem, send
mail, or open a network connection.

``make_benign_server`` builds a fresh server for one run. ``mcp`` is one such
server, kept so existing imports keep working. Pass either to
``fastmcp.Client`` for the in-memory transport. Running this module as a
script starts the stdio transport instead.
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def _record(tool: str, arguments: dict[str, str]) -> None:
    logger.info("tool_call name=%s arguments=%s", tool, arguments)


def make_benign_server() -> FastMCP:
    """Build a fresh in-memory server of the four benign office tools.

    Each call returns a new server so one run cannot share state with another.
    """
    server = FastMCP("benign")

    @server.tool
    def read_file(path: str) -> str:
        """Return simulated contents of a file. Does not touch the filesystem."""
        _record("read_file", {"path": path})
        return f"[simulated] read_file path={path}"

    @server.tool
    def send_email(to: str, subject: str, body: str) -> str:
        """Pretend to send an email. No message leaves this process."""
        _record("send_email", {"to": to, "subject": subject, "body": body})
        return f"[simulated] send_email to={to} subject={subject}"

    @server.tool
    def lookup_employee(name: str) -> str:
        """Return a fake employee directory record."""
        _record("lookup_employee", {"name": name})
        return (
            f"[simulated] lookup_employee name={name} title=Analyst email=analyst@example.invalid"
        )

    @server.tool
    def fetch_url(url: str) -> str:
        """Return a fake HTTP response body. Does not perform a request."""
        _record("fetch_url", {"url": url})
        return f"[simulated] fetch_url url={url} status=200"

    return server


mcp = make_benign_server()


if __name__ == "__main__":
    mcp.run()
