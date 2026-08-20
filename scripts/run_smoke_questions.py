"""Run the agent over the 20 Chinook smoke questions and score execution accuracy.

Usage:
    uv run python scripts/run_smoke_questions.py --provider ollama [--limit N] [--ids id1,id2]

Scoring: the agent's final SQL is executed and its result compared (as a
multiset) against the gold SQL's result — the same execution-accuracy idea the
Phase 3 benchmark harness uses. Questions where the agent returned no SQL are
counted as failures but flagged separately.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from dbagent.agent.loop import AgentLoop
from dbagent.agent.tools import ToolBelt
from dbagent.config import get_providers
from dbagent.db.database import Database
from dbagent.llm.client import ModelClient
from dbagent.tracing.tracer import Tracer, default_trace_path
from evals.metrics import results_match

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "evals" / "smoke_questions.json"
CHINOOK = ROOT / "data" / "chinook.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N questions")
    parser.add_argument("--ids", default=None, help="Comma-separated question ids to run")
    args = parser.parse_args()

    console = Console()
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if args.limit:
        questions = questions[: args.limit]

    provider = get_providers()[args.provider]
    client = ModelClient(provider)
    gold_db = Database(CHINOOK)

    trace_path = default_trace_path()
    console.print(
        f"[bold]Smoke run[/] — {len(questions)} questions, model={provider.model} "
        f"({args.provider}), trace={trace_path}"
    )

    table = Table(title="Smoke question results")
    table.add_column("id")
    table.add_column("result")
    table.add_column("llm calls", justify="right")
    table.add_column("time", justify="right")
    table.add_column("note")

    passed = failed = no_sql = 0
    started = time.perf_counter()
    for item in questions:
        agent_db = Database(CHINOOK)
        loop = AgentLoop(client, ToolBelt(agent_db), Tracer(trace_path))
        t0 = time.perf_counter()
        try:
            result = loop.run(item["question"])
            elapsed = f"{time.perf_counter() - t0:.1f}s"
            if not result.sql.strip():
                no_sql += 1
                failed += 1
                table.add_row(
                    item["id"], "[red]FAIL[/]", str(result.llm_calls), elapsed, "no SQL in answer"
                )
                continue
            gold = gold_db.run_sql(item["gold_sql"])
            try:
                predicted = gold_db.run_sql(result.sql)
            except Exception as error:
                failed += 1
                table.add_row(
                    item["id"],
                    "[red]FAIL[/]",
                    str(result.llm_calls),
                    elapsed,
                    f"final SQL does not run: {str(error)[:60]}",
                )
                continue
            if results_match(gold.rows, predicted.rows):
                passed += 1
                table.add_row(item["id"], "[green]PASS[/]", str(result.llm_calls), elapsed, "")
            else:
                failed += 1
                table.add_row(
                    item["id"],
                    "[red]FAIL[/]",
                    str(result.llm_calls),
                    elapsed,
                    f"result mismatch (gold {len(gold.rows)} rows, got {len(predicted.rows)})",
                )
        finally:
            agent_db.close()

    gold_db.close()
    total_time = time.perf_counter() - started
    console.print(table)
    total = passed + failed
    accuracy = (100 * passed / total) if total else 0.0
    console.print(
        f"[bold]{passed}/{total} passed ({accuracy:.0f}%)[/] — "
        f"{no_sql} gave no SQL — {total_time:.0f}s total"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
