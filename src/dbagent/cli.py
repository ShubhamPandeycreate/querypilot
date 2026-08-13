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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
