"""Dataset loader tests. Benchmark-dependent tests skip when data isn't staged,
so CI (which never downloads benchmarks) stays green."""

import pytest

from evals.datasets import load_bird_mini_dev, load_chinook_smoke


def test_chinook_smoke_loads() -> None:
    items = load_chinook_smoke()
    assert len(items) == 20
    assert all(item.db_path.exists() for item in items)
    assert all(item.gold_sql.upper().startswith(("SELECT", "WITH")) for item in items)


def test_bird_mini_dev_loads_when_staged() -> None:
    try:
        items = load_bird_mini_dev()
    except FileNotFoundError:
        pytest.skip("BIRD Mini-Dev not staged (scripts/fetch_benchmarks.py --bird)")
    assert len(items) == 500
    assert sum(1 for item in items if not item.db_path.exists()) == 0
    assert {item.difficulty for item in items} == {"simple", "moderate", "challenging"}
    assert any(item.evidence for item in items)  # BIRD's hint field survives loading
