"""Central configuration: provider registry and settings loaded from .env."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""

    # "-latest" alias tracks the current free-tier Flash model; pin an exact
    # version (e.g. gemini-3.6-flash) when running evals so results reproduce.
    gemini_model: str = "gemini-flash-latest"
    groq_model: str = "llama-3.3-70b-versatile"
    openrouter_model: str = "openai/gpt-oss-20b:free"
    # qwen3 for reliable tool calling; qwen2.5-coder:7b narrates tool calls as
    # text instead of using the tools channel (observed 2026-08-16).
    ollama_model: str = "qwen3:4b"
    # qwen3 thinking mode costs ~6 min/question on a 6GB laptop GPU; the
    # /no_think soft switch trades some SQL quality for ~10x faster iteration.
    ollama_no_think: bool = True
    ollama_base_url: str = "http://localhost:11434/v1"


@dataclass(frozen=True)
class Provider:
    """One OpenAI-compatible backend the agent can talk to."""

    name: str
    base_url: str
    api_key: str
    model: str
    # Appended to the system message (e.g. qwen3's "/no_think" soft switch).
    system_suffix: str = ""


def get_settings() -> Settings:
    return Settings()


def get_providers(settings: Settings | None = None) -> dict[str, Provider]:
    """All configured providers, keyed by name. Providers without keys are still
    listed (Ollama needs none); callers decide what to do with missing keys."""
    s = settings or get_settings()
    return {
        "gemini": Provider(
            name="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=s.gemini_api_key,
            model=s.gemini_model,
        ),
        "groq": Provider(
            name="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=s.groq_api_key,
            model=s.groq_model,
        ),
        "openrouter": Provider(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=s.openrouter_api_key,
            model=s.openrouter_model,
        ),
        "ollama": Provider(
            name="ollama",
            base_url=s.ollama_base_url,
            api_key="ollama",  # Ollama ignores the key but the SDK requires one
            model=s.ollama_model,
            system_suffix="/no_think" if s.ollama_no_think else "",
        ),
    }
