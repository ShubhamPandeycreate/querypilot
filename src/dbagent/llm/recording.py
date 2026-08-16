"""Golden-trace record/replay for the LLM client.

RecordingClient wraps a live client and saves every LLMReply to a JSON file.
ReplayClient serves those replies back in order — so CI can re-run a whole
agent session deterministically with no API keys and no network, while the
real ToolBelt and guard still execute for real.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dbagent.llm.client import ChatClient, LLMReply, ToolCall


class RecordingClient:
    def __init__(self, inner: ChatClient, path: str | Path) -> None:
        self.inner = inner
        self.path = Path(path)
        self.provider_name = inner.provider_name
        self.model = inner.model
        self._exchanges: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMReply:
        reply = self.inner.chat(messages, tools=tools, max_tokens=max_tokens)
        self._exchanges.append({"n_messages": len(messages), "reply": asdict(reply)})
        return reply

    def save(self, meta: dict[str, Any] | None = None) -> Path:
        payload = {
            "provider": self.provider_name,
            "model": self.model,
            "meta": meta or {},
            "exchanges": self._exchanges,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.path


class ReplayClient:
    def __init__(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.provider_name = f"replay:{payload['provider']}"
        self.model = payload["model"]
        self._pending: list[dict[str, Any]] = list(payload["exchanges"])

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMReply:
        if not self._pending:
            raise RuntimeError(
                "Replay exhausted: the agent made more LLM calls than the recording has. "
                "Behavior diverged from the golden trace."
            )
        exchange = self._pending.pop(0)
        recorded = exchange["reply"]
        return LLMReply(
            content=recorded["content"],
            tool_calls=[ToolCall(**c) for c in recorded["tool_calls"]],
            raw_message=recorded["raw_message"],
            usage=recorded.get("usage", {}),
            latency_s=0.0,
        )

    @property
    def exhausted(self) -> bool:
        return not self._pending
