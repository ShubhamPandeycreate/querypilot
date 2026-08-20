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
