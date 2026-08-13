"""Phase 0 sanity tests: the package imports and config loads without a .env file."""

import dbagent
from dbagent.config import Settings, get_providers


def test_version() -> None:
    assert dbagent.__version__


def test_providers_registry() -> None:
    providers = get_providers(Settings(_env_file=None))
    assert set(providers) == {"gemini", "groq", "openrouter", "ollama"}
    for provider in providers.values():
        assert provider.base_url.startswith("http")
        assert provider.model


def test_ollama_needs_no_key() -> None:
    providers = get_providers(Settings(_env_file=None))
    assert providers["ollama"].api_key
