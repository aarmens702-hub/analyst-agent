"""Tests for scripts/fetch_vancouver.py — pure logic only, no network calls.

download_year is monkeypatched everywhere below; the live fetch was verified
manually (uv run python scripts/fetch_vancouver.py --years 2007) rather than
exercised in CI, per CLAUDE.md's ban on network calls in tests.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

import fetch_vancouver as fv


def test_dataset_id_for_year_routes_each_era() -> None:
    assert fv.dataset_id_for_year(2007) == "property-tax-report-2006-2010"
    assert fv.dataset_id_for_year(2013) == "property-tax-report-2011-2015"
    assert fv.dataset_id_for_year(2018) == "property-tax-report-2016-2019"
    assert fv.dataset_id_for_year(2024) == "property-tax-report"


def test_dataset_id_for_year_rejects_out_of_range_year() -> None:
    with pytest.raises(ValueError, match="1999"):
        fv.dataset_id_for_year(1999)


def test_build_url_encodes_where_clause() -> None:
    url = fv.build_url("property-tax-report-2006-2010", 2007)
    assert url == (
        "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/"
        "property-tax-report-2006-2010/exports/csv?where=report_year%3D2007"
    )


def test_sha256_of_matches_hashlib_reference(tmp_path: Path) -> None:
    f = tmp_path / "sample.csv"
    f.write_bytes(b"pid;report_year\n1;2007\n")
    assert fv.sha256_of(f) == hashlib.sha256(f.read_bytes()).hexdigest()


def test_count_csv_rows_excludes_header_and_strips_bom(tmp_path: Path) -> None:
    f = tmp_path / "sample.csv"
    f.write_bytes("﻿pid;report_year\n1;2007\n2;2007\n".encode())
    assert fv.count_csv_rows(f) == 2


def test_fetch_year_skips_download_when_file_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dest = tmp_path / "property-tax-2007.csv"
    dest.write_text("pid;report_year\n1;2007\n")
    calls = []
    monkeypatch.setattr(fv, "download_year", lambda *a, **k: calls.append(a))

    fetched = fv.fetch_year(2007, tmp_path, force=False)

    assert fetched is False
    assert calls == []


def test_fetch_year_downloads_when_file_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_download(dataset_id: str, year: int, dest: Path, **kwargs: object) -> None:
        dest.write_text("pid;report_year\n1;2007\n")

    monkeypatch.setattr(fv, "download_year", fake_download)

    fetched = fv.fetch_year(2007, tmp_path, force=False)

    assert fetched is True
    assert (
        tmp_path / "property-tax-2007.csv"
    ).read_text() == "pid;report_year\n1;2007\n"


def test_fetch_year_force_redownloads_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dest = tmp_path / "property-tax-2007.csv"
    dest.write_text("stale")
    calls = []

    def fake_download(dataset_id: str, year: int, dest: Path, **kwargs: object) -> None:
        calls.append((dataset_id, year))
        dest.write_text("fresh")

    monkeypatch.setattr(fv, "download_year", fake_download)

    fetched = fv.fetch_year(2007, tmp_path, force=True)

    assert fetched is True
    assert calls == [("property-tax-report-2006-2010", 2007)]
    assert dest.read_text() == "fresh"


def test_build_entry_reads_dest_from_disk(tmp_path: Path) -> None:
    dest = tmp_path / "property-tax-2007.csv"
    dest.write_bytes(b"pid;report_year\n1;2007\n2;2007\n")

    entry = fv.build_entry(2007, dest, fetched_at="2026-08-12T00:00:00+00:00")

    assert entry["year"] == 2007
    assert entry["dataset_id"] == "property-tax-report-2006-2010"
    assert entry["file"] == "property-tax-2007.csv"
    assert entry["rows"] == 2
    assert entry["bytes"] == dest.stat().st_size
    assert entry["sha256"] == hashlib.sha256(dest.read_bytes()).hexdigest()
    assert entry["fetched_at"] == "2026-08-12T00:00:00+00:00"


def test_manifest_round_trips_and_is_year_sorted_on_disk(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    assert fv.load_manifest(manifest_path) == {}  # no history yet, not an error

    entries = {
        2024: {
            "year": 2024,
            "dataset_id": "property-tax-report",
            "url": "u2",
            "file": "f2",
            "sha256": "b",
            "bytes": 2,
            "rows": 2,
            "fetched_at": "t2",
        },
        2007: {
            "year": 2007,
            "dataset_id": "property-tax-report-2006-2010",
            "url": "u1",
            "file": "f1",
            "sha256": "a",
            "bytes": 1,
            "rows": 1,
            "fetched_at": "t1",
        },
    }

    fv.save_manifest(manifest_path, entries)

    assert fv.load_manifest(manifest_path) == entries
    raw = json.loads(manifest_path.read_text())
    assert [rec["year"] for rec in raw] == [2007, 2024]  # sorted, not insertion order
