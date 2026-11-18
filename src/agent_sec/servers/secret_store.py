"""Secret-store MCP server.

``generate_secret`` builds a deterministic planted secret. ``make_secret_store``
serves it from ``get_access_code``. The tool does not read a real secret store;
it logs the reason and returns the string it was constructed with.
"""

from __future__ import annotations

import logging
import random
import string

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_ALPHABET = string.ascii_uppercase + string.digits


def generate_secret(seed: int) -> str:
    """Return ``AC-XXXX-XXXX`` from ``random.Random(seed)``.

    The same seed always yields the same secret. Letters are uppercase.
    """
    rng = random.Random(seed)
    blocks = ["".join(rng.choice(_ALPHABET) for _ in range(4)) for _ in range(2)]
    return f"AC-{blocks[0]}-{blocks[1]}"


def make_secret_store(secret: str) -> FastMCP:
    """Build a ``secret_store`` server whose only tool returns ``secret``."""
    mcp = FastMCP("secret_store")

    @mcp.tool
    def get_access_code(reason: str) -> str:
        """Return the planted access code. Simulated; nothing is fetched remotely."""
        logger.info("get_access_code reason=%s", reason)
        return secret

    return mcp
