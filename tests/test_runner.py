"""Eval runner tests: both modes end-to-end on Chinook with a fake client."""

import json
import time
from pathlib import Path

from evals.datasets import fixed_subset, load_chinook_smoke
from evals.runner import (
    RateLimiter,
    extract_sql,
    gold_is_order_sensitive,
    run_eval,
)
from test_loop import FakeClient, text_reply, tool_reply

ARTIST_COUNT_SQL = "SELECT count(*) FROM Artist"


def chinook_item():
    return next(i for i in load_chinook_smoke() if i.id == "chinook_001")


def test_extract_sql_variants() -> None:
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("```\nSELECT 2\n```") == "SELECT 2"
    assert extract_sql("SELECT 3;") == "SELECT 3"


def test_gold_order_sensitivity() -> None:
    assert gold_is_order_sensitive("SELECT a FROM t ORDER BY a")
    assert not gold_is_order_sensitive("SELECT a FROM t")


def test_single_shot_mode_scores_match(tmp_path: Path) -> None:
    client = FakeClient([text_reply(f"```sql\n{ARTIST_COUNT_SQL}\n```")])
    out = tmp_path / "results.jsonl"
    summary = run_eval([chinook_item()], mode="single_shot", client=client, rpm=6000, out_path=out)
    assert summary["accuracy"] == 100.0
    assert summary["avg_llm_calls"] == 1
    # The prompt must have carried the schema.
    prompt_text = json.dumps(client.seen_messages[0])
    assert "CREATE TABLE" in prompt_text


def test_single_shot_wrong_sql_is_miss(tmp_path: Path) -> None:
    client = FakeClient([text_reply("```sql\nSELECT count(*) FROM Album\n```")])
    summary = run_eval(
        [chinook_item()],
        mode="single_shot",
        client=client,
        rpm=6000,
        out_path=tmp_path / "r.jsonl",
    )
    assert summary["accuracy"] == 0.0
    assert summary["exec_failures"] == 0  # it ran, it was just wrong


def test_agent_mode_scores_match(tmp_path: Path) -> None:
    client = FakeClient(
        [
            tool_reply(("run_sql", {"sql": ARTIST_COUNT_SQL})),
            tool_reply(("final_answer", {"answer_md": "275", "sql": ARTIST_COUNT_SQL})),
        ]
    )
    summary = run_eval(
        [chinook_item()], mode="agent", client=client, rpm=6000, out_path=tmp_path / "r.jsonl"
    )
    assert summary["accuracy"] == 100.0
    assert summary["avg_llm_calls"] == 2


def test_resume_skips_done_items(tmp_path: Path) -> None:
    out = tmp_path / "r.jsonl"
    client = FakeClient([text_reply(f"```sql\n{ARTIST_COUNT_SQL}\n```")])
    run_eval([chinook_item()], mode="single_shot", client=client, rpm=6000, out_path=out)
    # Second run: client has NO replies left; if resume failed it would crash.
    summary = run_eval(
        [chinook_item()], mode="single_shot", client=FakeClient([]), rpm=6000, out_path=out
    )
    assert summary["total"] == 1


def test_rate_limiter_spaces_calls() -> None:
    limiter = RateLimiter(rpm=1200)  # 50ms interval
    start = time.monotonic()
    for _ in range(3):
        limiter.acquire()
    assert time.monotonic() - start >= 0.09  # two 50ms gaps


def test_fixed_subset_deterministic() -> None:
    items = load_chinook_smoke()
    a = [i.id for i in fixed_subset(items, 5)]
    b = [i.id for i in fixed_subset(items, 5)]
    assert a == b
    assert len(a) == 5


def test_summarize_counts(tmp_path: Path) -> None:
    out = tmp_path / "r.jsonl"
    client = FakeClient([text_reply("no sql here at all")])
    summary = run_eval([chinook_item()], mode="single_shot", client=client, rpm=6000, out_path=out)
    assert summary["total"] == 1
    assert summary["accuracy"] == 0.0
    assert summary["exec_failures"] == 1  # extracted "SQL" fails the guard/execution
