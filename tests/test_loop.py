"""Agent-loop control-flow tests driven by a scripted fake client. No network."""

import json
from pathlib import Path
from typing import Any

import pytest

from dbagent.agent.loop import AgentLoop, AgentResult
from dbagent.agent.prompts import TOO_MANY_FAILURES_NUDGE, USE_FINAL_ANSWER_NUDGE
from dbagent.agent.tools import ToolBelt
from dbagent.db.database import Database
from dbagent.llm.client import LLMReply, ToolCall
from dbagent.tracing.tracer import Tracer


class FakeClient:
    provider_name = "fake"
    model = "fake-1"

    def __init__(self, replies: list[LLMReply]) -> None:
        self.replies = list(replies)
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.seen_max_tokens: list[int] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMReply:
        self.seen_messages.append([dict(m) for m in messages])
        self.seen_max_tokens.append(max_tokens)
        if not self.replies:
            raise AssertionError("FakeClient ran out of scripted replies")
        return self.replies.pop(0)


def tool_reply(*calls: tuple[str, dict[str, Any]]) -> LLMReply:
    tool_calls = [
        ToolCall(id=f"call_{i}", name=name, arguments=json.dumps(args))
        for i, (name, args) in enumerate(calls)
    ]
    return LLMReply(
        content=None,
        tool_calls=tool_calls,
        raw_message={
            "role": "assistant",
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in tool_calls
            ],
        },
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )


def text_reply(text: str) -> LLMReply:
    return LLMReply(
        content=text,
        tool_calls=[],
        raw_message={"role": "assistant", "content": text},
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )


def raw_json_tool_reply(name: str, raw_arguments: str) -> LLMReply:
    call = ToolCall(id="call_bad", name=name, arguments=raw_arguments)
    return LLMReply(
        content=None,
        tool_calls=[call],
        raw_message={"role": "assistant", "tool_calls": []},
    )


@pytest.fixture
def belt(chinook: Database, tmp_path: Path) -> ToolBelt:
    return ToolBelt(chinook, charts_dir=tmp_path)


def run_loop(
    replies: list[LLMReply], belt: ToolBelt, **kwargs: Any
) -> tuple[AgentResult, FakeClient, Tracer]:
    client = FakeClient(replies)
    tracer = Tracer(None)
    result = AgentLoop(client, belt, tracer, **kwargs).run("test question")
    return result, client, tracer


def test_happy_path(belt: ToolBelt) -> None:
    result, client, tracer = run_loop(
        [
            tool_reply(("list_tables", {})),
            tool_reply(("run_sql", {"sql": "SELECT count(*) AS n FROM Artist"})),
            tool_reply(
                (
                    "final_answer",
                    {"answer_md": "**275 artists**", "sql": "SELECT ...", "caveats": ""},
                )
            ),
        ],
        belt,
    )
    assert result.stop_reason == "final_answer"
    assert result.answer_md == "**275 artists**"
    assert result.llm_calls == 3
    assert result.usage == {"prompt_tokens": 30, "completion_tokens": 15}
    kinds = [e["kind"] for e in tracer.events]
    assert kinds == [
        "question",
        "llm_call",
        "tool",
        "llm_call",
        "tool",
        "llm_call",
        "tool",
        "final",
    ]


def test_sql_error_feeds_hint_back(belt: ToolBelt) -> None:
    result, client, _ = run_loop(
        [
            tool_reply(("run_sql", {"sql": "SELECT WrongCol FROM Album"})),
            tool_reply(("run_sql", {"sql": "SELECT Title FROM Album LIMIT 1"})),
            tool_reply(("final_answer", {"answer_md": "fixed"})),
        ],
        belt,
    )
    assert result.stop_reason == "final_answer"
    # The transcript the model saw on call 2 must contain the structured error hint.
    second_call_transcript = json.dumps(client.seen_messages[1])
    assert "no such column" in second_call_transcript
    assert "get_schema" in second_call_transcript  # the hint


def test_three_sql_failures_triggers_nudge(belt: ToolBelt) -> None:
    bad = ("run_sql", {"sql": "SELECT Nope FROM Album"})
    result, client, tracer = run_loop(
        [
            tool_reply(bad),
            tool_reply(bad),
            tool_reply(bad),
            tool_reply(("final_answer", {"answer_md": "could not do it", "caveats": "3 failures"})),
        ],
        belt,
    )
    assert result.stop_reason == "final_answer"
    final_transcript = client.seen_messages[-1]
    assert any(
        m.get("role") == "user" and m.get("content") == TOO_MANY_FAILURES_NUDGE
        for m in final_transcript
    )
    assert any(
        e["kind"] == "nudge" and e["reason"] == "too_many_sql_failures" for e in tracer.events
    )


def test_text_answer_gets_one_nudge_then_accepted(belt: ToolBelt) -> None:
    result, client, _ = run_loop(
        [text_reply("The answer is 42."), text_reply("The answer is 42.")],
        belt,
    )
    assert result.stop_reason == "answered_in_text"
    assert result.answer_md == "The answer is 42."
    assert any(m.get("content") == USE_FINAL_ANSWER_NUDGE for m in client.seen_messages[-1])


def test_text_answer_backfills_sql_from_last_query(belt: ToolBelt) -> None:
    result, _, _ = run_loop(
        [
            tool_reply(("run_sql", {"sql": "SELECT count(*) AS n FROM Artist"})),
            text_reply("There are 275 artists."),
            text_reply("There are 275 artists."),
        ],
        belt,
    )
    assert result.stop_reason == "answered_in_text"
    assert "COUNT(*)" in result.sql.upper()


