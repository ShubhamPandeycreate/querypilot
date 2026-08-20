"""Eval runner: score the agent (or a bare model) on benchmark questions.

Two modes:
- single_shot: full schema in one prompt, one completion, extract SQL. The
  classic text-to-SQL setup — cheap (1 call/question), runs on full sets.
- agent: the real AgentLoop with tools (4-6 calls/question) — run on subsets.

Free-tier aware: a shared token-bucket rate limiter gates EVERY llm call in
both modes; runs write JSONL records per question and resume by item id, so a
run killed by quota exhaustion continues where it stopped.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from dbagent.agent.loop import AgentLoop
from dbagent.agent.single_shot import (
    SINGLE_SHOT_MAX_TOKENS,
    build_prompt,
    extract_sql,
    schema_text,
)
from dbagent.agent.tools import ToolBelt
from dbagent.db.database import Database
from dbagent.llm.client import ChatClient
from dbagent.tracing.tracer import Tracer
from evals.datasets import EvalItem
from evals.metrics import results_match

EVAL_ROW_LIMIT = 100_000  # never let the guard's LIMIT injection distort results
EVAL_TIMEOUT_S = 30.0
# The single-shot prompt, its token budget and the SQL extractor live in
# dbagent.agent.single_shot so the demo app and this harness provably share one
# implementation. NB: run local evals with OLLAMA_NO_THINK=false — suppressing
# thinking makes qwen3:4b degenerate into endless output on BIRD-difficulty
# questions (0 SQL in 8 trials), while thinking-enabled finishes cleanly.


@dataclass
class EvalRecord:
    item_id: str
    source: str
    mode: str
    provider: str
    model: str
    predicted_sql: str = ""
    executed: bool = False
    match: bool = False
    difficulty: str = ""
    llm_calls: int = 0
    latency_s: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""


class RateLimiter:
    """Thread-safe requests-per-minute gate shared by all workers."""

    def __init__(self, rpm: int) -> None:
        self.interval = 60.0 / max(1, rpm)
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


class RateLimitedClient:
    """ChatClient wrapper: every chat() waits for a rate-limit slot first."""

    def __init__(self, inner: ChatClient, limiter: RateLimiter) -> None:
        self.inner = inner
        self.limiter = limiter
        self.provider_name = inner.provider_name
        self.model = inner.model

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        self.limiter.acquire()
        return self.inner.chat(messages, **kwargs)


def build_single_shot_prompt(item: EvalItem) -> list[dict[str, Any]]:
    db = Database(item.db_path, row_limit=EVAL_ROW_LIMIT, timeout_seconds=EVAL_TIMEOUT_S)
    try:
        ddl, fks = schema_text(db)
    finally:
        db.close()
    return build_prompt(ddl=ddl, foreign_keys=fks, question=item.question, evidence=item.evidence)


def gold_is_order_sensitive(gold_sql: str) -> bool:
    try:
        parsed = sqlglot.parse_one(gold_sql, read="sqlite")
    except Exception:
        return "order by" in gold_sql.lower()
    return (
        parsed.args.get("order") is not None
        or isinstance(parsed, exp.SetOperation)
        and any(q.args.get("order") for q in parsed.walk() if isinstance(q, exp.Select))
    )


def score_predicted_sql(item: EvalItem, predicted_sql: str) -> tuple[bool, bool, str]:
    """Returns (executed, match, error). Executes gold and predicted on the
    item's database and compares result multisets."""
    if not predicted_sql.strip():
        return False, False, "no SQL produced"
    db = Database(item.db_path, row_limit=EVAL_ROW_LIMIT, timeout_seconds=EVAL_TIMEOUT_S)
    try:
        gold = db.run_sql(item.gold_sql)
        try:
            predicted = db.run_sql(predicted_sql)
        except Exception as error:
            return False, False, f"predicted SQL failed: {str(error)[:200]}"
        order_sensitive = gold_is_order_sensitive(item.gold_sql)
        return True, results_match(gold.rows, predicted.rows, order_sensitive=order_sensitive), ""
    except Exception as error:
        return False, False, f"gold SQL failed: {str(error)[:200]}"
    finally:
        db.close()


