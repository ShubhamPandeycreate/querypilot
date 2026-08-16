"""Record a golden trace: run the agent live once, save every LLM reply.

The saved file replays in tests/CI with zero network. Usage:
    uv run python scripts/record_golden.py -q "Which artist has the most albums?" \
        --provider ollama --out tests/fixtures/golden_most_albums.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from dbagent.agent.loop import AgentLoop
from dbagent.agent.tools import ToolBelt
from dbagent.config import get_providers
from dbagent.db.database import Database
from dbagent.llm.client import ModelClient
from dbagent.llm.recording import RecordingClient
from dbagent.tracing.tracer import Tracer

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-q", "--question", required=True)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--db", default=str(ROOT / "data" / "chinook.sqlite"))
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--expect",
        default="",
        help="Comma-separated substrings the final answer must contain (for the replay test)",
    )
    args = parser.parse_args()

    console = Console()
    recorder = RecordingClient(ModelClient(get_providers()[args.provider]), args.out)
    database = Database(args.db)
    try:
        result = AgentLoop(recorder, ToolBelt(database), Tracer(None)).run(args.question)
    finally:
        database.close()
    expect = [s.strip() for s in args.expect.split(",") if s.strip()]
    missing = [s for s in expect if s.lower() not in result.answer_md.lower()]
    if missing:
        console.print(f"[red]NOT SAVED — answer missing expected substrings: {missing}[/]")
        console.print(f"answer was: {result.answer_md[:300]}")
        raise SystemExit(1)
    path = recorder.save(meta={"question": args.question, "expect_contains": expect})
    console.print(f"stop_reason={result.stop_reason}, llm_calls={result.llm_calls}")
    console.print(f"answer: {result.answer_md[:200]}")
    console.print(f"[green]golden trace saved:[/] {path}")


if __name__ == "__main__":
    main()
