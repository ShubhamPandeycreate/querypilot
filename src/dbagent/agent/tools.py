"""The agent's toolbelt: six tools the model can call, plus their JSON schemas.

Every tool returns a JSON-serializable dict. Failures come back as
{"error": {"type", "message", "hint"}} instead of raising, so the agent loop
can relay them to the model and let it self-correct.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dbagent.charts import CHART_KINDS, render_chart
from dbagent.db.database import Database, QueryResult
from dbagent.db.guard import GuardError

# Rows actually shown to the model per query — keeps prompts small. The guard's
# row_limit (200) caps what we fetch; this caps what we relay.
TOOL_ROW_LIMIT = 50


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    return value


def _rows_payload(result: QueryResult, limit: int = TOOL_ROW_LIMIT) -> dict[str, Any]:
    shown = result.rows[:limit]
    return {
        "columns": result.columns,
        "rows": [[_jsonable(v) for v in row] for row in shown],
        "row_count": len(result.rows),
        "truncated": len(result.rows) > len(shown),
    }


def _error(error_type: str, message: str, hint: str) -> dict[str, Any]:
    return {"error": {"type": error_type, "message": message, "hint": hint}}


def _sql_error_hint(message: str) -> str:
    lowered = message.lower()
    if "no such table" in lowered:
        return "That table does not exist. Call list_tables to see what is available."
    if "no such column" in lowered:
        return "That column does not exist. Call get_schema on the table to see its columns."
    if "interrupt" in lowered:
        return "The query hit the time limit. Add WHERE filters or aggregate less data."
    if "syntax error" in lowered:
        return "SQLite rejected the syntax. Simplify the query and check SQLite dialect rules."
    return "Rewrite the query and try again."


class ToolBelt:
    """Tools bound to one database. Holds the last query result for charting."""

    def __init__(self, db: Database, *, charts_dir: str | Path = "traces/charts") -> None:
        self.db = db
        self.charts_dir = Path(charts_dir)
        self.last_result: QueryResult | None = None

    # --- the six tools ----------------------------------------------------

    def list_tables(self) -> dict[str, Any]:
        tables = self.db.list_tables()
        return {"tables": [{"name": t.name, "row_count": t.row_count} for t in tables]}

    def get_schema(self, tables: list[str]) -> dict[str, Any]:
        try:
            schemas = self.db.get_schema(tables)
        except ValueError as error:
            return _error("unknown_table", str(error), "Call list_tables to see available tables.")
        return {
            "tables": [
                {"name": s.name, "ddl": s.ddl, "foreign_keys": s.foreign_keys} for s in schemas
            ]
        }

    def sample_rows(self, table: str, n: int = 5) -> dict[str, Any]:
        try:
            result = self.db.sample_rows(table, n=n)
        except ValueError as error:
            return _error("unknown_table", str(error), "Call list_tables to see available tables.")
        return _rows_payload(result)

    def run_sql(self, sql: str) -> dict[str, Any]:
        try:
            result = self.db.run_sql(sql)
        except GuardError as error:
            return _error(
                error.error_type,
                error.message,
                "Send exactly one read-only SELECT statement.",
            )
        except sqlite3.Error as error:
            return _error("sql_error", str(error), _sql_error_hint(str(error)))
        self.last_result = result
        payload = _rows_payload(result)
        payload["sql"] = result.sql
        if not result.rows:
            payload["note"] = (
                "The query returned zero rows. If you expected data, verify value formats "
                "with sample_rows before concluding the answer is empty."
            )
        return payload

    def render_chart(self, kind: str, x: str, y: str, title: str = "") -> dict[str, Any]:
        if self.last_result is None:
            return _error(
                "no_result", "No query result to chart.", "Call run_sql first, then chart it."
            )
        try:
            path = render_chart(
                self.last_result, kind=kind, x=x, y=y, title=title, out_dir=self.charts_dir
            )
        except ValueError as error:
            return _error(
                "bad_chart_spec",
                str(error),
                "Use a column from the last run_sql result and a supported chart kind.",
            )
        return {"chart_path": str(path), "kind": kind, "x": x, "y": y, "title": title}

    def final_answer(self, answer_md: str, sql: str = "", caveats: str = "") -> dict[str, Any]:
        return {"answer_md": answer_md, "sql": sql, "caveats": caveats}

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool by name with model-supplied arguments."""
        handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "list_tables": self.list_tables,
            "get_schema": self.get_schema,
            "sample_rows": self.sample_rows,
            "run_sql": self.run_sql,
            "render_chart": self.render_chart,
            "final_answer": self.final_answer,
        }
        handler = handlers.get(name)
        if handler is None:
            return _error(
                "unknown_tool", f"No tool named {name!r}.", "Use one of the provided tools."
            )
        try:
            return handler(**arguments)
        except TypeError as error:
            return _error(
                "bad_arguments", str(error), "Check the tool's parameter schema and retry."
            )


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "List every table in the database with its row count. "
            "Start here when you don't know the schema.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": "Get CREATE TABLE DDL and foreign-key relationships for specific "
            "tables. Request only the tables you need — schemas are verbose.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Table names to describe.",
                    }
                },
                "required": ["tables"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sample_rows",
            "description": "Fetch up to 5 example rows from a table to see real value "
            "formats (dates, codes, casing) before writing WHERE clauses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {"type": "string", "description": "Table to sample."},
                    "n": {"type": "integer", "description": "Rows to fetch (1-5).", "default": 5},
                },
                "required": ["table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Execute ONE read-only SQLite SELECT (CTEs and UNION allowed). "
            "Writes, PRAGMA and multiple statements are rejected. Results are capped; "
            "aggregate in SQL rather than fetching raw rows when possible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "The SELECT statement to run."}
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_chart",
            "description": "Chart the most recent run_sql result. Use after a query that "
            "produced categories/series worth visualizing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(CHART_KINDS)},
                    "x": {"type": "string", "description": "Column for the x-axis."},
                    "y": {"type": "string", "description": "Column for the y-axis."},
                    "title": {"type": "string", "description": "Chart title."},
                },
                "required": ["kind", "x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "final_answer",
            "description": "Finish the task. Give the user-facing answer in markdown, the "
            "final SQL you relied on, and any caveats (assumptions, limits hit).",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer_md": {"type": "string", "description": "The answer, markdown."},
                    "sql": {"type": "string", "description": "The SQL the answer rests on."},
                    "caveats": {"type": "string", "description": "Assumptions or limitations."},
                },
                "required": ["answer_md"],
            },
        },
    },
]
