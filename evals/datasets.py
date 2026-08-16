"""Benchmark dataset loaders: BIRD Mini-Dev, Spider dev, Chinook smoke set.

Everything normalizes to EvalItem so the runner doesn't care where a question
came from. Benchmark data itself is NOT in git — stage it with
scripts/fetch_benchmarks.py (see each loader's error message).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS = ROOT / "evals" / "datasets" / "downloads"


@dataclass(frozen=True)
class EvalItem:
    id: str
    source: str  # bird_mini_dev | spider_dev | chinook_smoke
    db_id: str
    db_path: Path
    question: str
    gold_sql: str
    difficulty: str = ""
    evidence: str = ""  # BIRD's external-knowledge hint; goes into the prompt


def _missing(what: str, hint: str) -> FileNotFoundError:
    return FileNotFoundError(
        f"{what} not found. Stage it first: {hint} (see scripts/fetch_benchmarks.py)"
    )


def load_bird_mini_dev() -> list[EvalItem]:
    """BIRD Mini-Dev: 500 SELECT-only questions over 11 SQLite databases."""
    candidates = sorted(DOWNLOADS.glob("**/mini_dev_sqlite.json"))
    if not candidates:
        raise _missing(
            "BIRD Mini-Dev (mini_dev_sqlite.json)",
            "uv run python scripts/fetch_benchmarks.py --bird",
        )
    questions_file = candidates[0]
    db_root = _find_db_root(questions_file.parent, "dev_databases")
    raw = json.loads(questions_file.read_text(encoding="utf-8"))

    items = []
    for entry in raw:
        db_id = entry["db_id"]
        db_path = db_root / db_id / f"{db_id}.sqlite"
        items.append(
            EvalItem(
                id=f"bird_{entry['question_id']}",
                source="bird_mini_dev",
                db_id=db_id,
                db_path=db_path,
                question=entry["question"].strip(),
                gold_sql=entry["SQL"].strip(),
                difficulty=entry.get("difficulty", ""),
                evidence=(entry.get("evidence") or "").strip(),
            )
        )
    return items


def load_spider_dev() -> list[EvalItem]:
    """Spider 1.0 dev: 1034 questions over 20 SQLite databases."""
    candidates = [p for p in DOWNLOADS.glob("**/dev.json") if "spider" in str(p).lower()]
    if not candidates:
        raise _missing(
            "Spider dev (dev.json)", "uv run python scripts/fetch_benchmarks.py --spider"
        )
    questions_file = sorted(candidates)[0]
    db_root = _find_db_root(questions_file.parent, "database")
    raw = json.loads(questions_file.read_text(encoding="utf-8"))

    items = []
    for index, entry in enumerate(raw):
        db_id = entry["db_id"]
        items.append(
            EvalItem(
                id=f"spider_{index:04d}",
                source="spider_dev",
                db_id=db_id,
                db_path=db_root / db_id / f"{db_id}.sqlite",
                question=entry["question"].strip(),
                gold_sql=entry["query"].strip(),
            )
        )
    return items


def load_chinook_smoke() -> list[EvalItem]:
    """Our hand-written, contamination-free sanity set."""
    raw = json.loads((ROOT / "evals" / "smoke_questions.json").read_text(encoding="utf-8"))
    db_path = ROOT / "data" / "chinook.sqlite"
    return [
        EvalItem(
            id=entry["id"],
            source="chinook_smoke",
            db_id="chinook",
            db_path=db_path,
            question=entry["question"],
            gold_sql=entry["gold_sql"],
        )
        for entry in raw
    ]


def fixed_subset(items: list[EvalItem], n: int, seed: int = 42) -> list[EvalItem]:
    """Deterministic subset for cheap iteration: same seed -> same questions,
    so numbers stay comparable across runs and providers."""
    if n >= len(items):
        return list(items)
    ordered = sorted(items, key=lambda item: item.id)
    return sorted(random.Random(seed).sample(ordered, n), key=lambda item: item.id)


def _find_db_root(start: Path, dirname: str) -> Path:
    """The databases directory sits near the questions file; search up then down."""
    for base in (start, *start.parents[:3]):
        candidate = base / dirname
        if candidate.is_dir():
            return candidate
    matches = sorted(start.glob(f"**/{dirname}"))
    if matches:
        return matches[0]
    raise _missing(f"database directory {dirname!r} near {start}", "re-run the fetch script")


LOADERS = {
    "bird": load_bird_mini_dev,
    "spider": load_spider_dev,
    "chinook": load_chinook_smoke,
}
