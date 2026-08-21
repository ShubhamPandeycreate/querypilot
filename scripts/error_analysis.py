"""Sample benchmark failures and build a review file for manual error analysis.

Phase 5 asks a question the accuracy number cannot answer: *why* does it miss?
The literature reports substantial annotation errors in published text-to-SQL
gold SQL, so some fraction of any miss rate is not the model's fault. The only
way to know your own fraction is to read the failures.

This script does the reading-preparation, not the judging:

1. samples N failures from a results JSONL, stratified by difficulty and seeded
   so the sample is reproducible and citable;
2. re-executes gold and predicted SQL so both result sets sit side by side;
3. writes a Markdown review file, one section per failure;
4. writes a labels CSV with one blank row per failure, for you to fill in.

Then `--summarize` reads the filled CSV back and prints the breakdown that goes
into the report.

The suggested category on each entry is a *heuristic*, printed only to speed up
reading. Every published number must come from the labels you type, not from
the suggestion, because the heuristic has never been validated against anything.

Usage:
    uv run python scripts/error_analysis.py            # defaults to the full-500 results
    uv run python scripts/error_analysis.py --summarize evals/reports/error_analysis_labels.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from dbagent.db.database import Database  # noqa: E402
from evals.datasets import LOADERS  # noqa: E402

# Generous caps: we are inspecting, not benchmarking, and a truncated result set
# hides exactly the difference we are trying to see.
INSPECT_ROW_LIMIT = 100_000
INSPECT_TIMEOUT_S = 30.0
ROWS_SHOWN = 8

CATEGORIES = {
    "gold_wrong": "The gold SQL looks wrong or answers a different question",
    "ambiguous": "The question admits several honest readings; gold picked one",
    "metric_artifact": "Semantically right, scored wrong (column order, extra columns, types)",
    "schema_linking": "Wrong tables or columns chosen",
    "value_format": "Filter did not match how values are actually stored",
    "aggregation": "Wrong grain, join fan-out, wrong GROUP BY or aggregate",
    "filter_logic": "Missing, extra or inverted condition",
    "order_limit": "Ordering or LIMIT differs from what was asked",
    "no_sql": "Model produced no SQL at all",
    "exec_error": "SQL was produced but failed to run",
    "other": "Anything else; write a note",
}


@dataclass
class Failure:
    record: dict[str, Any]
    question: str
    evidence: str
    gold_sql: str
    db_id: str
    gold: tuple[list[str], list[tuple], str]  # columns, rows, error
    predicted: tuple[list[str], list[tuple], str]


def run_sql(db_path: Path, sql: str) -> tuple[list[str], list[tuple], str]:
    """Execute for inspection. Returns (columns, rows, error)."""
    if not sql.strip():
        return [], [], "no SQL"
    try:
        db = Database(db_path, row_limit=INSPECT_ROW_LIMIT, timeout_seconds=INSPECT_TIMEOUT_S)
    except Exception as error:  # missing database file
        return [], [], f"{type(error).__name__}: {error}"
    try:
        result = db.run_sql(sql)
        return result.columns, result.rows, ""
    except Exception as error:
        return [], [], f"{type(error).__name__}: {str(error)[:200]}"
    finally:
        db.close()


def suggest(failure: Failure) -> str:
    """A first guess, to be confirmed or overruled by a human. Never published."""
    _, gold_rows, gold_error = failure.gold
    pred_cols, pred_rows, pred_error = failure.predicted
    gold_cols = failure.gold[0]

    if pred_error == "no SQL":
        return "no_sql"
    if pred_error:
        return "exec_error"
    if gold_error:
        return "gold_wrong"
    if not gold_rows:
        # A reference query that answers nothing is usually a bad reference.
        return "gold_wrong"
    if not pred_rows:
        return "value_format"
    if len(pred_rows) == len(gold_rows) and len(pred_cols) != len(gold_cols):
        return "metric_artifact"
    if len(pred_rows) == len(gold_rows) and {tuple(sorted(map(str, r))) for r in pred_rows} == {
        tuple(sorted(map(str, r))) for r in gold_rows
    }:
        # Same values, different arrangement: column order or duplication.
        return "metric_artifact"
    if len(pred_rows) > len(gold_rows) * 2:
        return "aggregation"
    return "schema_linking"


def load_failures(results_path: Path, dataset: str) -> list[dict[str, Any]]:
    records = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    seen: dict[str, dict[str, Any]] = {}
    for record in records:  # the benchmark itself has duplicate ids; keep the first
        seen.setdefault(record["item_id"], record)
    return [r for r in seen.values() if not r["match"]]


def stratified_sample(failures: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """Proportional by difficulty, so the sample mirrors the failure population."""
    if n >= len(failures):
        return sorted(failures, key=lambda r: r["item_id"])
    rng = random.Random(seed)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in sorted(failures, key=lambda r: r["item_id"]):
        buckets.setdefault(record["difficulty"] or "n/a", []).append(record)

    chosen: list[dict[str, Any]] = []
    for _difficulty, bucket in sorted(buckets.items()):
        take = round(n * len(bucket) / len(failures))
        chosen.extend(rng.sample(bucket, min(take, len(bucket))))
    # Rounding can leave us a little over or under.
    pool = [r for r in failures if r not in chosen]
    while len(chosen) < n and pool:
        chosen.append(pool.pop(rng.randrange(len(pool))))
    return sorted(chosen[:n], key=lambda r: r["item_id"])


def render_rows(columns: list[str], rows: list[tuple], error: str) -> str:
    if error:
        return f"`{error}`"
    if not rows:
        return "_(zero rows)_"
    head = " | ".join(str(c) for c in columns)
    sep = " | ".join("---" for _ in columns)
    body = "\n".join(" | ".join(str(v)[:40] for v in row) for row in rows[:ROWS_SHOWN])
    more = f"\n\n_… {len(rows) - ROWS_SHOWN} more rows_" if len(rows) > ROWS_SHOWN else ""
    table = "\n".join(f"| {line} |" for line in body.splitlines())
    return f"| {head} |\n| {sep} |\n{table}{more}"


def write_review(failures: list[Failure], out_path: Path, results_path: Path, seed: int) -> None:
    lines = [
        "# Error analysis review sheet",
        "",
        f"{len(failures)} failures sampled from `{results_path.name}`, "
        f"stratified by difficulty, seed {seed}.",
        "",
        "For each one: read the question, compare the two result sets, then write a category "
        "in the labels CSV. The **suggested** line is a heuristic to speed up reading. It is "
        "not evidence and never appears in the report.",
        "",
        "## Categories",
        "",
        *[f"- `{key}` — {text}" for key, text in CATEGORIES.items()],
        "",
        "---",
        "",
    ]

    for index, failure in enumerate(failures, start=1):
        record = failure.record
        lines += [
            f"## {index}. `{record['item_id']}` ({record['difficulty'] or 'n/a'}, "
            f"db `{failure.db_id}`)",
            "",
            f"**Question.** {failure.question}",
            "",
        ]
        if failure.evidence:
            lines += [f"**Evidence hint.** {failure.evidence}", ""]
        lines += [
            f"_suggested: `{suggest(failure)}`_",
            "",
            "**Gold SQL**",
            "",
            "```sql",
            failure.gold_sql.strip(),
            "```",
            "",
            "**Predicted SQL**",
            "",
            "```sql",
            failure.record["predicted_sql"].strip() or "-- none produced",
            "```",
            "",
            "**Gold result**",
            "",
            render_rows(*failure.gold),
            "",
            "**Predicted result**",
            "",
            render_rows(*failure.predicted),
            "",
            "---",
            "",
        ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_labels(failures: list[Failure], out_path: Path) -> None:
    if out_path.exists():
        print(f"labels file already exists, not overwriting: {out_path}")
        return
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["item_id", "difficulty", "category", "notes"])
        for failure in failures:
            writer.writerow([failure.record["item_id"], failure.record["difficulty"], "", ""])


def summarize(labels_path: Path) -> None:
    with labels_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    labelled = [r for r in rows if (r.get("category") or "").strip()]
    print(f"labelled {len(labelled)} of {len(rows)}")
    if not labelled:
        print("nothing to summarize yet")
        return

    unknown = {r["category"] for r in labelled} - set(CATEGORIES)
    if unknown:
        print(f"WARNING: categories not in the agreed list: {sorted(unknown)}")

    counts = Counter(r["category"].strip() for r in labelled)
    print("\nbreakdown")
    for category, count in counts.most_common():
        share = 100 * count / len(labelled)
        print(f"  {category:<18}{count:>4}  {share:>5.1f}%   {CATEGORIES.get(category, '')}")

    not_model = sum(counts[c] for c in ("gold_wrong", "ambiguous", "metric_artifact"))
    print(
        f"\nnot the model's fault ({', '.join(('gold_wrong', 'ambiguous', 'metric_artifact'))}): "
        f"{not_model}/{len(labelled)} = {100 * not_model / len(labelled):.0f}% of sampled failures"
    )
    print("This is the number the report should quote, with the sample size beside it.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="evals/results/bird_single_shot_ollama_full500.jsonl")
    parser.add_argument("--dataset", default="bird", choices=sorted(LOADERS))
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="evals/reports/error_analysis_review.md")
    parser.add_argument("--labels", default="evals/reports/error_analysis_labels.csv")
    parser.add_argument("--summarize", default="", help="Read a filled labels CSV and report")
    args = parser.parse_args()

    if args.summarize:
        summarize(Path(args.summarize))
        return

    results_path = Path(args.results)
    failures = load_failures(results_path, args.dataset)
    print(f"{len(failures)} distinct failures in {results_path.name}")

    sample = stratified_sample(failures, args.n, args.seed)
    print(f"sampled {len(sample)}: {dict(Counter(r['difficulty'] for r in sample))}")

    items = {item.id: item for item in LOADERS[args.dataset]()}
    built: list[Failure] = []
    for index, record in enumerate(sample, start=1):
        item = items[record["item_id"]]
        print(f"  [{index}/{len(sample)}] executing {record['item_id']}", end="\r")
        built.append(
            Failure(
                record=record,
                question=item.question,
                evidence=item.evidence,
                gold_sql=item.gold_sql,
                db_id=item.db_id,
                gold=run_sql(item.db_path, item.gold_sql),
                predicted=run_sql(item.db_path, record["predicted_sql"]),
            )
        )

    write_review(built, Path(args.out), results_path, args.seed)
    write_labels(built, Path(args.labels))
    print(f"\nreview sheet : {args.out}")
    print(f"labels csv   : {args.labels}")
    print("\nWhen the CSV is filled in:")
    print(f"  uv run python scripts/{Path(__file__).name} --summarize {args.labels}")


if __name__ == "__main__":
    main()
