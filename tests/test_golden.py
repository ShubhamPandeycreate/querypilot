"""Replay recorded golden traces through the real loop + toolbelt + guard.

Each fixture in tests/fixtures/golden_*.json is a full recorded agent session.
Replaying costs no network and no API keys, but exercises everything except
the LLM itself — the CI regression net for agent behavior.
"""

import json
from pathlib import Path

import pytest

from dbagent.agent.loop import AgentLoop
from dbagent.agent.tools import ToolBelt
from dbagent.db.database import Database
from dbagent.llm.recording import ReplayClient
from dbagent.tracing.tracer import Tracer

FIXTURES = sorted((Path(__file__).resolve().parent / "fixtures").glob("golden_*.json"))


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.stem)
def test_golden_replay(fixture: Path, chinook: Database, tmp_path: Path) -> None:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    meta = payload["meta"]
    replay = ReplayClient(fixture)
    loop = AgentLoop(replay, ToolBelt(chinook, charts_dir=tmp_path), Tracer(None))

    result = loop.run(meta["question"])

    assert result.stop_reason in ("final_answer", "answered_in_text")
    for needle in meta["expect_contains"]:
        assert needle.lower() in result.answer_md.lower(), (
            f"golden answer lost {needle!r}: {result.answer_md[:200]}"
        )
    assert replay.exhausted, "loop made fewer LLM calls than the recording"


def test_fixture_directory_note() -> None:
    # Golden fixtures are recorded with scripts/record_golden.py. Zero fixtures
    # is legal (fresh clone before any recording) — the parametrized test above
    # simply collects nothing in that case.
    assert True
