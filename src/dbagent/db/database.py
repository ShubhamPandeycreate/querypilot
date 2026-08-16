"""Read-only SQLite adapter: introspection + guarded execution with a timeout.

Second layer of defense after guard.py: the connection itself is opened with
mode=ro, so even a guard bypass cannot write. Long-running queries are aborted
by a progress handler once the per-query deadline passes.
"""

from __future__ import annotations

import sqlite3
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from dbagent.db.guard import guard_sql


@dataclass(frozen=True)
class TableInfo:
    name: str
    row_count: int


@dataclass(frozen=True)
class TableSchema:
    name: str
    ddl: str
    foreign_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    sql: str


class Database:
    """One read-only SQLite database."""

    def __init__(
        self,
        path: str | Path,
        *,
        row_limit: int = 200,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Database file not found: {self.path}")
        self.row_limit = row_limit
        self.timeout_seconds = timeout_seconds
        uri = f"file:{urllib.parse.quote(self.path.resolve().as_posix())}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True)
        self._deadline: float = 0.0
        # Checked every N opcodes; returning nonzero aborts the running query.
        self._conn.set_progress_handler(self._check_deadline, 20_000)

    def _check_deadline(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        self._deadline = time.monotonic() + self.timeout_seconds
        return self._conn.execute(sql, params)

    def close(self) -> None:
        self._conn.close()

    # --- introspection ----------------------------------------------------

    def table_names(self) -> list[str]:
        cursor = self._execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [row[0] for row in cursor.fetchall()]

    def list_tables(self) -> list[TableInfo]:
        tables = []
        for name in self.table_names():
            cursor = self._execute(f'SELECT count(*) FROM "{self._quote(name)}"')
            tables.append(TableInfo(name=name, row_count=cursor.fetchone()[0]))
        return tables

    def get_schema(self, tables: list[str]) -> list[TableSchema]:
        known = {t.lower(): t for t in self.table_names()}
        schemas = []
        for requested in tables:
            actual = known.get(requested.lower())
            if actual is None:
                raise ValueError(f"Unknown table: {requested!r}. Known: {sorted(known.values())}")
            ddl_row = self._execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (actual,)
            ).fetchone()
            fk_rows = self._execute(f'PRAGMA foreign_key_list("{self._quote(actual)}")').fetchall()
            # PRAGMA foreign_key_list columns: id, seq, table, from, to, ...
            fks = [f"{actual}.{row[3]} -> {row[2]}({row[4]})" for row in fk_rows]
            schemas.append(TableSchema(name=actual, ddl=ddl_row[0], foreign_keys=fks))
        return schemas

    def sample_rows(self, table: str, n: int = 5) -> QueryResult:
        known = {t.lower(): t for t in self.table_names()}
        actual = known.get(table.lower())
        if actual is None:
            raise ValueError(f"Unknown table: {table!r}. Known: {sorted(known.values())}")
        n = max(1, min(n, 5))
        sql = f'SELECT * FROM "{self._quote(actual)}" LIMIT {n}'
        cursor = self._execute(sql)
        columns = [d[0] for d in cursor.description]
        return QueryResult(columns=columns, rows=cursor.fetchall(), sql=sql)

    # --- guarded execution ------------------------------------------------

    def run_sql(self, sql: str) -> QueryResult:
        """Validate through the guard, then execute. Raises GuardError or
        sqlite3.Error (OperationalError 'interrupted' on timeout)."""
        safe_sql = guard_sql(sql, row_limit=self.row_limit)
        cursor = self._execute(safe_sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        return QueryResult(columns=columns, rows=cursor.fetchall(), sql=safe_sql)

    @staticmethod
    def _quote(identifier: str) -> str:
        return identifier.replace('"', '""')
