"""Command-line interface. Phase 0: version + smoke. Later phases add sql / chat / eval."""

from __future__ import annotations

import typer
from rich.console import Console

import dbagent

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def version() -> None:
    """Print the QueryPilot version."""
    console.print(f"querypilot {dbagent.__version__}")


@app.command()
def smoke() -> None:
    """Send a trivial prompt through every configured provider."""
    from dbagent.smoke import run_smoke_test

    raise typer.Exit(code=1 if run_smoke_test() else 0)


def _resolve_db(db: str) -> str:
    """Accept a bare name (looked up in data/) or an explicit path."""
    from pathlib import Path

    candidate = Path(db)
    if candidate.exists():
        return str(candidate)
    named = Path("data") / f"{db}.sqlite"
    if named.exists():
        return str(named)
    raise typer.BadParameter(f"No database at {db!r} or {named}")


@app.command()
def sql(
    query: str = typer.Argument(..., help="One read-only SELECT statement."),
    db: str = typer.Option("chinook", help="Database name in data/ or a path."),
) -> None:
    """Run a guarded read-only SQL query and pretty-print the result."""
    from rich.table import Table

    from dbagent.db.database import Database
    from dbagent.db.guard import GuardError

    database = Database(_resolve_db(db))
    try:
        result = database.run_sql(query)
    except GuardError as error:
        console.print(f"[red]Rejected ({error.error_type}):[/] {error.message}")
        raise typer.Exit(code=1) from error
    except Exception as error:  # sqlite errors: show the message, not a traceback
        console.print(f"[red]SQL error:[/] {error}")
        raise typer.Exit(code=1) from error
    finally:
        database.close()

    console.print(f"[dim]executed:[/] {result.sql}")
    table = Table(show_lines=False)
    for column in result.columns:
        table.add_column(column)
    for row in result.rows[:50]:
        table.add_row(*[str(v) for v in row])
    console.print(table)
    if len(result.rows) > 50:
        console.print(f"[dim]... showing 50 of {len(result.rows)} rows[/]")


def _print_step(event: dict) -> None:
    kind = event["kind"]
    if kind == "llm_call":
        tokens = event.get("usage") or {}
        total = tokens.get("prompt_tokens", 0) + tokens.get("completion_tokens", 0)
        console.print(f"  [dim]llm call #{event['n']} — {event['latency_s']}s, {total} tokens[/]")
    elif kind == "tool":
        icon = "[green]ok[/]" if event["ok"] else "[red]err[/]"
        console.print(f"  {icon} [bold]{event['name']}[/] — {event['summary']}")
    elif kind == "nudge":
        console.print(f"  [yellow]nudge: {event['reason']}[/]")


@app.command()
def chat(
    db: str = typer.Option("chinook", help="Database name in data/ or a path."),
    provider: str = typer.Option("ollama", help="gemini | groq | openrouter | ollama"),
    question: str | None = typer.Option(
        None, "--question", "-q", help="Ask one question and exit (default: interactive)."
    ),
    trace: bool = typer.Option(True, help="Write a JSONL trace under traces/."),
) -> None:
    """Ask natural-language questions; the agent explores, queries, self-corrects."""
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax

    from dbagent.agent.loop import AgentLoop
    from dbagent.agent.tools import ToolBelt
    from dbagent.config import get_providers
    from dbagent.db.database import Database
    from dbagent.llm.client import ModelClient
    from dbagent.tracing.tracer import Tracer, default_trace_path

    providers = get_providers()
    if provider not in providers:
        raise typer.BadParameter(f"Unknown provider {provider!r}. Choose: {list(providers)}")
    chosen = providers[provider]
    if provider != "ollama" and not chosen.api_key:
        console.print(f"[red]No API key configured for {provider}. Fill .env first.[/]")
        raise typer.Exit(code=1)

    database = Database(_resolve_db(db))
    trace_path = default_trace_path() if trace else None
    loop = AgentLoop(
        ModelClient(chosen),
        ToolBelt(database),
        Tracer(trace_path),
        on_step=_print_step,
    )
    console.print(f"[bold]QueryPilot[/] — db={db}, model={chosen.model} ({provider})")

    def ask(text: str) -> None:
        console.rule(f"[bold]{text}")
        try:
            result = loop.run(text)
        except Exception as error:  # provider errors: readable message, not a traceback
            console.print(f"[red]{type(error).__name__}:[/] {str(error)[:400]}")
            return
        console.print(Panel(Markdown(result.answer_md), title="answer", border_style="green"))
        if result.sql:
            console.print(Syntax(result.sql, "sql", word_wrap=True))
        if result.caveats:
            console.print(f"[dim]caveats: {result.caveats}[/]")
        for chart in result.chart_paths:
            console.print(f"[cyan]chart:[/] {chart}")
        if result.stop_reason != "final_answer":
            console.print(f"[yellow]stopped: {result.stop_reason}[/]")

    try:
        if question is not None:
            ask(question)
        else:
            console.print("[dim]Type a question, or 'quit' to exit.[/]")
            while True:
                text = console.input("[bold cyan]? [/]").strip()
                if text.lower() in {"quit", "exit", "q"}:
                    break
                if text:
                    ask(text)
    finally:
        database.close()
        if trace_path is not None:
            console.print(f"[dim]trace: {trace_path}[/]")


