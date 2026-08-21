"""Everything the demo app needs that is worth testing without a browser.

The Streamlit script stays presentational; database discovery, key resolution
and client construction live here so they run under pytest and mypy like the
rest of the package.
"""

from __future__ import annotations

import os
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from dbagent.budget import BYOK_BUDGET, DEMO_BUDGET, LOCAL_BUDGET, SessionBudget
from dbagent.config import Settings, get_providers, get_settings
from dbagent.llm.client import ChatClient, ModelClient

# --- demo databases -------------------------------------------------------


@dataclass(frozen=True)
class DemoDatabase:
    key: str
    label: str
    filename: str
    blurb: str
    questions: tuple[str, ...]

    @property
    def path(self) -> Path:
        return data_dir() / self.filename

    @property
    def exists(self) -> bool:
        return self.path.exists()


DEMO_DATABASES: tuple[DemoDatabase, ...] = (
    DemoDatabase(
        key="chinook",
        label="Chinook — digital music store",
        filename="chinook.sqlite",
        blurb="11 tables: invoices, tracks, albums, artists, employees. The classic SQL "
        "teaching database — familiar joins, real dates and currencies.",
        questions=(
            "Which 5 artists generated the most revenue? Chart it.",
            "What is the average invoice total per country, for the top 10 countries?",
            "Which genre sells best in Germany?",
            "How many tracks have never appeared on an invoice?",
        ),
    ),
    DemoDatabase(
        key="northstar",
        label="Northstar Retail — synthetic orders",
        filename="northstar.sqlite",
        blurb="Generated for this demo, so no model has memorised it: customers, products, "
        "orders, order items and returns, with the messy value formats real data has.",
        questions=(
            "Which product category had the highest revenue in 2025? Chart it.",
            "What share of orders were cancelled, by sales channel?",
            "Which five customers returned the most items, and why?",
            "How did monthly revenue trend across 2025?",
        ),
    ),
)


def data_dir() -> Path:
    """Where demo .sqlite files live: env override, cwd/data, then repo layout."""
    override = os.environ.get("QUERYPILOT_DATA_DIR")
    if override:
        return Path(override)
    cwd_data = Path.cwd() / "data"
    if cwd_data.is_dir():
        return cwd_data
    return Path(__file__).resolve().parents[2] / "data"


def available_databases() -> list[DemoDatabase]:
    """Only the ones actually on disk — a clone without northstar.sqlite still runs."""
    return [db for db in DEMO_DATABASES if db.exists]


def find_database(key: str) -> DemoDatabase | None:
    return next((db for db in DEMO_DATABASES if db.key == key), None)


# --- uploaded databases ---------------------------------------------------

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SQLITE_MAGIC = b"SQLite format 3\x00"
_SAFE_STEM = re.compile(r"[^A-Za-z0-9_-]+")


