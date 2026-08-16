"""Execution-accuracy metric: do two SQL results denote the same answer?

The standard text-to-SQL metric (BIRD/Spider style): execute gold and
predicted SQL, compare result sets as multisets of rows — column names and
row order don't matter (unless the question demands ordering), numeric noise
is tolerated.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

FLOAT_DECIMALS = 4


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return round(value, FLOAT_DECIMALS)
    if isinstance(value, int):
        return value
    return value


def _normalize_rows(rows: list[tuple]) -> list[tuple]:
    return [tuple(_normalize_value(v) for v in row) for row in rows]


def results_match(
    gold_rows: list[tuple],
    predicted_rows: list[tuple],
    *,
    order_sensitive: bool = False,
) -> bool:
    gold = _normalize_rows(gold_rows)
    predicted = _normalize_rows(predicted_rows)
    if order_sensitive:
        return gold == predicted
    return Counter(gold) == Counter(predicted)
