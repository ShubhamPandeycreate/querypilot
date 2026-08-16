"""ModelClient: one OpenAI-compatible client for every backend.

The agent loop never touches SDK types: chat() returns an LLMReply, so the
loop can be driven identically by a live provider, a replay file, or a fake
in tests. Transient failures (rate limits, connection blips, 5xx) retry with
exponential backoff via tenacity.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dbagent.config import Provider

RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_think(text: str | None) -> str | None:
    """Some local reasoning models leak <think>...</think> into content."""
    if not text:
        return text
    return _THINK_BLOCK.sub("", text).strip() or None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as sent by the model


@dataclass(frozen=True)
class LLMReply:
    content: str | None
    tool_calls: list[ToolCall]
    raw_message: dict[str, Any]  # serializable assistant message, for the transcript
    usage: dict[str, int] = field(default_factory=dict)
    latency_s: float = 0.0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class ChatClient(Protocol):
    """What the agent loop needs: live client, replay client and fakes match this."""

    provider_name: str
    model: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMReply: ...


class ModelClient:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.provider_name = provider.name
        self.model = provider.model
        self._client = OpenAI(base_url=provider.base_url, api_key=provider.api_key)

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,  # low: we want format-compliant tool calls and stable SQL
    ) -> LLMReply:
        start = time.perf_counter()
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools or openai.NOT_GIVEN,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
        )
        latency = time.perf_counter() - start
        message = response.choices[0].message
        content = _strip_think(message.content)
        raw_message = message.model_dump(exclude_none=True)
        if raw_message.get("content"):
            raw_message["content"] = content
        # Never echo reasoning back into the transcript.
        raw_message.pop("reasoning", None)
        raw_message.pop("reasoning_content", None)
        tool_calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "{}")
            for c in (message.tool_calls or [])
            if c.type == "function"
        ]
        usage: dict[str, int] = {}
        if response.usage is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
        return LLMReply(
            content=content,
            tool_calls=tool_calls,
            raw_message=raw_message,
            usage=usage,
            latency_s=latency,
        )

    # Reasoning models spend "thinking" tokens out of max_tokens before any
    # visible text; a small cap yields empty replies — keep headroom.
    def complete(self, prompt: str, *, max_tokens: int = 512) -> str:
        reply = self.chat([{"role": "user", "content": prompt}], max_tokens=max_tokens)
        return (reply.content or "").strip()
