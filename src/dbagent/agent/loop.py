"""The hand-rolled agent loop. No frameworks — this file IS the agent runtime.

One iteration = one LLM call. The model either calls tools (dispatched against
the ToolBelt, results appended to the transcript) or answers in text. The loop
enforces the budgets and drives self-correction:

- hard cap on LLM calls per question (default 12)
- 3 consecutive run_sql failures -> nudge to answer-or-admit-failure
- plain text without final_answer -> one nudge to use the tool, then accept
- every step lands in the Tracer, which powers the demo's trace viewer
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


@dataclass
class AgentResult:
    answer_md: str
    sql: str = ""
    caveats: str = ""
    llm_calls: int = 0
    stop_reason: str = "final_answer"  # max_llm_calls | answered_in_text | final_answer
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
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.client = client
        self.toolbelt = toolbelt
        self.tracer = tracer or Tracer(None)
        self.max_llm_calls = max_llm_calls
        self.max_consecutive_sql_failures = max_consecutive_sql_failures
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
            reply = self.client.chat(messages, tools=TOOL_SCHEMAS)
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
                            sql=self.toolbelt.last_result.sql
                            if self.toolbelt.last_result
                            else "",
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
        return f"chart saved: {result['chart_path']}"
    if tool_name == "final_answer":
        return f"answer: {result['answer_md'][:120]}"
    return "ok"
