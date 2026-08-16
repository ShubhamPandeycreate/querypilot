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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
