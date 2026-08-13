"""Download demo databases into data/. Chinook is also committed to the repo;
this script exists so a fresh clone (or CI) can re-fetch everything from source.

Usage: uv run python scripts/fetch_data.py
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SOURCES = {
    "chinook.sqlite": (
        "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_Sqlite.sqlite"
    ),
}


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    for filename, url in SOURCES.items():
        target = DATA_DIR / filename
        if target.exists():
            print(f"{filename}: already present ({target.stat().st_size:,} bytes)")
            continue
        print(f"{filename}: downloading from {url}")
        urllib.request.urlretrieve(url, target)  # noqa: S310 - fixed https URL
        print(f"{filename}: done ({target.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
