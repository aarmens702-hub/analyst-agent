"""Fetch Vancouver property-tax yearly CSV slices into data/vancouver/ (P2.5).

Vancouver Open Data publishes one logical property-tax table split across 4
dataset ids by era, each exportable per year via a `where=report_year=<year>`
filter. That yields 21 same-schema yearly slices (~4.25M rows total) where
one confirmed column-mapping fix should replay across every slice — the
"compounding family" demo for the skill system. OGL-Vancouver licensed:
https://opendata.vancouver.ca/pages/licence/

Everything below DATASET_RANGES was confirmed against the live API, not
assumed from docs: per-dataset report_year coverage (via a group_by(
report_year) count query — report_year is typed `text`, so min()/max()
aggregation is rejected and group_by is the only way to ask), a UTF-8 BOM,
semicolon-delimited bodies, and column-order drift between the 2006-2010
file and the current one (narrative_legal_line4/5 relocated after
tax_assessment_year, previous_land_value/previous_improvement_value swapped,
and a trailing `note` column that 2006-2010 lacks entirely).

Usage:
    uv run python scripts/fetch_vancouver.py                  # 3 most recent years
    uv run python scripts/fetch_vancouver.py --years 2007 2019
    uv run python scripts/fetch_vancouver.py --all             # all 21 years, ~4.25M rows
    uv run python scripts/fetch_vancouver.py --force --years 2024
"""

import argparse
import csv
import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

API_BASE = "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets"
USER_AGENT = "crivo-fetch-vancouver/0.1"

# Confirmed live via:
#   .../datasets/{id}/records?select=report_year,count(*)%20as%20cnt
#                            &group_by=report_year&order_by=report_year
# against each of the 4 dataset ids. Per-year row counts at verification
# time (for sanity-checking future re-fetches, not enforced by code):
#   2006-2010: 171074, 174946, 177968, 182247, 185793   (5 yrs,   892,028 rows)
#   2011-2015: 188149, 190802, 193392, 200925, 203494   (5 yrs,   976,762 rows)
#   2016-2019: 203658, 206480, 209649, 213182           (4 yrs,   832,969 rows)
#   2020-2026: 214803, 217802, 218674, 222197, 224743,
#              226360, 228567                           (7 yrs, 1,553,146 rows)
# 21 yearly slices, ~4.25M rows total — matches the research doc's estimate.
DATASET_RANGES: dict[str, tuple[int, int]] = {
    "property-tax-report-2006-2010": (2006, 2010),
    "property-tax-report-2011-2015": (2011, 2015),
    "property-tax-report-2016-2019": (2016, 2019),
    # "current years" — Vancouver appends a new report_year to this dataset
    # id each year. A stale upper bound just means dataset_id_for_year raises
    # a clear ValueError for the new year instead of silently mis-routing it.
    "property-tax-report": (2020, 2026),
}

ALL_YEARS = sorted(y for lo, hi in DATASET_RANGES.values() for y in range(lo, hi + 1))
# Small and reasonable by default — the full set is ~4.25M rows across 21
# files, not something a plain `run the script` invocation should trigger.
DEFAULT_YEARS = tuple(ALL_YEARS[-3:])


def dataset_id_for_year(year: int) -> str:
    """Route a report year to the dataset id that publishes it."""
    for dataset_id, (lo, hi) in DATASET_RANGES.items():
        if lo <= year <= hi:
            return dataset_id
    valid = ", ".join(f"{lo}-{hi}" for lo, hi in DATASET_RANGES.values())
    raise ValueError(f"no Vancouver property-tax dataset covers {year} (have {valid})")


def build_url(dataset_id: str, year: int) -> str:
    """Build the per-year CSV export URL.

    `exports/csv` alone returns the whole dataset; a `where=report_year=
    <year>` filter slices out one same-schema year, which is what makes this
    a "compounding family" — the same column-mapping fix replays across all
    21 slices instead of being re-derived per file.
    """
    query = urllib.parse.urlencode({"where": f"report_year={year}"})
    return f"{API_BASE}/{dataset_id}/exports/csv?{query}"