def test_max_llm_calls_stops_loop(belt: ToolBelt) -> None:
    result, _, _ = run_loop(
        [tool_reply(("list_tables", {})) for _ in range(5)],
        belt,
        max_llm_calls=5,
    )
    assert result.stop_reason == "max_llm_calls"
    assert result.llm_calls == 5


def test_bad_json_arguments_reported_not_fatal(belt: ToolBelt) -> None:
    result, client, _ = run_loop(
        [
            raw_json_tool_reply("run_sql", "{not json"),
            tool_reply(("final_answer", {"answer_md": "recovered"})),
        ],
        belt,
    )
    assert result.stop_reason == "final_answer"
    assert "bad_json" in json.dumps(client.seen_messages[1])


def test_final_answer_sql_backfilled_from_last_query(belt: ToolBelt) -> None:
    result, _, _ = run_loop(
        [
            tool_reply(("run_sql", {"sql": "SELECT count(*) AS n FROM Artist"})),
            tool_reply(("final_answer", {"answer_md": "275 artists"})),  # sql omitted
        ],
        belt,
    )
    assert result.stop_reason == "final_answer"
    assert "COUNT(*)" in result.sql.upper()  # backfilled from the executed query


def test_chart_paths_collected(belt: ToolBelt) -> None:
    result, _, _ = run_loop(
        [
            tool_reply(("run_sql", {"sql": "SELECT Name, GenreId FROM Genre LIMIT 3"})),
            tool_reply(("render_chart", {"kind": "bar", "x": "Name", "y": "GenreId"})),
            tool_reply(("final_answer", {"answer_md": "charted"})),
        ],
        belt,
    )
    assert len(result.chart_paths) == 1
    assert Path(result.chart_paths[0]).exists()


def test_tracer_writes_jsonl_file(belt: ToolBelt, tmp_path: Path) -> None:
    trace_path = tmp_path / "t.jsonl"
    client = FakeClient([tool_reply(("final_answer", {"answer_md": "done"}))])
    AgentLoop(client, belt, Tracer(trace_path)).run("q")
    lines = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert [e["kind"] for e in lines] == ["question", "llm_call", "tool", "final"]
    assert lines[0]["provider"] == "fake"


def test_chart_summary_hides_the_absolute_path(belt: ToolBelt) -> None:
    """Traces are downloadable from the public demo, so they must not carry
    the server's directory layout (or, running locally, a username)."""
    from dbagent.agent.loop import _summarize

    summary = _summarize("render_chart", {"chart_path": r"C:\Users\someone\AppData\chart_1.png"})
    assert summary == "chart saved: chart_1.png"
    assert "Users" not in summary


def empty_reply() -> LLMReply:
    """What a truncated reply looks like: no content, no tool calls."""
    return LLMReply(
        content=None,
        tool_calls=[],
        raw_message={"role": "assistant", "content": None},
        usage={"prompt_tokens": 900, "completion_tokens": 3584},
    )


def test_empty_reply_is_retried_with_a_larger_budget(belt: ToolBelt) -> None:
    """The cause is truncation, so the useful intervention is more room."""
    client = FakeClient(
        [
            empty_reply(),
            tool_reply(("final_answer", {"answer_md": "**275** artists.", "sql": "SELECT 1"})),
        ]
    )
    result = AgentLoop(client, belt, Tracer(None)).run("how many artists?")

    assert result.stop_reason == "final_answer"
    assert client.seen_max_tokens[0] < client.seen_max_tokens[1]
    assert client.seen_max_tokens[1] == 5120


def test_two_empty_replies_stop_the_episode(belt: ToolBelt) -> None:
    """Without this the loop nudge-treadmills to the 12-call cap."""
    client = FakeClient([empty_reply(), empty_reply()])
    events: list[dict[str, Any]] = []
    result = AgentLoop(client, belt, Tracer(None), on_step=events.append).run("anything")

    assert result.stop_reason == "empty_replies"
    assert result.llm_calls == 2  # not 12
    assert "budget" in result.answer_md
    assert [e["reason"] for e in events if e["kind"] == "retry"] == ["empty_reply", "empty_reply"]


def test_empty_reply_counter_resets_after_a_good_reply(belt: ToolBelt) -> None:
    """Alternating empty and useful replies is not a stuck episode."""
    client = FakeClient(
        [
            empty_reply(),
            tool_reply(("run_sql", {"sql": "SELECT count(*) FROM Artist"})),
            empty_reply(),
            tool_reply(("final_answer", {"answer_md": "**275**", "sql": "SELECT 1"})),
        ]
    )
    result = AgentLoop(client, belt, Tracer(None)).run("how many artists?")

    assert result.stop_reason == "final_answer"
    assert result.llm_calls == 4


def test_prose_reply_is_still_nudged_not_treated_as_empty(belt: ToolBelt) -> None:
    """An empty reply and a chatty one need opposite responses."""
    client = FakeClient([text_reply("There are 275 artists."), text_reply("There are 275.")])
    events: list[dict[str, Any]] = []
    result = AgentLoop(client, belt, Tracer(None), on_step=events.append).run("how many?")

    assert result.stop_reason == "answered_in_text"
    assert not [e for e in events if e["kind"] == "retry"]
    assert [e["reason"] for e in events if e["kind"] == "nudge"] == ["no_tool_call"]
