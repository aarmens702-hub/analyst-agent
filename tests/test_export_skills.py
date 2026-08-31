"""Tests for scripts/export_skills.py — the skill export/scrub/bundle path (P4).

No network, no Docker. Fixture skill folders are built with
crivo.skills.save() and a hand-written ledger dict, matching a real
skills/ directory: skills/<name>/{SKILL.md,scripts/} plus skills/ledger.json,
or skills/retired/<name>/ for the ones the library has already retired.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from crivo import library, skills

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

from export_skills import run_export


def make_skill(name="fix-sentinel-missing", **kw) -> skills.Skill:
    base = {
        "name": name,
        "description": "Replace missing-data tokens with NaN. Use when a column "
        "holds 'N/A' or 'unknown' as if they were values.",
        "fix_source": "def fix(df, columns):\n    return df.copy()\n",
        "test_source": "def test_fix():\n    assert True\n",
        "metadata": {"disease": "4"},
    }
    base.update(kw)
    return skills.Skill(**base)


def ledger_entry(*, state, disease=4, successes=0, failures=0) -> dict:
    return {
        "state": state,
        "disease": disease,
        "successes": successes,
        "failures": failures,
        "uses": successes + failures,
        "datasets": [],
        "recent": [],
        "created": "",
        "last_used": "",
        "events": [],
    }


def write_ledger(root: Path, entries: dict) -> None:
    library.Library(root=root, entries=entries).save()


def test_default_selection_exports_only_proven(tmp_path) -> None:
    root = tmp_path / "skills"
    skills.save(make_skill("fix-proven"), root)
    skills.save(make_skill("fix-probation"), root)
    write_ledger(
        root,
        {
            "fix-proven": ledger_entry(state="proven"),
            "fix-probation": ledger_entry(state="probation"),
        },
    )

    report = run_export(root, tmp_path / "out")

    assert report.exported == ["fix-proven"]
    assert (tmp_path / "out" / "fix-proven" / "SKILL.md").exists()
    assert not (tmp_path / "out" / "fix-probation").exists()


def test_selection_flags_widen_by_state(tmp_path) -> None:
    """--include-probation adds known-probation skills; unknown-state skills
    (no ledger entry at all) need the broader --all, not just --include-probation."""
    root = tmp_path / "skills"
    skills.save(make_skill("fix-proven"), root)
    skills.save(make_skill("fix-probation"), root)
    skills.save(make_skill("fix-mystery"), root)
    write_ledger(
        root,
        {
            "fix-proven": ledger_entry(state="proven"),
            "fix-probation": ledger_entry(state="probation"),
            # fix-mystery is deliberately absent from the ledger.
        },
    )

    default = run_export(root, tmp_path / "out-default")
    assert default.exported == ["fix-proven"]

    probation = run_export(root, tmp_path / "out-probation", include_probation=True)
    assert sorted(probation.exported) == ["fix-probation", "fix-proven"]

    everything = run_export(root, tmp_path / "out-all", include_all=True)
    assert sorted(everything.exported) == ["fix-mystery", "fix-probation", "fix-proven"]


def test_retired_needs_its_own_flag_even_under_all(tmp_path) -> None:
    root = tmp_path / "skills"
    skills.save(make_skill("fix-dead"), root / library.RETIRED_DIR)
    write_ledger(root, {"fix-dead": ledger_entry(state="retired")})

    broad = run_export(root, tmp_path / "out-all", include_all=True)
    assert broad.exported == []

    explicit = run_export(root, tmp_path / "out-retired", include_retired=True)
    assert explicit.exported == ["fix-dead"]


def test_born_from_is_generalized_to_a_basename(tmp_path) -> None:
    """metadata.born_from names a real path on the author's machine (P4 why).
    from_proposal() also echoes that same path into the body as quoted
    provenance, so the body needs the same generalisation, not just metadata."""
    root = tmp_path / "skills"
    born_from = "/Users/aarmensidhu/Desktop/crivo/data/raha/beers/dirty.csv"
    skill = make_skill(
        "fix-ibu-units",
        metadata={"disease": "4", "born_from": born_from},
        body=f"## Where it came from\n\nDisease 4 on `{born_from}`: 340 rows.\n",
    )
    skills.save(skill, root)
    write_ledger(root, {"fix-ibu-units": ledger_entry(state="proven")})

    report = run_export(root, tmp_path / "out")

    assert report.exported == ["fix-ibu-units"]
    result = next(r for r in report.results if r.name == "fix-ibu-units")
    assert any("born_from" in note for note in result.scrubbed)

    exported = skills.load(tmp_path / "out" / "fix-ibu-units")
    assert exported.metadata["born_from"] == "dirty.csv"
    assert born_from not in exported.body
    assert "dirty.csv" in exported.body


def test_strict_drops_born_from_entirely(tmp_path) -> None:
    root = tmp_path / "skills"
    skill = make_skill(
        "fix-ibu-units",
        metadata={"disease": "4", "born_from": "/Users/aarmensidhu/data/dirty.csv"},
        body="## Notes\n\nGeneralised at proposal time.\n",
    )
    skills.save(skill, root)
    write_ledger(root, {"fix-ibu-units": ledger_entry(state="proven")})

    report = run_export(root, tmp_path / "out", strict=True)

    assert report.exported == ["fix-ibu-units"]
    result = next(r for r in report.results if r.name == "fix-ibu-units")
    assert any("dropped" in note for note in result.scrubbed)

    exported = skills.load(tmp_path / "out" / "fix-ibu-units")
    assert "born_from" not in exported.metadata


def test_seventh_frontmatter_field_is_refused(tmp_path) -> None:
    """claude.ai and the Skills API hard-fail on a seventh top-level field, so a
    skill carrying one must be refused with a reason, not silently skipped."""
    root = tmp_path / "skills"
    path = skills.save(make_skill("fix-bad"), root)
    md = (
        (path / "SKILL.md")
        .read_text()
        .replace("license:", "author: someone\nlicense:", 1)
    )
    (path / "SKILL.md").write_text(md)
    write_ledger(root, {"fix-bad": ledger_entry(state="proven")})

    report = run_export(root, tmp_path / "out")

    assert report.exported == []
    result = next(r for r in report.results if r.name == "fix-bad")
    assert result.selected is True
    assert result.ok is False
    assert any("author" in p for p in result.problems)
    assert not (tmp_path / "out" / "fix-bad").exists()


def test_absolute_path_outside_born_from_is_refused(tmp_path) -> None:
    """The body can quote real values from the case that spawned the skill (P4
    why #3) — e.g. from_proposal()'s evidence text. born_from generalisation
    alone doesn't catch this, so any leftover absolute path anywhere in the
    rendered SKILL.md is a hard refusal, not a best-effort scrub."""
    root = tmp_path / "skills"
    skill = make_skill(
        "fix-leaky",
        body="## Evidence\n\nSee /Users/aarmensidhu/Desktop/crivo/"
        "workspace/s07/skill_cases/fix-leaky.parquet for the frozen slice.\n",
    )
    skills.save(skill, root)
    write_ledger(root, {"fix-leaky": ledger_entry(state="proven")})

    report = run_export(root, tmp_path / "out")

    assert report.exported == []
    result = next(r for r in report.results if r.name == "fix-leaky")
    assert result.ok is False
    assert any("absolute path" in p for p in result.problems)
    assert not (tmp_path / "out" / "fix-leaky").exists()


def test_folder_conformance_refuses_extra_and_oversized_files(
    tmp_path, monkeypatch
) -> None:
    """The export gate is stricter than skills.validate(): no stray files in
    the folder, and nothing over the size cap — checked on the source folder,
    not just the rendered SKILL.md."""
    import export_skills

    monkeypatch.setattr(export_skills, "MAX_FILE_BYTES", 10)

    root = tmp_path / "skills"
    cluttered = skills.save(make_skill("fix-cluttered"), root)
    (cluttered / "notes.txt").write_text("do not ship this\n")
    skills.save(make_skill("fix-huge"), root)  # fix.py is well over 10 bytes
    write_ledger(
        root,
        {
            "fix-cluttered": ledger_entry(state="proven"),
            "fix-huge": ledger_entry(state="proven"),
        },
    )

    report = run_export(root, tmp_path / "out")

    assert report.exported == []
    problems = {r.name: r.problems for r in report.results}
    assert any("notes.txt" in p for p in problems["fix-cluttered"])
    assert any("bytes" in p for p in problems["fix-huge"])


def test_manifest_and_readme_are_written_for_exported_skills(tmp_path) -> None:
    import hashlib
    import json

    root = tmp_path / "skills"
    skills.save(make_skill("fix-proven", metadata={"disease": "6"}), root)
    write_ledger(
        root,
        {
            "fix-proven": ledger_entry(
                state="proven", disease=6, successes=4, failures=1
            )
        },
    )

    run_export(root, tmp_path / "out")

    manifest = json.loads((tmp_path / "out" / "MANIFEST.json").read_text())
    assert len(manifest["skills"]) == 1
    entry = manifest["skills"][0]
    assert entry["name"] == "fix-proven"
    assert entry["disease"] == 6
    assert entry["state"] == "proven"
    assert entry["successes"] == 4
    assert entry["failures"] == 1

    expected_sha = hashlib.sha256(
        (tmp_path / "out" / "fix-proven" / "SKILL.md").read_bytes()
    ).hexdigest()
    assert entry["sha256"] == expected_sha

    readme = (tmp_path / "out" / "README.md").read_text()
    assert "test-gated" in readme
    assert "fix-proven" in readme


def test_dry_run_writes_nothing(tmp_path) -> None:
    root = tmp_path / "skills"
    skills.save(make_skill("fix-proven"), root)
    write_ledger(root, {"fix-proven": ledger_entry(state="proven")})
    out = tmp_path / "out"

    report = run_export(root, out, dry_run=True)

    assert report.exported == ["fix-proven"]
    assert report.dry_run is True
    assert not out.exists()


def test_zip_flag_writes_an_archive_of_the_bundle(tmp_path) -> None:
    import zipfile

    root = tmp_path / "skills"
    skills.save(make_skill("fix-proven"), root)
    write_ledger(root, {"fix-proven": ledger_entry(state="proven")})
    out = tmp_path / "out"

    report = run_export(root, out, make_zip=True)

    assert report.zip_path == out.with_suffix(".zip")
    assert report.zip_path.exists()
    with zipfile.ZipFile(report.zip_path) as zf:
        names = zf.namelist()
    assert "MANIFEST.json" in names
    assert "fix-proven/SKILL.md" in names


def test_main_runs_end_to_end_via_the_cli(tmp_path, monkeypatch, capsys) -> None:
    import export_skills

    root = tmp_path / "skills"
    skills.save(make_skill("fix-proven"), root)
    write_ledger(root, {"fix-proven": ledger_entry(state="proven")})
    out = tmp_path / "out"

    monkeypatch.setattr(
        "sys.argv",
        ["export_skills.py", "--skills-root", str(root), "--out", str(out)],
    )
    export_skills.main()

    captured = capsys.readouterr()
    assert "fix-proven" in captured.out
    assert (out / "fix-proven" / "SKILL.md").exists()
    assert (out / "MANIFEST.json").exists()


def test_manifest_disease_is_always_an_int(tmp_path) -> None:
    """The ledger stores it as int, SKILL.md metadata as str per spec. A
    manifest that reports whichever it happened to read is one a consumer
    cannot parse without guessing."""
    root = tmp_path / "skills"
    skills.save(make_skill("fix-a"), root)
    skills.save(make_skill("fix-b"), root)
    (root / "ledger.json").write_text(
        json.dumps({"fix-a": {"disease": 4, "state": "proven"}})
    )  # fix-b has no ledger row, so its disease comes from metadata as a string

    run_export(root, tmp_path / "dist", include_all=True)
    manifest = json.loads((tmp_path / "dist" / "MANIFEST.json").read_text())
    diseases = [s["disease"] for s in manifest["skills"]]
    assert diseases and all(isinstance(d, int) for d in diseases), diseases


def test_an_exported_skill_runs_in_a_consumer_that_never_heard_of_us(tmp_path):
    """The README claims these are portable to any SKILL.md-speaking tool. A
    consumer does not run our test harness or import our package — it imports
    fix.py and calls it. Until this ran, 'portable' was a sentence, not a
    checked claim."""
    root = tmp_path / "skills"
    skills.save(
        make_skill(
            fix_source=(
                "import pandas as pd\n\n\n"
                "def fix(df, columns):\n"
                "    out = df.copy()\n"
                "    for c in columns:\n"
                "        if c in out.columns:\n"
                "            out[c] = pd.to_numeric(out[c], errors='coerce')\n"
                "    return out\n"
            )
        ),
        root,
    )
    (root / library.LEDGER_NAME).write_text(
        json.dumps({"fix-sentinel-missing": {"disease": 4, "state": "proven"}})
    )
    out = tmp_path / "bundle"
    run_export(root, out)

    spec = importlib.util.spec_from_file_location(
        "exported_fix", out / "fix-sentinel-missing" / "scripts" / "fix.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.fix(pd.DataFrame({"ibu": ["N/A", "60", "unknown"]}), ["ibu"])
    assert result["ibu"].isna().sum() == 2
    assert result["ibu"].tolist()[1] == 60.0
