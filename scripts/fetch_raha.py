"""Fetch the Raha benchmark's clean/dirty pairs into data/external/raha/ (bench R4).

download() and verify() are kept apart so verify — the idempotent-skip and
corruption-detection logic — is unit-testable with zero network.

Apache-2.0 upstream: https://github.com/BigDaMa/raha

Usage:
    uv run python scripts/fetch_raha.py             # fetch what's missing
    uv run python scripts/fetch_raha.py --strict     # nonzero exit if incomplete
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path

USER_AGENT = "crivo-fetch-raha/0.1"

# name -> filename -> {url, sha256}. Pinned to a commit SHA, not a branch —
# raw.githubusercontent.com serves whatever a branch head currently points
# at, and the bench adapter needs byte-stable files. URLs and hashes below
# were discovered by hand (GitHub API: resolve the commit, list datasets/,
# download each file once, sha256 it) and are recorded here, not re-derived
# at runtime. sha256 of None marks an entry whose URL was never confirmed to
# serve real content — fetch_one skips it loudly instead of trusting an
# unverified byte stream.
_COMMIT = "7be1334b8c7bbdac3f47ef514fb3e1e8c5fc181c"  # BigDaMa/raha master, 2025-06-05
_RAW_BASE = f"https://raw.githubusercontent.com/BigDaMa/raha/{_COMMIT}/datasets"

DATASETS: dict[str, dict[str, dict[str, str | None]]] = {
    "hospital": {
        "clean.csv": {
            "url": f"{_RAW_BASE}/hospital/clean.csv",
            "sha256": "ea3ee44998455c0b491750c348509de176c758a3bbf58e4530c0a136bb248b4b",
        },
        "dirty.csv": {
            "url": f"{_RAW_BASE}/hospital/dirty.csv",
            "sha256": "dbc5575b915fe8b5e0ac6dc6172f38ba91e611fdb76d09a8f4a81cb7ea9925ac",
        },
    },
    "flights": {
        "clean.csv": {
            "url": f"{_RAW_BASE}/flights/clean.csv",
            "sha256": "0acfcfd8985b06fdd363965c9e8d9522c43e7589a93d79ae7dc311e1c37fdf3b",
        },
        "dirty.csv": {
            "url": f"{_RAW_BASE}/flights/dirty.csv",
            "sha256": "1b5c1afa10aa0e7c20fd7e14d05c56772715b2771aa0f5fa67ed1709e1eecd46",
        },
    },
    "beers": {
        "clean.csv": {
            "url": f"{_RAW_BASE}/beers/clean.csv",
            "sha256": "373227df59ad197e154dd5149125789e415019535c7223355e9486ee1b3b93de",
        },
        "dirty.csv": {
            "url": f"{_RAW_BASE}/beers/dirty.csv",
            "sha256": "7110bf4931a9445a1675e544d6c996817c739136239f8a2b02e088c7ec0a1f68",
        },
    },
    "rayyan": {
        "clean.csv": {
            "url": f"{_RAW_BASE}/rayyan/clean.csv",
            "sha256": "23159f43c0706782388ed8957ad0c74eb7b88bc98f34d65bd49296e186d4673f",
        },
        "dirty.csv": {
            "url": f"{_RAW_BASE}/rayyan/dirty.csv",
            "sha256": "7e25e6db262b0c72ca2d9735d5959599cf5a582e1c705459507c7b45d0d1d174",
        },
    },
}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(path: Path, expected_sha256: str) -> bool:
    """True iff path exists and hashes to expected_sha256 — the skip-or-fetch signal."""
    return path.exists() and sha256_of(path) == expected_sha256


def download(url: str, dest: Path, timeout: int = 60) -> None:
    """Fetch url's full body into dest. Whole-file, not streamed — these are
    sub-2MB CSVs."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        dest.write_bytes(response.read())


def fetch_one(name: str, filename: str, entry: dict, root: Path) -> str:
    """Resolve one clean.csv/dirty.csv against its recorded hash.

    Returns "skipped" (already present, verified), "fetched" (downloaded and
    verified), "mismatch" (downloaded but hash didn't match — partial deleted),
    "failed" (the download itself raised), or "unverified" (entry carries no
    confirmed hash — never downloaded blind).
    """
    dest = root / name / filename
    sha256 = entry["sha256"]
    if sha256 is None:
        print(f"UNVERIFIED  {name}/{filename} — no confirmed URL/hash, skipping")
        return "unverified"
    if verify(dest, sha256):
        print(f"skipped     {name}/{filename} (already present, hash verified)")
        return "skipped"
    try:
        download(entry["url"], dest)
    except OSError as exc:
        print(f"FAILED      {name}/{filename}: {exc}")
        if dest.exists():
            dest.unlink()
        return "failed"
    if not verify(dest, sha256):
        print(
            f"MISMATCH    {name}/{filename}: sha256 did not match after download, deleting"
        )
        dest.unlink()
        return "mismatch"
    print(f"fetched     {name}/{filename}")
    return "fetched"


def fetch_all(root: Path) -> dict[str, str]:
    """Run fetch_one for every dataset/file pair in DATASETS; return
    {"name/filename": status}."""
    statuses = {}
    for name, files in DATASETS.items():
        for filename, entry in files.items():
            statuses[f"{name}/{filename}"] = fetch_one(name, filename, entry, root)
    return statuses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="nonzero exit if anything is missing at the end",
    )
    parser.add_argument("--root", type=Path, default=Path("data/external/raha"))
    args = parser.parse_args(argv)

    statuses = fetch_all(args.root)

    ok = {"skipped", "fetched"}
    missing = {k: v for k, v in statuses.items() if v not in ok}
    if missing:
        print(f"\n{len(missing)} of {len(statuses)} file(s) not available: {missing}")
    return 1 if (args.strict and missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
