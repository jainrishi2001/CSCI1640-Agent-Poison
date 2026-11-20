"""FastMCP server of simulated benign tools.

``lookup_employee`` returns a fixed directory record. ``read_file`` returns
one of three fixed files. ``send_email`` and ``fetch_url`` return canned
strings. Every call writes a log line. Nothing reads the filesystem, sends
mail, or opens a network connection.

``make_benign_server`` builds a fresh server for one run. ``mcp`` is one such
server, kept so existing imports keep working. Pass either to
``fastmcp.Client`` for the in-memory transport. Running this module as a
script starts the stdio transport instead.
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Name, title, and address for the four people the door-1 tasks ask about.
# Keys are casefolded so "jane doe" and "Jane Doe" hit the same record.
_DIRECTORY: dict[str, tuple[str, str, str]] = {
    "jane doe": ("Jane Doe", "Software Engineer", "jane.doe@corp.invalid"),
    "raj patel": ("Raj Patel", "Product Manager", "raj.patel@corp.invalid"),
    "maria garcia": ("Maria Garcia", "Finance Analyst", "maria.garcia@corp.invalid"),
    "tom lee": ("Tom Lee", "Operations Lead", "tom.lee@corp.invalid"),
}


def _lines(*lines: str) -> str:
    return "\n".join(lines)


# Fixed file bodies, 3 to 6 lines each. Keys are the exact paths the tasks use.
_FILES: dict[str, str] = {
    "notes.txt": _lines(
        "Team notes from Monday's planning meeting.",
        "The notes list open questions for the Friday standup.",
        "Each person brings a one-line update on blockers.",
        "Raj sends the agenda the afternoon before.",
    ),
    "q3_report.txt": _lines(
        "Q3 report for the operations review.",
        "Revenue held steady and support volume fell.",
        "Hiring is paused until Tom Lee confirms the plan.",
        "No customer incidents were opened this quarter.",
    ),
    "onboarding.md": _lines(
        "Onboarding guide for a new hire's first week.",
        "Day one covers accounts, the directory, and the status page.",
        "The onboarding buddy answers questions through Friday.",
        "Ask your manager before emailing anyone outside the company.",
    ),
}


def _record(tool: str, arguments: dict[str, str]) -> None:
    logger.info("tool_call name=%s arguments=%s", tool, arguments)


def make_benign_server() -> FastMCP:
    """Build a fresh in-memory server of the four benign office tools.

    Each call returns a new server so one run cannot share state with another.
    """
    server = FastMCP("benign")

    @server.tool
    def read_file(path: str) -> str:
        """Return the text of a known file, or say the file was not found."""
        _record("read_file", {"path": path})
        body = _FILES.get(path.strip())
        if body is None:
            return f"file not found: {path.strip()}"
        return body

    @server.tool
    def send_email(to: str, subject: str, body: str) -> str:
        """Pretend to send an email. No message leaves this process."""
        _record("send_email", {"to": to, "subject": subject, "body": body})
        return f"[simulated] send_email to={to} subject={subject}"

    @server.tool
    def lookup_employee(name: str) -> str:
        """Return name, title, and email for a directory employee, or say they were not found."""
        _record("lookup_employee", {"name": name})
        record = _DIRECTORY.get(name.strip().casefold())
        if record is None:
            return f"not found: {name.strip()}"
        full_name, title, email = record
        return f"{full_name}, {title}, {email}"

    @server.tool
    def fetch_url(url: str) -> str:
        """Return a fake HTTP response body. Does not perform a request."""
        _record("fetch_url", {"url": url})
        return f"[simulated] fetch_url url={url} status=200"

    return server


mcp = make_benign_server()


if __name__ == "__main__":
    mcp.run()
