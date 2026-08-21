"""QueryPilot — the public demo.

Deliberately a thin presentation layer: database discovery, key resolution and
spend caps live in `dbagent.demo` / `dbagent.budget` so they are covered by
pytest and mypy. What this file owns is the one thing a screenshot cannot show
— the agent's steps arriving live while it works.

Run locally:  uv run streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

# A bare `git clone` should run without `pip install -e .` first (Streamlit
# Community Cloud installs requirements.txt, not the project itself).
ROOT = Path(__file__).resolve().parent.parent
for _candidate in (ROOT / "src", ROOT):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dbagent import demo  # noqa: E402
from dbagent.agent.loop import AgentLoop  # noqa: E402
from dbagent.agent.single_shot import answer_question  # noqa: E402
from dbagent.agent.tools import ToolBelt  # noqa: E402
from dbagent.budget import (  # noqa: E402
    BudgetedClient,
    BudgetExceeded,
    SessionBudget,
    SharedKeyLimiter,
    UsageTally,
    new_budget,
)
from dbagent.db.database import Database  # noqa: E402
from dbagent.tracing.tracer import Tracer  # noqa: E402

REPO_URL = "https://github.com/ShubhamPandeycreate/querypilot"
REPORT_URL = f"{REPO_URL}/blob/main/evals/reports/baseline.md"
SESSION_ROOT = Path(tempfile.gettempdir()) / "querypilot_session"
# One process serves every visitor, so this window is genuinely shared.
SHARED_KEY_CALLS_PER_HOUR = 60

AGENT = "Agent"
SINGLE_SHOT = "Single-shot"

st.set_page_config(
    page_title="QueryPilot — ask your database",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --- session state --------------------------------------------------------


@st.cache_resource
def shared_limiter() -> SharedKeyLimiter:
    """Process-wide, so every session shares one hourly window on the demo key."""
    return SharedKeyLimiter(SHARED_KEY_CALLS_PER_HOUR)


def init_state() -> None:
    state = st.session_state
    state.setdefault("turns", [])
    state.setdefault("tally", UsageTally())
    state.setdefault("budget", None)
    state.setdefault("budget_source", None)
    state.setdefault("uploaded_db", None)
    state.setdefault("pending_question", None)
    state.setdefault("session_id", uuid4().hex)


def session_dir() -> Path:
    """This visitor's own scratch space for uploads and rendered charts.

    One process serves everyone, so a flat directory would let two visitors who
    upload the same filename overwrite each other's database.
    """
    return SESSION_ROOT / st.session_state.session_id


def budget_for(choice: demo.KeyChoice) -> SessionBudget:
    """One budget per key source; switching keys starts a fresh allowance."""
    state = st.session_state
    if state.budget is None or state.budget_source != choice.source:
        state.budget = new_budget(demo.budget_template(choice))
        state.budget_source = choice.source
    return state.budget


def shared_keys() -> dict[str, str]:
    """Operator keys: .env for local runs, st.secrets when deployed."""
    keys = demo.shared_keys_from_env()
    for provider in ("gemini", "groq", "openrouter"):
        try:
            value = str(st.secrets.get(f"{provider}_api_key", "") or "").strip()
        except Exception:  # no secrets.toml configured — normal for a local clone
            break
        if value:
            keys[provider] = value
    return keys


# --- sidebar --------------------------------------------------------------


def sidebar() -> dict[str, Any]:
    """Draw the controls and return the configuration for the next question."""
    state = st.session_state
    available = demo.available_databases()

    with st.sidebar:
        st.subheader("Data")
        options = [db.key for db in available] + ["__upload__"]
        labels = {db.key: db.label for db in available} | {"__upload__": "Upload a .sqlite file"}
        selected = st.selectbox(
            "Database", options, format_func=lambda key: labels[key], label_visibility="collapsed"
        )

        db_path: Path | None = None
        db_label = ""
        suggestions: tuple[str, ...] = ()
        if selected == "__upload__":
            upload = st.file_uploader(
                "SQLite file",
                type=["sqlite", "db", "sqlite3"],
                help=f"Read-only, up to {demo.MAX_UPLOAD_BYTES // 1_000_000} MB. "
                "It stays on this server only for the session.",
            )
            if upload is not None:
                try:
                    state.uploaded_db = str(
                        demo.save_upload(upload.getvalue(), upload.name, session_dir())
                    )
                except ValueError as error:
                    st.error(str(error))
                    state.uploaded_db = None
            if state.uploaded_db:
                db_path = Path(state.uploaded_db)
                db_label = db_path.stem
                st.caption(f"Using **{db_path.name}** — queries stay read-only.")
        else:
            chosen = demo.find_database(selected)
            if chosen is not None:
                db_path, db_label, suggestions = chosen.path, chosen.label, chosen.questions
                st.caption(chosen.blurb)

        st.subheader("Model")
        keys = shared_keys()
        ollama = demo.ollama_is_running()
        providers = list(demo.PROVIDER_LABELS)
        default_provider = demo.default_provider(keys, ollama=ollama)
        provider = st.selectbox(
            "Provider",
            providers,
            index=providers.index(default_provider),
            format_func=lambda name: demo.PROVIDER_LABELS[name],
            label_visibility="collapsed",
        )
        user_key = ""
        if provider != "ollama":
            user_key = st.text_input(
                "Your API key",
                type="password",
                placeholder="paste to use your own quota",
                help=f"Free key: {demo.KEY_URLS[provider]} — kept in this session only, "
                "never written to disk or into traces.",
            )
        choice = demo.resolve_key(provider, user_key, keys, ollama=ollama)
        st.caption(f"`{demo.model_name(provider)}` — {choice.note}")

        st.subheader("Mode")
        mode = st.radio(
            "Mode",
            [AGENT, SINGLE_SHOT],
            label_visibility="collapsed",
            help="Agent: explores the schema with tools and retries its own failures. "
            "Single-shot: whole schema in one prompt, one query, no second chance — "
            "the Phase 3 baseline.",
        )

        budget = budget_for(choice)
        if budget.is_capped:
            st.subheader("Session allowance")
            for label, used, cap in budget.meters():
                st.progress(min(used / cap, 1.0), text=f"{label}: {used:,} / {cap:,}")
            if choice.source == "shared":
                st.caption(
                    f"Shared key: {shared_limiter().used()}/{SHARED_KEY_CALLS_PER_HOUR} calls "
                    "used this hour across all visitors. Paste your own key to lift the caps."
                )

        tally = state.tally
        if tally.llm_calls:
            st.caption(
                f"This session: {tally.llm_calls} model calls · "
                f"{tally.total_tokens:,} tokens · {tally.seconds:.0f}s"
            )
        if st.button("Clear conversation", width="stretch"):
            state.turns = []
            state.tally = UsageTally()
            st.rerun()

        st.divider()
        st.caption(
            f"[Source]({REPO_URL}) · [Benchmark report]({REPORT_URL}) · "
            "hand-rolled agent loop, no agent frameworks."
        )

    return {
        "db_path": db_path,
        "db_label": db_label,
        "suggestions": suggestions,
        "provider": provider,
        "choice": choice,
        "mode": mode,
        "budget": budget,
    }


# --- rendering ------------------------------------------------------------

STEP_ICONS = {"llm_call": "🧠", "tool": "🔧", "nudge": "↩️"}


def format_step(event: dict[str, Any]) -> str:
    kind = event["kind"]
    if kind == "llm_call":
        usage = event.get("usage") or {}
        total = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        return f"🧠 model call #{event['n']} — {event['latency_s']}s, {total:,} tokens"
    if kind == "tool":
        icon = "🔧" if event["ok"] else "⚠️"
        return f"{icon} `{event['name']}` — {event['summary']}"
    if kind == "nudge":
        return f"↩️ nudge — {event['reason'].replace('_', ' ')}"
    if kind == "retry":
        return "🔁 empty reply — retrying with a larger token budget"
    return ""


def to_frame(columns: list[str], rows: list[Any]) -> pd.DataFrame:
    """Arrow chokes on bytes, mixed types and duplicate column names — a join
    like `SELECT a.Name, b.Name` produces the last one routinely."""

    def cell(value: Any) -> Any:
        if isinstance(value, bytes):
            return f"<blob {len(value)} bytes>"
        if value is None or isinstance(value, int | float | str | bool):
            return value
        return str(value)

    seen: dict[str, int] = {}
    labels = []
    for column in columns:
        seen[column] = seen.get(column, 0) + 1
        labels.append(column if seen[column] == 1 else f"{column} ({seen[column]})")
    return pd.DataFrame([[cell(v) for v in row] for row in rows], columns=labels)


def trace_frame(events: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for event in events:
        detail = format_step(event)
        if not detail:
            continue
        rows.append({"step": event["step"], "event": event["kind"], "detail": detail})
    return pd.DataFrame(rows)


def render_turn(turn: dict[str, Any], *, index: int) -> None:
    with st.chat_message("user"):
        st.write(turn["question"])
    with st.chat_message("assistant"):
        render_answer(turn, index=index)


def render_answer(turn: dict[str, Any], *, index: int) -> None:
    if turn.get("error"):
        st.error(turn["error"])
    if turn.get("answer_md"):
        st.markdown(turn["answer_md"])
    if turn.get("caveats"):
        st.caption(f"⚠️ {turn['caveats']}")
    if turn.get("stop_reason") == "empty_replies":
        st.warning(
            "The model ran out of room to answer: it spent its whole per-reply budget "
            "thinking. A narrower question, or a hosted model, usually gets through."
        )
    if turn.get("stop_reason") == "max_llm_calls":
        st.warning(
            "The agent hit its step budget before finishing — the trace shows how far it got."
        )

    sql_tab, data_tab, chart_tab, trace_tab = st.tabs(["SQL", "Data", "Chart", "Trace"])
    with sql_tab:
        if turn.get("sql"):
            st.code(turn["sql"], language="sql")
            st.caption("Executed read-only, through the sqlglot guard, with a row cap.")
        else:
            st.caption("No SQL was executed for this answer.")
    with data_tab:
        if turn.get("columns"):
            frame = to_frame(turn["columns"], turn["rows"])
            st.dataframe(frame, width="stretch", hide_index=True)
            st.caption(f"{len(frame):,} rows returned by the final query.")
        else:
            st.caption("No result set — the answer came from the model's text.")
    with chart_tab:
        charts = [path for path in turn.get("charts", []) if Path(path).exists()]
        if charts:
            for path in charts:
                st.image(path)
        else:
            st.caption("No chart for this answer. Ask for one — 'chart it' works.")
    with trace_tab:
        events = turn.get("events", [])
        if events:
            st.dataframe(trace_frame(events), width="stretch", hide_index=True)
            with st.expander("Raw trace events"):
                st.json(events, expanded=False)
            st.download_button(
                "Download trace (JSONL)",
                "\n".join(json.dumps(event, default=str) for event in events),
                file_name=f"querypilot_trace_{index}.jsonl",
                mime="application/jsonl",
                key=f"trace_download_{index}",
            )
        else:
            st.caption("Single-shot mode makes one call with no tools — nothing to trace.")

    usage = turn.get("usage", {})
    st.caption(
        f"{turn['mode']} · `{turn['model']}` ({turn['provider']}) · "
        f"{turn['llm_calls']} model call(s) · "
        f"{usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0):,} tokens · "
        f"{turn['seconds']:.1f}s"
    )


def welcome(config: dict[str, Any]) -> None:
    st.markdown(
        "#### Ask a question in plain English\n"
        "QueryPilot reads the schema, writes SQL, runs it **read-only**, fixes its own "
        "errors when a query fails, and shows you every step it took."
    )
    if config["suggestions"]:
        columns = st.columns(len(config["suggestions"]))
        for column, question in zip(columns, config["suggestions"], strict=False):
            if column.button(question, width="stretch", key=f"suggest_{hash(question)}"):
                st.session_state.pending_question = question
    with st.expander("How it works"):
        st.markdown(
            "- **Six tools**: `list_tables`, `get_schema`, `sample_rows`, `run_sql`, "
            "`render_chart`, `final_answer`. The loop is hand-written — no agent framework.\n"
            "- **Guarded SQL**: every statement is parsed with sqlglot; anything that is not a "
            "single read-only `SELECT` is rejected, a `LIMIT` is injected, and the connection "
            "itself is opened read-only with a query timeout.\n"
            "- **Self-correction**: a failed query comes back to the model as a typed error with "
            "a hint, and it rewrites — that behaviour is what the *Agent* mode adds over "
            f"*Single-shot*. [The benchmark report]({REPORT_URL}) measures the difference.\n"
            "- **Traces**: every step is recorded as JSONL — the Trace tab is that file, and the "
            "same fixtures replay in CI as regression tests."
        )


# --- running a question ---------------------------------------------------


def run_question(config: dict[str, Any], question: str) -> dict[str, Any]:
    """Execute one question and return a fully-rendered turn record."""
    budget: SessionBudget = config["budget"]
    choice: demo.KeyChoice = config["choice"]
    turn: dict[str, Any] = {
        "question": question,
        "mode": config["mode"],
        "provider": config["provider"],
        "model": demo.model_name(config["provider"]),
        "answer_md": "",
        "sql": "",
        "caveats": "",
        "columns": [],
        "rows": [],
        "charts": [],
        "events": [],
        "llm_calls": 0,
        "usage": {},
        "seconds": 0.0,
        "stop_reason": "",
        "error": "",
    }

    status = st.status("Working…", expanded=True)
    started = time.perf_counter()
    database: Database | None = None
    try:
        budget.start_question()
        client = BudgetedClient(
            demo.build_client(
                config["provider"],
                choice.api_key,
                allow_thinking=config["mode"] == SINGLE_SHOT,
            ),
            budget,
            shared_limiter() if choice.source == "shared" else None,
        )
        database = Database(config["db_path"])

        if config["mode"] == SINGLE_SHOT:
            status.write("🧠 one call, whole schema, no tools")
            result = answer_question(client, database, question)
            turn["answer_md"] = result.answer_md
            turn["sql"] = result.sql
            turn["caveats"] = result.caveats
            turn["llm_calls"] = result.llm_calls
            turn["usage"] = result.usage
            turn["stop_reason"] = result.stop_reason
            if result.error:
                turn["error"] = f"The single-shot query failed: {result.error}"
            if result.result is not None:
                turn["columns"] = result.result.columns
                turn["rows"] = result.result.rows
        else:
            belt = ToolBelt(database, charts_dir=session_dir() / "charts")
            events: list[dict[str, Any]] = []

            def on_step(event: dict[str, Any]) -> None:
                events.append(event)
                line = format_step(event)
                if line:
                    status.write(line)

            loop = AgentLoop(client, belt, Tracer(None), on_step=on_step)
            result = loop.run(question)
            turn["answer_md"] = result.answer_md
            turn["sql"] = result.sql
            turn["caveats"] = result.caveats
            turn["llm_calls"] = result.llm_calls
            turn["usage"] = result.usage
            turn["stop_reason"] = result.stop_reason
            turn["charts"] = [str(path) for path in result.chart_paths]
            turn["events"] = events
            if belt.last_result is not None:
                turn["columns"] = belt.last_result.columns
                turn["rows"] = belt.last_result.rows
    except BudgetExceeded as error:
        turn["error"] = error.message
        turn["answer_md"] = ""
        status.update(label="Stopped: session allowance reached", state="error", expanded=False)
    except Exception as error:  # provider/network failures: a message, not a traceback
        turn["error"] = f"{type(error).__name__}: {str(error)[:400]}"
        status.update(label="Failed", state="error", expanded=False)
    else:
        label = f"Done — {turn['llm_calls']} model call(s)"
        status.update(label=label, state="complete", expanded=False)
    finally:
        if database is not None:
            database.close()

    turn["seconds"] = round(time.perf_counter() - started, 2)
    st.session_state.tally.add(
        llm_calls=turn["llm_calls"], usage=turn["usage"], seconds=turn["seconds"]
    )
    return turn


# --- page -----------------------------------------------------------------


def main() -> None:
    init_state()
    config = sidebar()

    st.title("🧭 QueryPilot")
    st.caption(
        "A data-analyst agent over SQL databases — schema exploration, guarded read-only "
        "SQL, error-driven self-correction, and charts."
    )

    for index, turn in enumerate(st.session_state.turns):
        render_turn(turn, index=index)

    if not st.session_state.turns:
        welcome(config)

    if config["db_path"] is None:
        st.info("Pick a database in the sidebar, or upload a .sqlite file, to get started.")
        return

    asked = st.chat_input(
        f"Ask about {config['db_label'] or 'this database'}…",
        disabled=not config["choice"].usable,
    )
    if not config["choice"].usable:
        if config["choice"].source == "unreachable":
            # No key to ask for — the note explains what this option is actually for.
            st.info(config["choice"].note)
        else:
            key_url = demo.KEY_URLS.get(config["provider"], "")
            st.info(
                f"No shared key is configured for {demo.PROVIDER_LABELS[config['provider']]}. "
                f"Paste your own free key in the sidebar — get one at {key_url}"
            )
    pending = st.session_state.pending_question
    st.session_state.pending_question = None
    question = asked or pending
    if question:
        with st.chat_message("user"):
            st.write(question)
        with st.chat_message("assistant"):
            turn = run_question(config, question)
            render_answer(turn, index=len(st.session_state.turns))
        st.session_state.turns.append(turn)
        # The sidebar drew its allowance meters before this question ran; rerun
        # so they show what it actually cost.
        st.rerun()


main()
