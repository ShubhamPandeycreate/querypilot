"""The hand-rolled agent loop. No frameworks — this file IS the agent runtime.

One iteration = one LLM call. The model either calls tools (dispatched against
the ToolBelt, results appended to the transcript) or answers in text. The loop
enforces the budgets and drives self-correction:

- hard cap on LLM calls per question (default 12)
- 3 consecutive run_sql failures -> nudge to answer-or-admit-failure
- plain text without final_answer -> one nudge to use the tool, then accept
- an empty reply (no content, no tool calls) -> one retry with a bigger token
  budget, then stop; see EMPTY REPLIES below
- every step lands in the Tracer, which powers the demo's trace viewer

EMPTY REPLIES. Reasoning models spend hidden thinking tokens out of the same
max_tokens budget as their answer, and on Ollama that thinking comes back in a
separate `reasoning` field. When thinking fills the budget the reply arrives
with no content AND no tool calls. Nudging that is useless: the next reply
truncates the same way, and the episode treadmills to the call cap (41 of 55
BIRD questions stalled exactly this way). The only intervention that can help
is more room, so the loop retries once with a larger budget and then stops with
an honest message.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dbagent.agent.prompts import (
    SYSTEM_PROMPT,
    TOO_MANY_FAILURES_NUDGE,
    USE_FINAL_ANSWER_NUDGE,
)
from dbagent.agent.tools import TOOL_SCHEMAS, ToolBelt
from dbagent.llm.client import ChatClient
from dbagent.tracing.tracer import Tracer

MAX_LLM_CALLS = 12
MAX_CONSECUTIVE_SQL_FAILURES = 3
# Per-turn completion budget. Reasoning models (qwen3, gemini flash) think
# before emitting tool calls; when thinking outruns the budget the reply comes
# back truncated with NO content and NO tool calls, and the episode treadmills
# on nudges until the call cap (observed 2026-08-19 on BIRD: 41/55 questions
# stalled this way at max_tokens=2048 once a wide schema entered the
# transcript). 3584 fits a worst-case think plus late-episode prompts inside
# an 8192-token local context.
AGENT_MAX_TOKENS = 3584
# One retry at a larger budget after an empty reply. Sized to fit a late-episode
# prompt (~2150 tokens measured) plus the answer inside an 8192-token local
# context; hosted models have far more room and are unaffected.
AGENT_RETRY_MAX_TOKENS = 5120
MAX_CONSECUTIVE_EMPTY_REPLIES = 2


@dataclass
class AgentResult:
    answer_md: str
    sql: str = ""
    caveats: str = ""
    llm_calls: int = 0
    # final_answer | answered_in_text | max_llm_calls | empty_replies
    stop_reason: str = "final_answer"
    chart_paths: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class AgentLoop:
    def __init__(
        self,
        client: ChatClient,
        toolbelt: ToolBelt,
        tracer: Tracer | None = None,
        *,
        max_llm_calls: int = MAX_LLM_CALLS,
        max_consecutive_sql_failures: int = MAX_CONSECUTIVE_SQL_FAILURES,
        max_consecutive_empty_replies: int = MAX_CONSECUTIVE_EMPTY_REPLIES,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.client = client
        self.toolbelt = toolbelt
        self.tracer = tracer or Tracer(None)
        self.max_llm_calls = max_llm_calls
        self.max_consecutive_sql_failures = max_consecutive_sql_failures
        self.max_consecutive_empty_replies = max_consecutive_empty_replies
        self.on_step = on_step  # live UI hook: called with every trace event

    def _emit(self, kind: str, **data: Any) -> None:
        record = self.tracer.event(kind, **data)
        if self.on_step is not None:
            self.on_step(record)

    def run(self, question: str) -> AgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        self._emit(
            "question",
            question=question,
            provider=self.client.provider_name,
            model=self.client.model,
        )

        llm_calls = 0
        consecutive_sql_failures = 0
        consecutive_empty_replies = 0
        nudged_for_final_answer = False
        chart_paths: list[str] = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0}

        def finish(result: AgentResult) -> AgentResult:
            result.llm_calls = llm_calls
            result.chart_paths = chart_paths
            result.usage = dict(total_usage)
            self._emit(
                "final",
                stop_reason=result.stop_reason,
                llm_calls=llm_calls,
                answer_chars=len(result.answer_md),
                usage=result.usage,
            )
            return result

        while llm_calls < self.max_llm_calls:
            # An empty reply means the budget ran out mid-thought; the retry is
            # the same transcript with more room, not another nudge.
            budget = AGENT_MAX_TOKENS if not consecutive_empty_replies else AGENT_RETRY_MAX_TOKENS
            reply = self.client.chat(messages, tools=TOOL_SCHEMAS, max_tokens=budget)
            llm_calls += 1
            for key in total_usage:
                total_usage[key] += reply.usage.get(key, 0)
            self._emit(
                "llm_call",
                n=llm_calls,
                latency_s=round(reply.latency_s, 3),
                usage=reply.usage,
                n_tool_calls=len(reply.tool_calls),
                content_preview=(reply.content or "")[:200],
            )

            if not reply.has_tool_calls and not reply.content:
                # No content and no tool calls: the reply was truncated before
                # the model produced anything. Retry once with more room.
                consecutive_empty_replies += 1
                self._emit("retry", reason="empty_reply", n=consecutive_empty_replies)
                if consecutive_empty_replies >= self.max_consecutive_empty_replies:
                    return finish(
                        AgentResult(
                            answer_md=(
                                "The model returned an empty reply twice in a row, which "
                                "means it used its whole per-reply budget thinking and had "
                                "nothing left to answer with. Try a narrower question, or a "
                                "model with more room."
                            ),
                            stop_reason="empty_replies",
                            sql=self.toolbelt.last_result.sql if self.toolbelt.last_result else "",
                        )
                    )
                continue

            consecutive_empty_replies = 0

            if not reply.has_tool_calls:
                # Model answered in text. Nudge once toward final_answer; accept after.
                if reply.content and nudged_for_final_answer:
                    messages.append(reply.raw_message)
                    # Same backfill as final_answer: the model answered, so the
                    # last successful query is the SQL its answer rests on.
                    return finish(
                        AgentResult(
                            answer_md=reply.content,
                            stop_reason="answered_in_text",
                            sql=self.toolbelt.last_result.sql if self.toolbelt.last_result else "",
                        )
                    )
                messages.append(reply.raw_message)
                messages.append({"role": "user", "content": USE_FINAL_ANSWER_NUDGE})
                nudged_for_final_answer = True
                self._emit("nudge", reason="no_tool_call")
                continue

            messages.append(reply.raw_message)
            final: AgentResult | None = None

            for call in reply.tool_calls:
                arguments, parse_error = self._parse_arguments(call.arguments)
                if parse_error is not None:
                    result: dict[str, Any] = {
                        "error": {
                            "type": "bad_json",
                            "message": parse_error,
                            "hint": "Send arguments as valid JSON matching the tool schema.",
                        }
                    }
                else:
                    result = self.toolbelt.dispatch(call.name, arguments)

                ok = "error" not in result
                self._emit(
                    "tool",
                    name=call.name,
                    arguments=arguments if parse_error is None else call.arguments,
                    ok=ok,
                    error_type=None if ok else result["error"]["type"],
                    summary=_summarize(call.name, result),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

                if call.name == "run_sql":
                    consecutive_sql_failures = 0 if ok else consecutive_sql_failures + 1
                if call.name == "render_chart" and ok:
                    chart_paths.append(result["chart_path"])
                if call.name == "final_answer" and ok:
                    # Models often omit the sql argument; the toolbelt knows the
                    # last query that actually produced data — use it as fallback.
                    sql = result.get("sql", "") or (
                        self.toolbelt.last_result.sql if self.toolbelt.last_result else ""
                    )
                    final = AgentResult(
                        answer_md=result["answer_md"],
                        sql=sql,
                        caveats=result.get("caveats", ""),
                        stop_reason="final_answer",
                    )

            if final is not None:
                return finish(final)

            if consecutive_sql_failures >= self.max_consecutive_sql_failures:
                messages.append({"role": "user", "content": TOO_MANY_FAILURES_NUDGE})
                consecutive_sql_failures = 0  # the nudge resets the meter
                self._emit("nudge", reason="too_many_sql_failures")

        return finish(
            AgentResult(
                answer_md=(
                    "I could not finish within the step budget. "
                    "Partial work is in the trace; try a more specific question."
                ),
                stop_reason="max_llm_calls",
            )
        )

    @staticmethod
    def _parse_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError as error:
            return {}, f"Tool arguments were not valid JSON: {error}"
        if not isinstance(parsed, dict):
            return {}, "Tool arguments must be a JSON object."
        return parsed, None


def _summarize(tool_name: str, result: dict[str, Any]) -> str:
    """One short line per tool result — traces stay readable, prompts stay private."""
    if "error" in result:
        return f"{result['error']['type']}: {result['error']['message'][:120]}"
    if tool_name == "list_tables":
        return f"{len(result['tables'])} tables"
    if tool_name == "get_schema":
        return "schema for: " + ", ".join(t["name"] for t in result["tables"])
    if tool_name in ("sample_rows", "run_sql"):
        return f"{result['row_count']} rows" + (" (truncated)" if result["truncated"] else "")
    if tool_name == "render_chart":
        # Filename only: traces are downloadable from the public demo, and an
        # absolute path would publish the server's directory layout (and, when
        # run locally, the operator's username). Split on both separators by
        # hand rather than with pathlib: a trace recorded on Windows is often
        # read on Linux, where Path() does not treat a backslash as a separator
        # and hands back the whole path instead.
        filename = str(result["chart_path"]).replace("\\", "/").rsplit("/", 1)[-1]
        return f"chart saved: {filename}"
    if tool_name == "final_answer":
        return f"answer: {result['answer_md'][:120]}"
    return "ok"
