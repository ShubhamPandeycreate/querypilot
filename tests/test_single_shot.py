"""Single-shot mode: one prompt, one query, no second chance.

Also pins the prompt shared with the eval harness — if these two ever drift,
the app stops demonstrating the baseline the report publishes.
"""

from dbagent.agent.single_shot import (
    SINGLE_SHOT_SYSTEM,
    answer_question,
    build_prompt,
    extract_sql,
    schema_text,
)
from dbagent.db.database import Database
from evals.datasets import load_chinook_smoke
from evals.runner import build_single_shot_prompt
from test_loop import FakeClient, text_reply

ARTIST_COUNT_SQL = "SELECT count(*) FROM Artist"


def test_extract_sql_variants() -> None:
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("```\nSELECT 2\n```") == "SELECT 2"
    assert extract_sql("SELECT 3;") == "SELECT 3"
    assert extract_sql("") == ""


def test_prompt_carries_schema_foreign_keys_and_hint() -> None:
    messages = build_prompt(
        ddl="CREATE TABLE t (a INT)", foreign_keys="t.a -> u(b)", question="how many?", evidence="a"
    )
    assert messages[0] == {"role": "system", "content": SINGLE_SHOT_SYSTEM}
    body = messages[1]["content"]
    assert "Database schema:\nCREATE TABLE t (a INT)" in body
    assert "Foreign keys:\nt.a -> u(b)" in body
    assert "Hint: a" in body
    assert body.endswith("Question: how many?")


def test_prompt_omits_empty_sections() -> None:
    body = build_prompt(ddl="DDL", foreign_keys="", question="q")[1]["content"]
    assert "Foreign keys" not in body
    assert "Hint" not in body


def test_eval_harness_and_app_share_one_prompt() -> None:
    """The eval harness must build byte-identical prompts to the app's."""
    item = next(i for i in load_chinook_smoke() if i.id == "chinook_001")
    db = Database(item.db_path)
    try:
        ddl, fks = schema_text(db)
    finally:
        db.close()
    assert build_single_shot_prompt(item) == build_prompt(
        ddl=ddl, foreign_keys=fks, question=item.question, evidence=item.evidence
    )


def test_answer_question_runs_the_generated_sql(chinook: Database) -> None:
    client = FakeClient([text_reply(f"```sql\n{ARTIST_COUNT_SQL}\n```")])
    result = answer_question(client, chinook, "How many artists are there?")
    assert result.error == ""
    assert result.result is not None
    assert result.result.rows == [(275,)]
    assert result.answer_md == "**275**"
    assert result.llm_calls == 1
    assert "no schema exploration" in result.caveats


def test_answer_question_sends_the_whole_schema(chinook: Database) -> None:
    client = FakeClient([text_reply(f"```sql\n{ARTIST_COUNT_SQL}\n```")])
    answer_question(client, chinook, "How many artists are there?")
    prompt = client.seen_messages[0][1]["content"]
    assert prompt.count("CREATE TABLE") == len(chinook.table_names())


def test_failed_sql_is_reported_not_retried(chinook: Database) -> None:
    client = FakeClient([text_reply("```sql\nSELECT nope FROM Artist\n```")])
    result = answer_question(client, chinook, "anything")
    assert "no such column" in result.error
    assert "no second attempt" in result.answer_md
    assert client.replies == []  # exactly one call — no self-correction here


def test_guard_rejection_surfaces_as_an_error(chinook: Database) -> None:
    client = FakeClient([text_reply("```sql\nDROP TABLE Artist\n```")])
    result = answer_question(client, chinook, "drop it")
    assert result.error
    assert result.result is None


def test_empty_reply_reports_no_sql(chinook: Database) -> None:
    client = FakeClient([text_reply("")])
    result = answer_question(client, chinook, "anything")
    assert result.error == "no SQL produced"


def test_multi_row_answer_points_at_the_data_tab(chinook: Database) -> None:
    client = FakeClient([text_reply("```sql\nSELECT Name FROM Artist LIMIT 3\n```")])
    result = answer_question(client, chinook, "list some artists")
    assert "3 rows" in result.answer_md
