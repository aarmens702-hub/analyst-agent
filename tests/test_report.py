"""Clean-report tests (spec R12/R14): counts, markdown contract with cited
event ids, rNNN save round-trip, empty-report render."""

import json

from crivo.report import CleanReport


def _fix_record(disease, slug, columns, status, attempts, evs):
    """A fix record with the full R12 shape."""
    fixed = status == "fixed"
    return {
        "finding": {
            "disease": disease,
            "slug": slug,
            "columns": columns,
            "evidence": f"{slug}: 3 hits",
            "stats": {},
            "grade": "GATE",
            "confidence": 0.9,
            "indicator": False,
        },
        "status": status,
        "attempts": attempts,
        "fix_source": (
            f"def fix_{slug.replace('-', '_')}(df):\n"
            "    out = df.copy()\n"
            "    return out"
        ),
        "model_asserts": [f"assert df[{columns[0]!r}].notna().all()"],
        "verify": {
            "signal_clear": fixed,
            "rows": fixed,
            "untouched": fixed,
            "layer2": fixed,
        },
        "transcript_evs": evs,
        "elapsed_s": 2.5,
    }


def _report() -> CleanReport:
    return CleanReport(
        report_id="s01-r001",
        session="s01",
        variable="beers",
        source={"path": "data/beers.csv", "sha256": "deadbeef" * 8},
        fixes=[
            _fix_record(4, "sentinel-missing", ["ibu"], "fixed", 1, [11, 12]),
            _fix_record(1, "units-in-values", ["abv"], "fixed", 2, [14, 16]),
            _fix_record(7, "case-variants", ["state"], "skipped", 1, [18]),
            _fix_record(2, "date-mixture", ["brewed"], "failed", 3, [20, 22, 24]),
        ],
        indicators=[
            {
                "disease": 12,
                "slug": "cross-field-contradiction",
                "columns": ["city", "state"],
                "evidence": "3 rows where city != state",
                "stats": {"count": 3},
                "grade": "HUMAN",
                "confidence": 0.7,
                "indicator": True,
            }
        ],
        clear=[5, 6, 8, 11],
        outputs={
            "parquet": "workspace/s01/cleaned/beers.parquet",
            "lineage": "workspace/s01/cleaned/beers.lineage.json",
        },
        stats={"shape": [[2410, 8], [2403, 8]], "nulls": [1005, 0]},
        event_chain=[9, 11, 12, 14, 16, 18, 20, 22, 24],
        created="2026-08-11T12:00:00-07:00",
    )


def test_counts_tallies_statuses_and_flagged():
    assert _report().counts() == {
        "fixed": 2,
        "skipped": 1,
        "failed": 1,
        "aborted": 0,
        "flagged": 1,
    }


def test_markdown_render_contains_the_contract():
    md = _report().to_markdown()
    assert "## clean report s01-r001 — `beers`" in md
    assert "**2 fixed · 1 skipped · 1 failed · 0 not attempted · 1 flagged**" in md
    assert "4 signals clear" in md
    assert "- ✓ d04 sentinel-missing [ibu] · fixed (1 attempt) · ev 11, 12" in md
    assert "- → d07 case-variants [state] · skipped (1 attempt)" in md
    assert "- ✗ d02 date-mixture [brewed] · failed (3 attempts)" in md
    assert "**flagged, not fixed (indicators)**" in md
    assert "- ⚠ d12 cross-field-contradiction: 3 rows where city != state" in md
    assert "- cleaned: workspace/s01/cleaned/beers.parquet" in md
    assert "- lineage: workspace/s01/cleaned/beers.lineage.json" in md
    assert "9 → 11 → 12 → 14 → 16 → 18 → 20 → 22 → 24" in md
    assert "2026-08-11T12:00:00-07:00" in md


def test_save_writes_rnnn_json_and_md(tmp_path):
    report = _report()
    json_path = report.save(tmp_path / "clean_reports")
    assert json_path.name == "r001.json"
    md_path = tmp_path / "clean_reports" / "r001.md"
    assert md_path.exists()
    assert md_path.read_text(encoding="utf-8") == report.to_markdown() + "\n"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["report_id"] == "s01-r001"
    assert [r["status"] for r in data["fixes"]] == [
        "fixed",
        "fixed",
        "skipped",
        "failed",
    ]
    assert data["fixes"][0]["finding"]["slug"] == "sentinel-missing"
    assert data["fixes"][3]["attempts"] == 3


