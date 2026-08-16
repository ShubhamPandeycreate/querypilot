"""Adversarial tests for the SQL guard. Every rejection path and the LIMIT logic."""

import pytest

from dbagent.db.guard import GuardError, guard_sql


def rejected(sql: str, error_type: str) -> None:
    with pytest.raises(GuardError) as excinfo:
        guard_sql(sql)
    assert excinfo.value.error_type == error_type, (
        f"{sql!r}: expected {error_type}, got {excinfo.value.error_type}"
    )


# --- plain writes / DDL ---------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "REPLACE INTO t VALUES (1)",
        "DROP TABLE t",
        "CREATE TABLE t (a INT)",
        "CREATE INDEX i ON t(a)",
        "ALTER TABLE t ADD COLUMN b INT",
    ],
)
def test_writes_and_ddl_rejected(sql: str) -> None:
    rejected(sql, "not_read_only")


# --- meta / environment statements ---------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "PRAGMA writable_schema = 1",
        "PRAGMA journal_mode = DELETE",
        "ATTACH DATABASE 'evil.db' AS evil",
        "DETACH DATABASE evil",
        "VACUUM",
        "REINDEX",
        "BEGIN",
        "COMMIT",
    ],
)
def test_meta_statements_rejected(sql: str) -> None:
    rejected(sql, "not_read_only")


# --- smuggling ------------------------------------------------------------


def test_cte_wrapping_delete_rejected() -> None:
    rejected("WITH x AS (SELECT 1) DELETE FROM t", "not_read_only")


def test_cte_wrapping_insert_rejected() -> None:
    rejected("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", "not_read_only")


def test_multiple_statements_rejected() -> None:
    rejected("SELECT 1; DROP TABLE t", "multiple_statements")


def test_two_selects_rejected() -> None:
    rejected("SELECT 1; SELECT 2", "multiple_statements")


def test_values_root_rejected() -> None:
    rejected("VALUES (1, 2)", "not_read_only")


def test_load_extension_rejected() -> None:
    rejected("SELECT load_extension('evil.dll')", "forbidden_function")


def test_readfile_rejected() -> None:
    rejected("SELECT readfile('C:/secrets.txt')", "forbidden_function")


def test_forbidden_function_in_subquery_rejected() -> None:
    rejected(
        "SELECT * FROM t WHERE a IN (SELECT load_extension('x'))",
        "forbidden_function",
    )


# --- junk input -----------------------------------------------------------


def test_empty_rejected() -> None:
    rejected("", "empty")


def test_whitespace_rejected() -> None:
    rejected("   \n  ", "empty")


def test_garbage_rejected() -> None:
    with pytest.raises(GuardError):
        guard_sql("SELEC FORM WAT")


def test_comment_only_rejected() -> None:
    rejected("-- just a comment", "empty")


# --- allowed queries ------------------------------------------------------


def test_simple_select_allowed() -> None:
    out = guard_sql("SELECT * FROM album")
    assert out.upper().startswith("SELECT")


def test_cte_select_allowed() -> None:
    out = guard_sql("WITH top AS (SELECT 1 AS x) SELECT x FROM top")
    assert "WITH" in out.upper()


def test_union_allowed() -> None:
    out = guard_sql("SELECT 1 UNION SELECT 2")
    assert "UNION" in out.upper()


def test_join_and_aggregate_allowed() -> None:
    out = guard_sql(
        "SELECT g.Name, count(*) AS n FROM Track t "
        "JOIN Genre g ON g.GenreId = t.GenreId GROUP BY g.Name ORDER BY n DESC"
    )
    assert "JOIN" in out.upper()


def test_trailing_semicolon_allowed() -> None:
    out = guard_sql("SELECT 1;")
    assert out.upper().startswith("SELECT")


def test_comment_with_select_allowed() -> None:
    out = guard_sql("SELECT 1 -- the drop below is just a comment: DROP TABLE t")
    assert out.upper().startswith("SELECT")


# --- LIMIT handling -------------------------------------------------------


def test_limit_injected_when_missing() -> None:
    out = guard_sql("SELECT * FROM album", row_limit=200)
    assert "LIMIT 200" in out.upper()


def test_existing_small_limit_kept() -> None:
    out = guard_sql("SELECT * FROM album LIMIT 5", row_limit=200)
    assert "LIMIT 5" in out.upper()
    assert "200" not in out


def test_oversized_limit_clamped() -> None:
    out = guard_sql("SELECT * FROM album LIMIT 999999", row_limit=200)
    assert "LIMIT 200" in out.upper()
    assert "999999" not in out


def test_limit_injected_on_union() -> None:
    out = guard_sql("SELECT 1 UNION SELECT 2", row_limit=200)
    assert "LIMIT 200" in out.upper()


def test_subquery_limit_does_not_satisfy_outer() -> None:
    out = guard_sql("SELECT * FROM (SELECT * FROM album LIMIT 3)", row_limit=200)
    assert "LIMIT 3" in out.upper()
    assert "LIMIT 200" in out.upper()
