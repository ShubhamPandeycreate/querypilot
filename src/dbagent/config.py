"""Central configuration: provider registry and settings loaded from .env."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""

    gemini_model: str = "gemini-2.5-flash"
    groq_model: str = "llama-3.3-70b-versatile"
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    ollama_model: str = "qwen2.5-coder:7b"
    ollama_base_url: str = "http://localhost:11434/v1"


@dataclass(frozen=True)
class Provider:
    """One OpenAI-compatible backend the agent can talk to."""

    name: str
    base_url: str
    api_key: str
    model: str


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
        ),
    }