def sha256_of(path: Path) -> str:
    """Stream-hash a file so multi-hundred-MB CSVs don't need to fit in RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_csv_rows(path: Path) -> int:
    """Count data rows (excluding the header).

    Vancouver's export has a UTF-8 BOM and is semicolon-delimited (confirmed
    against a live sample, not the usual comma) — csv.reader with the right
    delimiter also correctly skips over the embedded newlines that show up
    inside narrative_legal_line text, which a naive line count would not.
    """
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader, None)  # header
        return sum(1 for _ in reader)


def download_year(
    dataset_id: str, year: int, dest: Path, *, timeout: float = 180.0
) -> None:
    """Stream one year's CSV straight to disk, atomically.

    Writes to a same-directory `.part` file and only `os.replace`s it into
    `dest` once the full response has landed — a network failure partway
    through never leaves a truncated CSV at the real filename (CLAUDE.md:
    data/ holds immutable originals, so a half-written file there would lie).
    """
    url = build_url(dataset_id, year)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        downloaded = 0
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            total = resp.getheader("Content-Length")
            size_label = f"{int(total) / 1e6:,.1f} MB" if total else "unknown size"
            with tmp.open("wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
                    downloaded += len(chunk)
                    print(
                        f"\r  {year}: {downloaded / 1e6:,.1f} MB / {size_label}",
                        end="",
                        flush=True,
                    )
        print()
        if downloaded == 0:
            raise RuntimeError(f"{year}: server returned an empty body ({url})")
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"failed to download {year} from {url}: {exc}") from exc
    except RuntimeError:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, dest)


def fetch_year(year: int, out_dir: Path, *, force: bool = False) -> bool:
    """Ensure `out_dir/property-tax-<year>.csv` exists; return True if fetched.

    Idempotent by default: an existing file is left untouched (and nothing
    is downloaded) unless force=True.
    """
    dataset_id = dataset_id_for_year(year)  # raises ValueError for a bad year
    dest = out_dir / f"property-tax-{year}.csv"
    if dest.exists() and not force:
        return False
    download_year(dataset_id, year, dest)
    return True


def build_entry(year: int, dest: Path, *, fetched_at: str) -> dict:
    """Build one manifest record by inspecting the CSV already on disk."""
    dataset_id = dataset_id_for_year(year)
    return {
        "year": year,
        "dataset_id": dataset_id,
        "url": build_url(dataset_id, year),
        "file": dest.name,
        "sha256": sha256_of(dest),
        "bytes": dest.stat().st_size,
        "rows": count_csv_rows(dest),
        "fetched_at": fetched_at,
    }


def load_manifest(path: Path) -> dict[int, dict]:
    """Load manifest.json into a {year: entry} dict for merge-by-year.

    No file yet just means no history — return empty, not an error.
    """
    if not path.exists():
        return {}
    records = json.loads(path.read_text(encoding="utf-8"))
    return {rec["year"]: rec for rec in records}


def save_manifest(path: Path, entries: dict[int, dict]) -> None:
    """Serialize the manifest as a year-sorted JSON list.

    This is provenance (CLAUDE.md: ship every answer with code + assertions
    + provenance), so it should be stable and diffable, not dict-ordered by
    whatever sequence years happened to be touched in.
    """
    ordered = [entries[year] for year in sorted(entries)]
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch Vancouver property-tax yearly CSV slices — the "
            "'compounding family' demo dataset (P2.5)."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--years",
        type=int,
        nargs="+",
        metavar="YEAR",
        help="specific report years to fetch, e.g. --years 2007 2019 2024",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help=(
            f"fetch all {len(ALL_YEARS)} years "
            f"({ALL_YEARS[0]}-{ALL_YEARS[-1]}, ~4.25M rows total)"
        ),
    )
    parser.add_argument(
        "--force", action="store_true", help="redownload even if the CSV exists"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: data/vancouver)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = args.out or (Path(__file__).resolve().parents[1] / "data" / "vancouver")
    years = ALL_YEARS if args.all else (args.years or list(DEFAULT_YEARS))

    manifest_path = out_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    all_ok = True

    for year in years:
        dest = out_dir / f"property-tax-{year}.csv"
        previous = manifest.get(year)
        try:
            was_fetched = fetch_year(year, out_dir, force=args.force)
        except (ValueError, RuntimeError) as exc:
            print(f"FAILED  {year}: {exc}")
            all_ok = False
            continue
        # Preserve the original fetch time across skips — "fetched_at" is
        # provenance for when the bytes were retrieved, not when the script
        # last happened to run over an already-present file.
        fetched_at = (
            datetime.now(UTC).isoformat(timespec="seconds")
            if was_fetched or previous is None
            else previous["fetched_at"]
        )
        entry = build_entry(year, dest, fetched_at=fetched_at)
        manifest[year] = entry
        verb = "fetched" if was_fetched else "kept   "
        print(f"{verb} {dest} ({entry['bytes']:,} bytes, {entry['rows']:,} rows)")

    save_manifest(manifest_path, manifest)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
