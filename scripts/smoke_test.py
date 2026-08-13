"""Phase 0 smoke test wrapper. Usage: uv run python scripts/smoke_test.py
(equivalent to: uv run python -m dbagent smoke)
"""

from dbagent.smoke import run_smoke_test

if __name__ == "__main__":
    raise SystemExit(1 if run_smoke_test() else 0)