def test_the_report_carries_a_per_column_rollup_of_what_moved():
    """P5 R7: a clean run should end with a scannable answer to "what changed
    in MY columns, and by how much" — sourced from the stats the findings
    already carry. Failed and skipped fixes moved nothing, so they stay out
    of it."""
    rec_a = _fix_record(4, "sentinel-missing", ["ibu"], "fixed", 1, [3])
    rec_a["finding"]["stats"] = {"sentinel_count": 55}
    rec_b = _fix_record(6, "whitespace-damage", ["ibu"], "fixed", 1, [5])
    rec_b["finding"]["stats"] = {"count": 12, "values": 40}
    rec_c = _fix_record(1, "numbers-as-strings", ["abv"], "failed", 3, [7])
    rec_c["finding"]["stats"] = {"values": 40, "residue_frac": 0.5}
    report = CleanReport(
        report_id="r001",
        session="s01",
        variable="df",
        source={},
        fixes=[rec_a, rec_b, rec_c],
    )

    md = report.to_markdown()

    assert "per column" in md.lower()
    rollup = md.lower().split("per column", 1)[1].split("*events")[0]
    assert "ibu" in rollup and "55" in rollup and "12" in rollup
    assert "abv" not in rollup, "a failed fix moved nothing"


def test_the_report_names_the_autonomy_level_it_ran_under():
    """Packet 4 R4: a run that decided things for itself has to say so. The
    level is what tells an auditor which posture produced these records."""
    report = _report()
    assert report.autonomy == "autonomous", "the shipped default"
    assert "**autonomy: autonomous**" in report.to_markdown()

    careful = _report()
    careful.autonomy = "careful"
    assert "**autonomy: careful**" in careful.to_markdown()


def test_the_report_says_which_changes_nobody_approved():
    """The load-bearing audit question. A count in the header answers "how
    many", and the mark on the fix line answers "which". A total without the
    per-fix mark would tell an auditor a number they cannot check."""
    silent = _fix_record(4, "sentinel-missing", ["ibu"], "fixed", 1, [11, 12])
    silent["unattended"] = True
    gated = _fix_record(6, "whitespace-damage", ["abv"], "fixed", 1, [14])
    gated["unattended"] = False
    report = CleanReport(
        report_id="s01-r001",
        session="s01",
        variable="beers",
        source={},
        fixes=[silent, gated],
    )

    md = report.to_markdown()

    assert "1 change applied with no gate shown" in md
    silent_line = next(line for line in md.splitlines() if "sentinel-missing" in line)
    gated_line = next(line for line in md.splitlines() if "whitespace-damage" in line)
    assert silent_line.endswith("· unattended"), silent_line
    assert "unattended" not in gated_line, gated_line


def test_a_report_with_no_unattended_fix_claims_none():
    """Absence must not read as a suppressed count: a fully gated run says the
    level and stops there."""
    md = _report().to_markdown()
    assert "no gate shown" not in md
    assert "unattended" not in md


def test_the_saved_json_carries_the_level_and_the_flags(tmp_path):
    """The JSON is the machine-readable audit surface; the markdown is a
    rendering of it. Both have to carry the record."""
    report = _report()
    report.autonomy = "careful"
    report.fixes[0]["unattended"] = False
    data = json.loads(
        report.save(tmp_path / "clean_reports").read_text(encoding="utf-8")
    )

    assert data["autonomy"] == "careful"
    assert data["fixes"][0]["unattended"] is False


def test_empty_report_renders_with_zero_counts():
    report = CleanReport(report_id="s01-r002", session="s01", variable="df", source={})
    assert report.counts() == {
        "fixed": 0,
        "skipped": 0,
        "failed": 0,
        "aborted": 0,
        "flagged": 0,
    }
    md = report.to_markdown()
    assert "## clean report s01-r002 — `df`" in md
    assert "**0 fixed · 0 skipped · 0 failed · 0 not attempted · 0 flagged**" in md
