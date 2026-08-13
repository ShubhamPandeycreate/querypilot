"""ModelClient: a thin wrapper over the openai SDK with a swappable base_url.

Phase 0 scope: plain text completion for the provider smoke test.
Phase 2 adds tool-calling, the fallback chain, and retry/backoff.
"""

from __future__ import annotations

from openai import OpenAI

from dbagent.config import Provider


class ModelClient:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self._client = OpenAI(base_url=provider.base_url, api_key=provider.api_key)

    def complete(self, prompt: str, *, max_tokens: int = 64) -> str:
        """Single-turn text completion. Raises on any API error."""
        response = self._client.chat.completions.create(
            model=self.provider.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()
