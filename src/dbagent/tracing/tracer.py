"""JSONL tracing: every agent step becomes one line the demo can replay.

Event kinds: question, llm_call, tool, nudge, final. Each line carries a
monotonic step index and wall-clock timestamp, so the Streamlit trace viewer
(Phase 4) can render the run exactly as it happened.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Tracer:
    """Appends events to one JSONL file. A `path` of None disables tracing."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._step = 0
        self.events: list[dict[str, Any]] = []  # kept in memory for the UI/tests
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, kind: str, **data: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "step": self._step,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "kind": kind,
            **data,
        }
        self._step += 1
        self.events.append(record)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record


def default_trace_path(base_dir: str | Path = "traces") -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return Path(base_dir) / f"session_{stamp}.jsonl"
