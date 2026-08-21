"""Demo-app support: database discovery, uploads, and which key a run will use."""

from pathlib import Path

import pytest

from dbagent import demo
from dbagent.budget import BYOK_BUDGET, DEMO_BUDGET, LOCAL_BUDGET


def test_committed_demo_databases_are_present() -> None:
    keys = {db.key for db in demo.available_databases()}
    assert {"chinook", "northstar"} <= keys


def test_every_demo_database_has_suggestions() -> None:
    for database in demo.DEMO_DATABASES:
        assert database.questions, f"{database.key} has no example questions"
        assert database.blurb


def test_data_dir_honours_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUERYPILOT_DATA_DIR", str(tmp_path))
    assert demo.data_dir() == tmp_path
    assert demo.available_databases() == []


def test_find_database_by_key() -> None:
    assert demo.find_database("chinook") is not None
    assert demo.find_database("nope") is None


def test_upload_must_be_a_sqlite_file() -> None:
    with pytest.raises(ValueError, match="SQLite"):
        demo.validate_upload(b"PK\x03\x04 this is a zip")


def test_upload_size_is_capped() -> None:
    oversized = demo.SQLITE_MAGIC + b"\x00" * (demo.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(ValueError, match="MB"):
        demo.validate_upload(oversized)


def test_save_upload_sanitizes_the_filename(tmp_path: Path) -> None:
    path = demo.save_upload(demo.SQLITE_MAGIC + b"rest", "../../etc/pa ssw;rd.db", tmp_path)
    # Directory components are dropped and the stem is scrubbed: an uploaded
    # name can never steer the write out of the session directory.
    assert path.parent == tmp_path
    assert path.name == "pa_ssw_rd.sqlite"
    assert path.read_bytes().startswith(demo.SQLITE_MAGIC)


def test_pasted_key_beats_the_shared_key() -> None:
    choice = demo.resolve_key("gemini", "user-key", {"gemini": "operator-key"})
    assert (choice.source, choice.api_key) == ("byok", "user-key")
    assert demo.budget_template(choice) is BYOK_BUDGET


def test_shared_key_is_the_fallback() -> None:
    choice = demo.resolve_key("gemini", "   ", {"gemini": "operator-key"})
    assert (choice.source, choice.api_key) == ("shared", "operator-key")
    assert demo.budget_template(choice) is DEMO_BUDGET


def test_no_key_anywhere_is_unusable() -> None:
    choice = demo.resolve_key("groq", "", {})
    assert choice.source == "missing"
    assert not choice.usable


def test_ollama_never_needs_a_key() -> None:
    choice = demo.resolve_key("ollama", "", {})
    assert (choice.source, choice.usable) == ("local", True)
    assert demo.budget_template(choice) is LOCAL_BUDGET


def test_ollama_is_unusable_when_no_server_is_listening() -> None:
    """The deployed demo has no Ollama, and the old code let a visitor ask a
    question anyway: no key field is drawn for Ollama, so they got an enabled
    chat box that dialled localhost on the server."""
    choice = demo.resolve_key("ollama", "", {}, ollama=False)
    assert choice.source == "unreachable"
    assert not choice.usable
    assert "own machine" in choice.note


def test_default_provider_prefers_a_configured_shared_key() -> None:
    assert demo.default_provider({"groq": "operator-key"}, ollama=True) == "groq"


def test_default_provider_uses_ollama_when_it_is_running() -> None:
    assert demo.default_provider({}, ollama=True) == "ollama"


def test_default_provider_falls_back_to_a_hosted_provider() -> None:
    """What the deployment actually hits: no shared key, no Ollama. It must open
    on a provider whose key field tells the visitor what to do."""
    chosen = demo.default_provider({}, ollama=False)
    assert chosen != "ollama"
    assert chosen in demo.KEY_URLS


def test_default_provider_ignores_a_blank_shared_key() -> None:
    """Groq rather than Gemini: Gemini is the fallback, so a blank Gemini key
    cannot tell "ignored the blank" apart from "fell back to it"."""
    assert demo.default_provider({"groq": "   "}, ollama=False) != "groq"


def test_shared_keys_from_env_skips_blanks() -> None:
    from dbagent.config import Settings

    settings = Settings(gemini_api_key="g", groq_api_key="", openrouter_api_key="  ")
    keys = demo.shared_keys_from_env(settings)
    assert keys == {"gemini": "g"}  # empty and whitespace-only keys stay out


def test_build_client_overrides_the_configured_key() -> None:
    client = demo.build_client("groq", "pasted-key")
    assert client.provider_name == "groq"  # type: ignore[attr-defined]
    assert client.provider.api_key == "pasted-key"  # type: ignore[attr-defined]


def test_build_client_rejects_unknown_providers() -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        demo.build_client("gpt-5-imaginary")


def test_no_think_switch_is_off_by_default() -> None:
    """Measured 2026-08-20: the switch does nothing on Ollama + qwen3:4b, so we
    do not pay prompt tokens for it. See Settings.ollama_no_think."""
    assert demo.build_client("ollama").provider.system_suffix == ""  # type: ignore[attr-defined]


def test_allow_thinking_strips_a_configured_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mechanism still has to work for anyone who turns the switch on."""
    from dataclasses import replace as replace_dc

    from dbagent.config import get_providers

    providers = get_providers()
    providers["ollama"] = replace_dc(providers["ollama"], system_suffix="/no_think")
    monkeypatch.setattr(demo, "get_providers", lambda *a, **k: providers)

    assert demo.build_client("ollama").provider.system_suffix == "/no_think"  # type: ignore[attr-defined]
    kept = demo.build_client("ollama", allow_thinking=True)
    assert kept.provider.system_suffix == ""  # type: ignore[attr-defined]


def test_allow_thinking_is_a_no_op_for_hosted_providers() -> None:
    client = demo.build_client("groq", "k", allow_thinking=True)
    assert client.provider.system_suffix == ""  # type: ignore[attr-defined]
