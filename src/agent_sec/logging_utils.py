"""Append-only JSONL logger for experiment runs.

Each call writes one record with ``run_id``, ``role``, ``event_type``,
``payload``, and a UTC ``timestamp``. The file is only ever opened for append.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonlRunLogger:
    """Write one JSON object per line to a run log."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def log(self, run_id: str, role: str, event_type: str, payload: dict[str, Any]) -> None:
        """Append a single event. ``payload`` must be JSON-serializable."""
        record = {
            "run_id": run_id,
            "role": role,
            "event_type": event_type,
            "payload": payload,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
