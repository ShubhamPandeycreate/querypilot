"""Single-shot text-to-SQL: whole schema in one prompt, one completion, one query.

This is the baseline the agent loop is measured against (Phase 3), and the
"Single-shot" mode in the demo app — being able to flip between the two on the
same question is the point: the eval numbers claim a delta, the app shows it.

The prompt builder and SQL extractor live here (not in `evals/`) so the app and
the eval harness provably share one implementation.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from dbagent.db.database import Database, QueryResult
from dbagent.llm.client import ChatClient

# Reasoning models spend thinking tokens before any content; a small cap yields
# an empty reply that scores as "no SQL produced". See evals/runner.py notes.
SINGLE_SHOT_MAX_TOKENS = 5120

SINGLE_SHOT_SYSTEM = """\
You translate questions into a single SQLite SELECT statement.
Reply with ONLY the SQL, in a ```sql fenced block. No explanations.
Use exactly the tables and columns from the provided schema.
"""

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(reply_text: str) -> str:
    """Pull the SQL out of a model reply, fenced or bare."""
    match = _SQL_FENCE.search(reply_text)
    sql = (match.group(1) if match else reply_text).strip()
    return sql.rstrip(";").strip()


def build_prompt(
    *, ddl: str, foreign_keys: str, question: str, evidence: str = ""
) -> list[dict[str, Any]]:
    """The exact prompt shape used for every single-shot number we publish."""
    parts = [f"Database schema:\n{ddl}"]
    if foreign_keys:
        parts.append(f"Foreign keys:\n{foreign_keys}")
    if evidence:
        parts.append(f"Hint: {evidence}")
    parts.append(f"Question: {question}")
    return [
        {"role": "system", "content": SINGLE_SHOT_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def schema_text(db: Database) -> tuple[str, str]:
    """(ddl, foreign_keys) for every table in `db`, as the prompt wants them."""
    schemas = db.get_schema(db.table_names())
    ddl = "\n\n".join(s.ddl for s in schemas)
    fks = "\n".join(fk for s in schemas for fk in s.foreign_keys)
    return ddl, fks


@dataclass
class SingleShotResult:
    """Mirrors AgentResult closely enough that one renderer draws both."""

    answer_md: str
    sql: str = ""
    caveats: str = ""
    llm_calls: int = 1
    stop_reason: str = "single_shot"
    result: QueryResult | None = None
    error: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    latency_s: float = 0.0


def answer_question(client: ChatClient, db: Database, question: str) -> SingleShotResult:
    """One call, one query. No tools, no schema exploration, no self-correction."""
    started = time.perf_counter()
    ddl, fks = schema_text(db)
    reply = client.chat(
        build_prompt(ddl=ddl, foreign_keys=fks, question=question),
        max_tokens=SINGLE_SHOT_MAX_TOKENS,
    )
    sql = extract_sql(reply.content or "")
    elapsed = round(time.perf_counter() - started, 2)
    usage = dict(reply.usage)

    if not sql:
        return SingleShotResult(
            answer_md="The model returned no SQL for this question.",
            error="no SQL produced",
            usage=usage,
            latency_s=elapsed,
        )
    try:
        result = db.run_sql(sql)
    except Exception as error:  # guard rejection or SQLite error — no retry by design
        return SingleShotResult(
            answer_md=(
                "The generated SQL failed to run. In single-shot mode there is no "
                "second attempt — that is exactly what the agent loop adds."
            ),
            sql=sql,
            error=str(error)[:300],
            usage=usage,
            latency_s=elapsed,
        )
    return SingleShotResult(
        answer_md=_describe(result),
        sql=result.sql,
        caveats="Single-shot mode: no schema exploration, no self-correction.",
        result=result,
        usage=usage,
        latency_s=elapsed,
    )


def _describe(result: QueryResult) -> str:
    if not result.rows:
        return "The query ran and returned **no rows**."
    if len(result.rows) == 1 and len(result.columns) == 1:
        return f"**{result.rows[0][0]}**"
    return f"The query returned **{len(result.rows)} rows** — see the Data tab."
