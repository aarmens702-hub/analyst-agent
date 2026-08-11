"""Fetch the Raha benchmark dirty/clean pairs into data/raha/ (P1 dev data).

Dirty/clean pairs make fixes scoreable: apply a fix to dirty.csv, diff against
clean.csv, and you get precision/recall instead of vibes. Apache-2.0 upstream:
https://github.com/BigDaMa/raha

Usage: uv run python scripts/fetch_raha.py
"""

import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/BigDaMa/raha/master/datasets"
DATASETS = ["hospital", "flights", "beers", "movies_1"]
FILES = ["dirty.csv", "clean.csv"]


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "data" / "raha"
    for name in DATASETS:
        for fname in FILES:
            dest = root / name / fname
            if dest.exists():
                print(f"kept    {dest} ({dest.stat().st_size:,} bytes)")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            url = f"{BASE}/{name}/{fname}"
            with urllib.request.urlopen(url, timeout=60) as resp:
                dest.write_bytes(resp.read())
            print(f"fetched {dest} ({dest.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