def run_one(item: EvalItem, mode: str, client: ChatClient) -> EvalRecord:
    record = EvalRecord(
        item_id=item.id,
        source=item.source,
        mode=mode,
        provider=client.provider_name,
        model=client.model,
        difficulty=item.difficulty,
    )
    started = time.perf_counter()
    try:
        if mode == "single_shot":
            reply = client.chat(build_single_shot_prompt(item), max_tokens=SINGLE_SHOT_MAX_TOKENS)
            record.llm_calls = 1
            record.usage = dict(reply.usage)
            record.predicted_sql = extract_sql(reply.content or "")
        elif mode == "agent":
            db = Database(item.db_path, timeout_seconds=EVAL_TIMEOUT_S)
            try:
                # Parity with single_shot: BIRD's evidence hint goes to both
                # modes (the agent path silently dropped it before 2026-08-19,
                # forcing extra exploration turns to rediscover domain facts).
                question = item.question
                if item.evidence:
                    question = f"{item.question}\n\nHint: {item.evidence}"
                result = AgentLoop(client, ToolBelt(db), Tracer(None)).run(question)
            finally:
                db.close()
            record.llm_calls = result.llm_calls
            record.usage = dict(result.usage)
            record.predicted_sql = result.sql
        else:
            raise ValueError(f"Unknown mode: {mode!r}")
        record.executed, record.match, record.error = score_predicted_sql(
            item, record.predicted_sql
        )
    except Exception as error:
        record.error = f"{type(error).__name__}: {str(error)[:300]}"
    record.latency_s = round(time.perf_counter() - started, 2)
    return record


def run_eval(
    items: list[EvalItem],
    *,
    mode: str,
    client: ChatClient,
    rpm: int,
    out_path: str | Path,
    concurrency: int = 1,
    on_record: Any = None,
) -> dict[str, Any]:
    """Run (or resume) an eval. Records append to out_path as JSONL."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    done_ids = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done_ids.add(json.loads(line)["item_id"])

    pending = [item for item in items if item.id not in done_ids]
    limited = RateLimitedClient(client, RateLimiter(rpm))
    write_lock = threading.Lock()

    def worker(item: EvalItem) -> EvalRecord:
        record = run_one(item, mode, limited)
        with write_lock, out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        if on_record is not None:
            on_record(record)
        return record

    if concurrency <= 1:
        for item in pending:
            worker(item)
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(worker, pending))

    return summarize(out)


def summarize(results_path: str | Path) -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in Path(results_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    total = len(records)
    matches = sum(1 for r in records if r["match"])
    by_difficulty: Counter[str] = Counter()
    match_by_difficulty: Counter[str] = Counter()
    for r in records:
        key = r["difficulty"] or "n/a"
        by_difficulty[key] += 1
        if r["match"]:
            match_by_difficulty[key] += 1
    return {
        "total": total,
        "matches": matches,
        "accuracy": round(100 * matches / total, 1) if total else 0.0,
        "no_sql": sum(1 for r in records if not r["predicted_sql"]),
        "exec_failures": sum(1 for r in records if r["predicted_sql"] and not r["executed"]),
        "avg_llm_calls": round(sum(r["llm_calls"] for r in records) / total, 2) if total else 0,
        "avg_latency_s": round(sum(r["latency_s"] for r in records) / total, 2) if total else 0,
        "by_difficulty": {
            k: f"{match_by_difficulty[k]}/{v} ({100 * match_by_difficulty[k] / v:.0f}%)"
            for k, v in sorted(by_difficulty.items())
        },
        "provider": records[0]["provider"] if records else "",
        "model": records[0]["model"] if records else "",
        "mode": records[0]["mode"] if records else "",
    }


def write_report(summaries: list[dict[str, Any]], out_path: str | Path, title: str) -> Path:
    lines = [
        f"# {title}",
        "",
        f"_Generated {time.strftime('%Y-%m-%d %H:%M')}_, metric: execution accuracy "
        "(gold vs predicted result multisets).",
        "",
        "| provider | model | mode | accuracy | no-SQL | exec-fail | avg calls | avg latency |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['provider']} | {s['model']} | {s['mode']} | "
            f"**{s['accuracy']}%** ({s['matches']}/{s['total']}) | {s['no_sql']} | "
            f"{s['exec_failures']} | {s['avg_llm_calls']} | {s['avg_latency_s']}s |"
        )
    lines.append("")
    for s in summaries:
        if s["by_difficulty"] and set(s["by_difficulty"]) != {"n/a"}:
            lines.append(f"### {s['provider']} {s['mode']} — by difficulty")
            for difficulty, score in s["by_difficulty"].items():
                lines.append(f"- {difficulty}: {score}")
            lines.append("")
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
