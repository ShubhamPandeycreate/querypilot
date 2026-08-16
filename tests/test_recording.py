"""Record/replay round-trip: a recorded session must drive the loop identically."""

from pathlib import Path

import pytest

from dbagent.agent.loop import AgentLoop
from dbagent.agent.tools import ToolBelt
from dbagent.db.database import Database
from dbagent.llm.recording import RecordingClient, ReplayClient
from dbagent.tracing.tracer import Tracer
from test_loop import FakeClient, tool_reply


@pytest.fixture
def belt(chinook: Database, tmp_path: Path) -> ToolBelt:
    return ToolBelt(chinook, charts_dir=tmp_path)


def scripted_replies() -> list:
    return [
        tool_reply(("run_sql", {"sql": "SELECT count(*) AS n FROM Artist"})),
        tool_reply(
            ("final_answer", {"answer_md": "275 artists", "sql": "SELECT count(*) FROM Artist"})
        ),
    ]


def test_record_then_replay_produces_same_run(
    belt: ToolBelt, chinook: Database, tmp_path: Path
) -> None:
    golden = tmp_path / "golden.json"

    recorder = RecordingClient(FakeClient(scripted_replies()), golden)
    first = AgentLoop(recorder, belt, Tracer(None)).run("How many artists?")
    recorder.save()
    assert first.stop_reason == "final_answer"

    replay = ReplayClient(golden)
    fresh_belt = ToolBelt(chinook, charts_dir=tmp_path)
    second = AgentLoop(replay, fresh_belt, Tracer(None)).run("How many artists?")

    assert second.answer_md == first.answer_md
    assert second.sql == first.sql
    assert second.llm_calls == first.llm_calls
    assert replay.exhausted


def test_replay_exhaustion_raises(belt: ToolBelt, tmp_path: Path) -> None:
    golden = tmp_path / "golden.json"
    recorder = RecordingClient(FakeClient(scripted_replies()), golden)
    AgentLoop(recorder, belt, Tracer(None)).run("q")
    recorder.save()

    replay = ReplayClient(golden)
    replay.chat([])
    replay.chat([])
    with pytest.raises(RuntimeError, match="Replay exhausted"):
        replay.chat([])