def validate_upload(data: bytes) -> None:
    """Reject anything that is not a plain SQLite file of workable size."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"That file is {len(data) / 1e6:.1f} MB; the demo accepts up to "
            f"{MAX_UPLOAD_BYTES // 1_000_000} MB."
        )
    if not data.startswith(SQLITE_MAGIC):
        raise ValueError("That does not look like a SQLite database file.")


def save_upload(data: bytes, filename: str, dest_dir: str | Path) -> Path:
    """Validate and write an uploaded database under a sanitized name."""
    validate_upload(data)
    stem = _SAFE_STEM.sub("_", Path(filename).stem)[:48] or "uploaded"
    target = Path(dest_dir) / f"{stem}.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


# --- providers and keys ---------------------------------------------------

PROVIDER_LABELS: dict[str, str] = {
    "gemini": "Gemini (Google AI Studio free tier)",
    "groq": "Groq (free tier)",
    "openrouter": "OpenRouter (:free models)",
    "ollama": "Ollama (local, runs on your own machine)",
}

KEY_URLS: dict[str, str] = {
    "gemini": "https://aistudio.google.com/apikey",
    "groq": "https://console.groq.com/keys",
    "openrouter": "https://openrouter.ai/settings/keys",
}


@dataclass(frozen=True)
class KeyChoice:
    """Which key a run will use, and therefore which budget applies."""

    provider: str
    api_key: str
    source: str  # byok | shared | local | missing

    @property
    def usable(self) -> bool:
        return self.source not in ("missing", "unreachable")

    @property
    def note(self) -> str:
        return {
            "byok": "Using your key. It is held in memory for this session only, and is never "
            "written to disk or into a trace.",
            "shared": "Using the shared demo key, which is capped per session.",
            "local": "Talking to Ollama on this machine. No key, no quota.",
            "missing": "Add a key in the sidebar to run a question.",
            "unreachable": "No Ollama server is reachable from here — that option is for "
            "running the repo on your own machine. Pick a hosted provider and paste a free key.",
        }[self.source]


def ollama_is_running(timeout: float = 0.4) -> bool:
    """Whether an Ollama server is actually listening at the configured URL.

    The deployed demo has no Ollama, and the failure is silent in an unhelpful
    way: the key field hides itself for a provider that needs no key, so a
    visitor gets an enabled chat box that dials localhost on the server.

    Deliberately not cached. Nothing listening gives an immediate refusal rather
    than a timeout, so this costs a syscall per rerun, and probing live means a
    local Ollama started after the app is picked up without a restart.
    """
    url = urlparse(get_settings().ollama_base_url)
    port = url.port or (443 if url.scheme == "https" else 80)
    try:
        with socket.create_connection((url.hostname or "localhost", port), timeout=timeout):
            return True
    except OSError:
        return False


def default_provider(shared_keys: Mapping[str, str] | None = None, *, ollama: bool = True) -> str:
    """Which provider the sidebar opens on.

    An operator's shared key wins, since configuring one is a statement of
    intent. Otherwise prefer a local Ollama when one is genuinely there, and
    fall back to the first hosted provider, whose key field is the instruction.
    """
    keys = shared_keys or {}
    for name in PROVIDER_LABELS:
        if keys.get(name, "").strip():
            return name
    if ollama:
        return "ollama"
    return next(name for name in PROVIDER_LABELS if name != "ollama")


def resolve_key(
    provider: str,
    user_key: str = "",
    shared_keys: Mapping[str, str] | None = None,
    *,
    ollama: bool = True,
) -> KeyChoice:
    """A pasted key always wins; the operator's shared key is the fallback."""
    if provider == "ollama":
        return KeyChoice(provider, "", "local" if ollama else "unreachable")
    if user_key.strip():
        return KeyChoice(provider, user_key.strip(), "byok")
    shared = (shared_keys or {}).get(provider, "").strip()
    if shared:
        return KeyChoice(provider, shared, "shared")
    return KeyChoice(provider, "", "missing")


def budget_template(choice: KeyChoice) -> SessionBudget:
    return {"shared": DEMO_BUDGET, "byok": BYOK_BUDGET}.get(choice.source, LOCAL_BUDGET)


def shared_keys_from_env(settings: Settings | None = None) -> dict[str, str]:
    """Operator keys from .env — Streamlit secrets are merged over these in the app."""
    s = settings or get_settings()
    return {
        provider: key
        for provider, key in (
            ("gemini", s.gemini_api_key),
            ("groq", s.groq_api_key),
            ("openrouter", s.openrouter_api_key),
        )
        if key.strip()
    }


def model_name(provider: str) -> str:
    return get_providers()[provider].model


def build_client(provider: str, api_key: str = "", *, allow_thinking: bool = False) -> ChatClient:
    """The app's single door to the network — patched out in tests.

    `allow_thinking` drops the local model's "/no_think" switch, which matters
    only when that switch is enabled: single-shot needs thinking on, because
    qwen3:4b with it suppressed degenerates into endless non-SQL output (0 SQL
    in 8 trials, 2026-08-19). NB the switch is off by default because it was
    measured on 2026-08-20 to have no effect at all on Ollama 0.32.13 — see
    Settings.ollama_no_think.
    """
    providers = get_providers()
    if provider not in providers:
        raise ValueError(f"Unknown provider {provider!r}. Choose: {sorted(providers)}")
    chosen = providers[provider]
    if api_key:
        chosen = replace(chosen, api_key=api_key)
    if allow_thinking and chosen.system_suffix:
        chosen = replace(chosen, system_suffix="")
    return ModelClient(chosen)
