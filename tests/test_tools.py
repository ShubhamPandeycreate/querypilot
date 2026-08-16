"""ToolBelt tests: structured results, structured errors, chart rendering, dispatch."""

import json
from pathlib import Path

import pytest

from dbagent.agent.tools import TOOL_SCHEMAS, ToolBelt
from dbagent.db.database import Database


@pytest.fixture
def belt(chinook: Database, tmp_path: Path) -> ToolBelt:
    return ToolBelt(chinook, charts_dir=tmp_path)


def test_list_tables(belt: ToolBelt) -> None:
    out = belt.list_tables()
    names = {t["name"] for t in out["tables"]}
    assert {"Album", "Artist", "Track"} <= names


def test_get_schema(belt: ToolBelt) -> None:
    out = belt.get_schema(["Album"])
    assert "CREATE TABLE" in out["tables"][0]["ddl"]
    assert out["tables"][0]["foreign_keys"] == ["Album.ArtistId -> Artist(ArtistId)"]


def test_get_schema_unknown_table_returns_error_dict(belt: ToolBelt) -> None:
    out = belt.get_schema(["Nope"])
    assert out["error"]["type"] == "unknown_table"
    assert "list_tables" in out["error"]["hint"]


def test_sample_rows(belt: ToolBelt) -> None:
    out = belt.sample_rows("Artist", n=2)
    assert out["columns"] == ["ArtistId", "Name"]
    assert out["row_count"] == 2


def test_run_sql_success_sets_last_result(belt: ToolBelt) -> None:
    out = belt.run_sql("SELECT Name FROM Genre ORDER BY GenreId LIMIT 3")
    assert out["row_count"] == 3
    assert belt.last_result is not None


def test_run_sql_truncates_rows_for_model(belt: ToolBelt) -> None:
    out = belt.run_sql("SELECT TrackId FROM Track")
    assert out["row_count"] == 200  # guard cap
    assert len(out["rows"]) == 50  # tool cap
    assert out["truncated"] is True


def test_run_sql_zero_rows_nudges_verification(belt: ToolBelt) -> None:
    out = belt.run_sql("SELECT * FROM Artist WHERE Name = 'zzz-no-such-artist'")
    assert out["row_count"] == 0
    assert "sample_rows" in out["note"]


def test_run_sql_guard_error(belt: ToolBelt) -> None:
    out = belt.run_sql("DROP TABLE Album")
    assert out["error"]["type"] == "not_read_only"


def test_run_sql_bad_column_error_hint(belt: ToolBelt) -> None:
    out = belt.run_sql("SELECT WrongCol FROM Album")
    assert out["error"]["type"] == "sql_error"
    assert "get_schema" in out["error"]["hint"]


def test_render_chart_without_result(belt: ToolBelt) -> None:
    assert belt.render_chart("bar", "a", "b")["error"]["type"] == "no_result"


def test_render_chart_success(belt: ToolBelt, tmp_path: Path) -> None:
    belt.run_sql(
        "SELECT g.Name AS genre, count(*) AS n FROM Track t "
        "JOIN Genre g ON g.GenreId = t.GenreId GROUP BY g.Name ORDER BY n DESC LIMIT 5"
    )
    out = belt.render_chart("bar", "genre", "n", title="Top genres")
    path = Path(out["chart_path"])
    assert path.exists() and path.stat().st_size > 0
    assert path.parent == tmp_path


def test_render_chart_bad_column(belt: ToolBelt) -> None:
    belt.run_sql("SELECT Name FROM Genre LIMIT 3")
    assert belt.render_chart("bar", "Nope", "Name")["error"]["type"] == "bad_chart_spec"


def test_final_answer_passthrough(belt: ToolBelt) -> None:
    out = belt.final_answer("**42**", sql="SELECT 42", caveats="none")
    assert out == {"answer_md": "**42**", "sql": "SELECT 42", "caveats": "none"}


def test_dispatch_routes_and_reports_unknown(belt: ToolBelt) -> None:
    assert "tables" in belt.dispatch("list_tables", {})
    assert belt.dispatch("hack_the_db", {})["error"]["type"] == "unknown_tool"
    assert belt.dispatch("run_sql", {"nope": 1})["error"]["type"] == "bad_arguments"


def test_tool_results_are_json_serializable(belt: ToolBelt) -> None:
    for name, args in [
        ("list_tables", {}),
        ("get_schema", {"tables": ["Album"]}),
        ("sample_rows", {"table": "Artist"}),
        ("run_sql", {"sql": "SELECT * FROM Album LIMIT 3"}),
    ]:
        json.dumps(belt.dispatch(name, args))


def test_schemas_cover_all_six_tools() -> None:
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert names == [
        "list_tables",
        "get_schema",
        "sample_rows",
        "run_sql",
        "render_chart",
        "final_answer",
    ]
    for schema in TOOL_SCHEMAS:
        parameters = schema["function"]["parameters"]
        assert parameters["type"] == "object"
        assert set(parameters.get("required", [])) <= set(parameters["properties"])
