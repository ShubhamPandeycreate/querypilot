"""Declarative chart rendering: a tiny spec applied to a query result.

Deliberately NOT arbitrary Python — the agent chooses from three chart kinds
and names columns, which keeps chart generation safe for a public demo.
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: render to files, never open windows
import matplotlib.pyplot as plt

from dbagent.db.database import QueryResult

CHART_KINDS = ("bar", "line", "scatter")


def render_chart(
    result: QueryResult,
    *,
    kind: str,
    x: str,
    y: str,
    title: str = "",
    out_dir: str | Path = "traces/charts",
) -> Path:
    """Render `result` as a chart and return the saved PNG path.

    Raises ValueError on unknown kind or columns not present in the result.
    """
    if kind not in CHART_KINDS:
        raise ValueError(f"Unknown chart kind {kind!r}. Choose from: {', '.join(CHART_KINDS)}")
    for column in (x, y):
        if column not in result.columns:
            raise ValueError(f"Column {column!r} is not in the result columns: {result.columns}")

    x_index = result.columns.index(x)
    y_index = result.columns.index(y)
    x_values = [row[x_index] for row in result.rows]
    y_values = [row[y_index] for row in result.rows]

    fig, ax = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
    if kind == "bar":
        ax.bar(range(len(x_values)), y_values)
        ax.set_xticks(range(len(x_values)))
        ax.set_xticklabels([str(v) for v in x_values], rotation=45, ha="right")
    elif kind == "line":
        ax.plot(x_values, y_values, marker="o")
    else:  # scatter
        ax.scatter(x_values, y_values)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    if title:
        ax.set_title(title)

    out_path = Path(out_dir) / f"chart_{int(time.time() * 1000)}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