@app.command()
def eval(
    dataset: str = typer.Option("bird", help="bird | spider | chinook"),
    mode: str = typer.Option("single_shot", help="single_shot | agent"),
    provider: str = typer.Option("ollama", help="gemini | groq | openrouter | ollama"),
    subset: int = typer.Option(0, help="Run only a fixed deterministic subset of N questions."),
    rpm: int = typer.Option(0, help="Requests/minute cap (0 = provider default)."),
    concurrency: int = typer.Option(1, help="Parallel questions (keep 1 for local models)."),
    out: str = typer.Option("", help="Results JSONL path (default: evals/results/<auto>.jsonl)"),
) -> None:
    """Run (or resume) an execution-accuracy eval and print the summary."""
    from evals.datasets import LOADERS, fixed_subset
    from evals.runner import run_eval, summarize, write_report

    from dbagent.config import get_providers
    from dbagent.llm.client import ModelClient

    if dataset not in LOADERS:
        raise typer.BadParameter(f"Unknown dataset {dataset!r}. Choose: {list(LOADERS)}")
    items = LOADERS[dataset]()
    if subset:
        items = fixed_subset(items, subset)

    providers = get_providers()
    chosen = providers[provider]
    if provider != "ollama" and not chosen.api_key:
        console.print(f"[red]No API key configured for {provider}.[/]")
        raise typer.Exit(code=1)
    # Free-tier defaults, conservative: Gemini flash 5 RPM (observed), Groq 25
    # (limit 30), OpenRouter 15 (limit 20). Local: effectively unlimited.
    default_rpm = {"gemini": 5, "groq": 25, "openrouter": 15, "ollama": 600}
    effective_rpm = rpm or default_rpm[provider]

    out_path = out or f"evals/results/{dataset}_{mode}_{provider}.jsonl"
    console.print(
        f"[bold]eval[/] {dataset} n={len(items)} mode={mode} model={chosen.model} "
        f"rpm={effective_rpm} out={out_path}"
    )
    if mode == "agent":
        console.print(f"[dim]~{len(items) * 5} LLM calls expected (about 5/question)[/]")

    def on_record(record) -> None:  # noqa: ANN001 - EvalRecord
        status = "[green]match[/]" if record.match else "[red]miss[/]"
        note = record.error[:60] if record.error else ""
        console.print(f"  {record.item_id} {status} {record.latency_s}s {note}")

    run_eval(
        items,
        mode=mode,
        client=ModelClient(chosen),
        rpm=effective_rpm,
        out_path=out_path,
        concurrency=concurrency,
        on_record=on_record,
    )
    summary = summarize(out_path)
    console.print(summary)
    report = write_report(
        [summary], f"evals/reports/{dataset}_{mode}_{provider}.md", f"QueryPilot eval: {dataset}"
    )
    console.print(f"[green]report:[/] {report}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
