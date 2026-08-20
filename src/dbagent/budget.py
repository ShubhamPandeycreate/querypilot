"""Spend caps for the public demo.

A hosted demo that talks to a paid-ish API has two distinct exposures, and they
need two different mechanisms:

- One visitor burning the shared key in a single session -> `SessionBudget`,
  enforced per browser session by `BudgetedClient` wrapping the model client.
- Many visitors arriving at once -> `SharedKeyLimiter`, a process-wide sliding
  window shared by every session in the container.

Both fail closed with `BudgetExceeded`, which the app turns into a "bring your
own key" prompt rather than a stack trace.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from dbagent.llm.client import ChatClient, LLMReply

UNLIMITED = 0


class BudgetExceeded(RuntimeError):
    """A cap was hit. `limit` names which one, for a targeted UI message."""

    def __init__(self, limit: str, message: str) -> None:
        super().__init__(message)
        self.limit = limit
        self.message = message


@dataclass
class SessionBudget:
    """Caps for one browser session. 0 means "no cap" on that dimension.

    Checked *before* each call and updated after, so a session can never start
    a question it has no budget to finish paying for.
    """

    max_questions: int = UNLIMITED
    max_llm_calls: int = UNLIMITED
    max_tokens: int = UNLIMITED
    questions: int = 0
    llm_calls: int = 0
    tokens: int = 0

    def start_question(self) -> None:
        """Call once per user question, before the first LLM call."""
        if self.max_questions and self.questions >= self.max_questions:
            raise BudgetExceeded(
                "questions",
                f"This session's demo allowance of {self.max_questions} questions is used up.",
            )
        self.check_call()
        self.questions += 1

    def check_call(self) -> None:
        if self.max_llm_calls and self.llm_calls >= self.max_llm_calls:
            raise BudgetExceeded(
                "llm_calls",
                f"This session's demo allowance of {self.max_llm_calls} model calls is used up.",
            )
        if self.max_tokens and self.tokens >= self.max_tokens:
            raise BudgetExceeded(
                "tokens",
                f"This session's demo allowance of {self.max_tokens:,} tokens is used up.",
            )

    def record_call(self, usage: dict[str, int] | None = None) -> None:
        self.llm_calls += 1
        used = usage or {}
        self.tokens += used.get("prompt_tokens", 0) + used.get("completion_tokens", 0)

    def meters(self) -> list[tuple[str, int, int]]:
        """(label, used, cap) for every capped dimension — the sidebar meters."""
        rows = [
            ("questions", self.questions, self.max_questions),
            ("model calls", self.llm_calls, self.max_llm_calls),
            ("tokens", self.tokens, self.max_tokens),
        ]
        return [(label, used, cap) for label, used, cap in rows if cap]

    @property
    def is_capped(self) -> bool:
        return bool(self.max_questions or self.max_llm_calls or self.max_tokens)


# A visitor should be able to see self-correction happen (that needs a handful
# of tool calls) without being able to drain the operator's daily quota.
DEMO_BUDGET = SessionBudget(max_questions=8, max_llm_calls=40, max_tokens=120_000)
# Your key, your problem — but still stop a runaway loop from billing all night.
BYOK_BUDGET = SessionBudget(max_questions=UNLIMITED, max_llm_calls=400, max_tokens=2_000_000)
LOCAL_BUDGET = SessionBudget()  # Ollama on your own machine: nothing to protect


def new_budget(template: SessionBudget) -> SessionBudget:
    """A fresh, zeroed copy of a budget template (never share mutable state)."""
    return SessionBudget(
        max_questions=template.max_questions,
        max_llm_calls=template.max_llm_calls,
        max_tokens=template.max_tokens,
    )


class SharedKeyLimiter:
    """Process-wide sliding window over the shared demo key.

    Streamlit serves every session from one process, so a plain lock plus a
    deque of timestamps is enough — no external store, no extra service.
    """

    def __init__(self, max_calls: int, window_seconds: float = 3600.0) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0] > self.window_seconds:
            self._calls.popleft()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            if len(self._calls) >= self.max_calls:
                wait_minutes = int((self.window_seconds - (now - self._calls[0])) // 60) + 1
                raise BudgetExceeded(
                    "shared_key",
                    f"The shared demo key is at its hourly limit ({self.max_calls} calls). "
                    f"Try again in ~{wait_minutes} min, or paste your own key in the sidebar.",
                )
            self._calls.append(now)

    def used(self) -> int:
        with self._lock:
            self._prune(time.monotonic())
            return len(self._calls)


class BudgetedClient:
    """ChatClient wrapper that meters every call against a budget.

    Sits between the agent loop and the provider, so the loop stays unaware of
    billing — same trick the eval harness uses for rate limiting.
    """

    def __init__(
        self,
        inner: ChatClient,
        budget: SessionBudget,
        limiter: SharedKeyLimiter | None = None,
    ) -> None:
        self.inner = inner
        self.budget = budget
        self.limiter = limiter
        self.provider_name = inner.provider_name
        self.model = inner.model

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMReply:
        self.budget.check_call()
        if self.limiter is not None:
            self.limiter.acquire()
        reply = self.inner.chat(messages, **kwargs)
        self.budget.record_call(reply.usage)
        return reply


@dataclass
class UsageTally:
    """Running totals shown in the app footer (independent of any cap)."""

    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    charges: list[str] = field(default_factory=list)

    def add(self, *, llm_calls: int, usage: dict[str, int], seconds: float) -> None:
        self.llm_calls += llm_calls
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.seconds += seconds

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens
