"""Database adapter tests against the committed Chinook database."""

import sqlite3
from pathlib import Path

import pytest

from dbagent.db.database import Database
from dbagent.db.guard import GuardError

CHINOOK_PATH = Path(__file__).resolve().parent.parent / "data" / "chinook.sqlite"


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        Database("does/not/exist.sqlite")


def test_table_names(chinook: Database) -> None:
    names = chinook.table_names()
    assert "Album" in names
    assert "Artist" in names
    assert not any(n.startswith("sqlite_") for n in names)


def test_list_tables_row_counts(chinook: Database) -> None:
    tables = {t.name: t.row_count for t in chinook.list_tables()}
    assert tables["Album"] == 347
    assert tables["Artist"] == 275


def test_get_schema_ddl_and_fks(chinook: Database) -> None:
    (schema,) = chinook.get_schema(["Album"])
    assert schema.name == "Album"
    assert "CREATE TABLE" in schema.ddl
    assert any(fk == "Album.ArtistId -> Artist(ArtistId)" for fk in schema.foreign_keys)


def test_get_schema_case_insensitive(chinook: Database) -> None:
    (schema,) = chinook.get_schema(["album"])
    assert schema.name == "Album"


def test_get_schema_unknown_table(chinook: Database) -> None:
    with pytest.raises(ValueError, match="Unknown table"):
        chinook.get_schema(["NoSuchTable"])


def test_sample_rows(chinook: Database) -> None:
    result = chinook.sample_rows("Artist", n=3)
    assert result.columns == ["ArtistId", "Name"]
    assert len(result.rows) == 3


def test_sample_rows_n_clamped(chinook: Database) -> None:
    assert len(chinook.sample_rows("Artist", n=999).rows) == 5


def test_run_sql_happy_path(chinook: Database) -> None:
    result = chinook.run_sql("SELECT Name FROM Artist ORDER BY ArtistId LIMIT 2")
    assert result.columns == ["Name"]
    assert len(result.rows) == 2


def test_run_sql_guard_applies(chinook: Database) -> None:
    with pytest.raises(GuardError):
        chinook.run_sql("DROP TABLE Album")


def test_run_sql_row_limit_injected(chinook: Database) -> None:
    result = chinook.run_sql("SELECT * FROM Track")
    assert len(result.rows) == 200  # default row_limit, Track has 3503 rows


def test_run_sql_bad_column_raises_operational_error(chinook: Database) -> None:
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        chinook.run_sql("SELECT NoSuchColumn FROM Album")


def test_connection_is_read_only(chinook: Database) -> None:
    # Bypass the guard on purpose: the ro connection itself must refuse writes.
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        chinook._conn.execute("CREATE TABLE hack (a INT)")


def test_timeout_aborts_runaway_query() -> None:
    db = Database(CHINOOK_PATH, timeout_seconds=0.2)
    try:
        with pytest.raises(sqlite3.OperationalError, match="interrupt"):
            db.run_sql(
                "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
                "SELECT count(*) FROM c"
            )
    finally:
        db.close()
