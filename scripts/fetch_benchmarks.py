"""Stage benchmark datasets locally (they are gitignored — too big for the repo).

Usage:
    uv run python scripts/fetch_benchmarks.py --bird     # BIRD Mini-Dev (~0.75GB zip)
    uv run python scripts/fetch_benchmarks.py --spider   # Spider 1.0 (Google Drive, needs gdown)

BIRD ships MySQL and PostgreSQL variants too; we keep only SQLite and delete
the rest to respect the project's disk budget.
"""

from __future__ import annotations

import argparse
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOWNLOADS = ROOT / "evals" / "datasets" / "downloads"

BIRD_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip"
SPIDER_GDRIVE_ID = "1403EGqzIDoHMdQF4c9Bkyl7dZLZ5Wt6J"


def _dir_size_gb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.glob("**/*") if f.is_file()) / 1e9


def fetch_bird() -> None:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOADS / "minidev.zip"
    if not zip_path.exists():
        print(f"downloading {BIRD_URL} (~0.75GB)")
        urllib.request.urlretrieve(BIRD_URL, zip_path)
    print("extracting minidev.zip")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(DOWNLOADS / "bird")
    # Keep SQLite only: drop MySQL/PostgreSQL variants and raw dumps.
    removed = 0
    for path in sorted((DOWNLOADS / "bird").glob("**/*"), reverse=True):
        name = path.name.lower()
        if path.is_dir() and ("mysql" in name or "postgres" in name):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        elif path.is_file() and name.endswith((".sql", ".jsonl")) and "mysql" in name:
            path.unlink(missing_ok=True)
            removed += 1
    zip_path.unlink()  # the zip itself is dead weight once extracted
    print(f"pruned {removed} non-SQLite artifacts; staged {_dir_size_gb(DOWNLOADS):.2f}GB")


def fetch_spider() -> None:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOADS / "spider.zip"
    if not zip_path.exists():
        try:
            import gdown
        except ImportError as error:
            raise SystemExit(
                "Spider lives on Google Drive; install gdown first:\n"
                "  uv run --with gdown python scripts/fetch_benchmarks.py --spider"
            ) from error
        gdown.download(id=SPIDER_GDRIVE_ID, output=str(zip_path))
    print("extracting spider.zip")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(DOWNLOADS / "spider")
    zip_path.unlink()
    print(f"staged; downloads dir now {_dir_size_gb(DOWNLOADS):.2f}GB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bird", action="store_true")
    parser.add_argument("--spider", action="store_true")
    args = parser.parse_args()
    if not (args.bird or args.spider):
        raise SystemExit("Pass --bird and/or --spider")
    if args.bird:
        fetch_bird()
    if args.spider:
        fetch_spider()


if __name__ == "__main__":
    main()
