"""The Streamlit app, driven headlessly through AppTest — no browser, no network.

Every test patches `demo.build_client`, so a scripted FakeClient stands in for
the provider: these exercise the real page code (sidebar, budgets, tabs, trace)
without spending a token.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dbagent import demo
from dbagent.budget import SessionBudget
from test_loop import FakeClient, text_reply, tool_reply

APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")
ARTIST_SQL = "SELECT count(*) FROM Artist"


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's .env must never leak into a test run."""
    monkeypatch.setattr(demo, "shared_keys_from_env", dict)


def app(monkeypatch: pytest.MonkeyPatch, client: FakeClient | None = None) -> AppTest:
    if client is not None:
        monkeypatch.setattr(demo, "build_client", lambda provider, api_key="", **kwargs: client)
    return AppTest.from_file(APP, default_timeout=60)


def answers_artist_count() -> FakeClient:
    """Explore, query, answer — the shortest realistic agent episode."""
    return FakeClient(
        [
            tool_reply(("run_sql", {"sql": ARTIST_SQL})),
            tool_reply(
                (
                    "final_answer",
                    {"answer_md": "There are **275** artists.", "sql": ARTIST_SQL},
                )
            ),
        ]
    )


def text_of(at: AppTest) -> str:
    return "\n".join(element.value for element in at.markdown) + "\n".join(
        element.value for element in at.caption
    )


def test_page_loads_with_welcome_and_no_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    at = app(monkeypatch).run()
    assert not at.exception
    assert at.title[0].value == "🧭 QueryPilot"
    assert "plain English" in text_of(at)
    assert at.chat_input  # ready to take a question


def test_sidebar_lists_the_demo_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    at = app(monkeypatch).run()
    picker = at.sidebar.selectbox[0]
    labels = " | ".join(picker.options)  # options render through format_func
    assert "Chinook" in labels and "Northstar" in labels
    assert picker.options[-1] == "Upload a .sqlite file"
    assert picker.value == "chinook"


def test_defaults_to_ollama_when_no_shared_key_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    at = app(monkeypatch).run()
    assert at.sidebar.selectbox[1].value == "ollama"
    assert not at.sidebar.text_input  # no key box for a local model


def test_shared_key_selects_that_provider_and_caps_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demo, "shared_keys_from_env", lambda *a, **k: {"gemini": "operator"})
    at = app(monkeypatch).run()
    assert at.sidebar.selectbox[1].value == "gemini"
    assert "shared demo key" in "\n".join(c.value for c in at.sidebar.caption)
    assert at.session_state.budget.max_questions  # the demo cap is in force


def test_asking_a_question_renders_answer_sql_and_data(monkeypatch: pytest.MonkeyPatch) -> None:
    at = app(monkeypatch, answers_artist_count())
    at.run()
    at.chat_input[0].set_value("How many artists are there?").run()

    assert not at.exception
    assert "There are **275** artists." in text_of(at)
    assert ARTIST_SQL in [block.value for block in at.code]
    assert at.dataframe  # the result table and the trace table both rendered
    turn = at.session_state.turns[0]
    assert turn["rows"] == [(275,)]
    assert turn["stop_reason"] == "final_answer"
    assert turn["llm_calls"] == 2


def test_the_trace_tab_shows_every_step(monkeypatch: pytest.MonkeyPatch) -> None:
    at = app(monkeypatch, answers_artist_count())
    at.run()
    at.chat_input[0].set_value("How many artists are there?").run()

    kinds = [event["kind"] for event in at.session_state.turns[0]["events"]]
    assert kinds == ["question", "llm_call", "tool", "llm_call", "tool", "final"]
    assert at.download_button  # the trace is downloadable as JSONL


def test_a_failed_query_and_its_correction_both_show_in_the_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The demo's whole claim: you can watch the agent fix its own SQL."""
    client = FakeClient(
        [
            tool_reply(("run_sql", {"sql": "SELECT count(*) FROM Artists"})),  # no such table
            tool_reply(("run_sql", {"sql": ARTIST_SQL})),
            tool_reply(("final_answer", {"answer_md": "**275** artists.", "sql": ARTIST_SQL})),
        ]
    )
    at = app(monkeypatch, client)
    at.run()
    at.chat_input[0].set_value("How many artists are there?").run()

    tools = [event for event in at.session_state.turns[0]["events"] if event["kind"] == "tool"]
    assert [event["ok"] for event in tools] == [False, True, True]
    assert tools[0]["error_type"] == "sql_error"
    # The failure is visible in the rendered trace table, not swallowed.
    trace = at.session_state.turns[0]["events"]
    assert any("no such table" in str(event.get("summary", "")) for event in trace)
    assert "**275** artists." in text_of(at)


def test_example_question_buttons_run_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    at = app(monkeypatch, answers_artist_count())
    at.run()
    suggestion = next(b for b in at.button if b.label.startswith("Which 5 artists"))
    suggestion.click().run()
    assert not at.exception
    assert at.session_state.turns[0]["question"].startswith("Which 5 artists")


def test_single_shot_mode_makes_exactly_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient([text_reply(f"```sql\n{ARTIST_SQL}\n```")])
    at = app(monkeypatch, client)
    at.run()
    at.sidebar.radio[0].set_value("Single-shot").run()
    at.chat_input[0].set_value("How many artists are there?").run()

    turn = at.session_state.turns[0]
    assert (turn["mode"], turn["llm_calls"]) == ("Single-shot", 1)
    assert turn["events"] == []  # no tools, nothing to trace
    assert "**275**" in text_of(at)


def test_budget_exhaustion_stops_the_run_and_explains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo, "shared_keys_from_env", lambda *a, **k: {"gemini": "operator"})
    client = answers_artist_count()
    at = app(monkeypatch, client)
    at.session_state.budget = SessionBudget(max_questions=1, questions=1)
    at.session_state.budget_source = "shared"
    at.run()
    at.chat_input[0].set_value("How many artists are there?").run()

    assert not at.exception
    assert "allowance of 1 questions is used up" in at.error[0].value
    assert client.seen_messages == []  # the provider was never called


def test_a_provider_failure_is_a_message_not_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExplodingClient:
        provider_name = "gemini"
        model = "gemini-flash-latest"

        def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003, ANN201
            raise ConnectionError("the network is on fire")

    monkeypatch.setattr(
        demo, "build_client", lambda provider, api_key="", **kwargs: ExplodingClient()
    )
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.chat_input[0].set_value("How many artists are there?").run()

    assert not at.exception
    assert "ConnectionError: the network is on fire" in at.error[0].value


def test_clear_conversation_resets_the_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    at = app(monkeypatch, answers_artist_count())
    at.run()
    at.chat_input[0].set_value("How many artists are there?").run()
    assert at.session_state.turns

    next(b for b in at.sidebar.button if b.label == "Clear conversation").click().run()
    assert at.session_state.turns == []
    assert at.session_state.tally.llm_calls == 0
