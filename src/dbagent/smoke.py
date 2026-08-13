"""Provider smoke test: send one trivial prompt through every configured backend.

Providers without an API key in .env are reported as SKIPPED, not failed.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.table import Table

from dbagent.config import get_providers, get_settings
from dbagent.llm.client import ModelClient

PROMPT = "Reply with exactly one word: OK"


def run_smoke_test() -> int:
    """Returns the number of providers that FAILED (skips don't count)."""
    console = Console()
    providers = get_providers(get_settings())

    table = Table(title="QueryPilot provider smoke test")
    table.add_column("provider")
    table.add_column("model")
    table.add_column("status")
    table.add_column("latency")
    table.add_column("response / error")

    failures = 0
    for name, provider in providers.items():
        if name != "ollama" and not provider.api_key:
            table.add_row(name, provider.model, "[yellow]SKIPPED[/]", "-", "no API key in .env")
            continue
        start = time.perf_counter()
        try:
            reply = ModelClient(provider).complete(PROMPT)
            status, detail = "[green]OK[/]", reply[:60]
        except Exception as exc:  # noqa: BLE001 - report every provider, decide at the end
            failures += 1
            status, detail = "[red]FAIL[/]", str(exc)[:120]
        elapsed = f"{time.perf_counter() - start:.2f}s"
        table.add_row(name, provider.model, status, elapsed, detail)

    console.print(table)
    if failures:
        console.print(f"[red]{failures} provider(s) failed.[/]")
    return failures
