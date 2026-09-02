"""The corpus and runner are the instrument's chassis: if the corpus quietly
stops covering a disease, or two builds of the same entry diverge, every
published number downstream is fiction — so coverage and determinism are the
first invariants, tested before any scoring runs."""


def test_smoke_corpus_is_deterministic_and_covers_every_injector():
    from bench.corpus import SMOKE, build
    from bench.corrupt import INJECTORS

    covered: set[int] = set()
    names = [entry["name"] for entry in SMOKE]
    assert len(names) == len(set(names)), "corpus names must be unique"
    assert 12 <= len(SMOKE) <= 30, "smoke-sized by design (~20 datasets)"
    for entry in SMOKE:
        pristine, dirty, truth = build(entry)
        _, _, again = build(entry)
        assert truth.frame_sha256 == again.frame_sha256, entry["name"]
        assert truth.to_json() == again.to_json(), entry["name"]
        assert len(dirty) >= len(pristine), entry["name"]
        assert truth.corruptions, entry["name"]
        covered |= set(entry["diseases"])
    assert covered == set(INJECTORS), sorted(set(INJECTORS) ^ covered)


def test_run_smoke_scores_corpus_and_writes_artifacts(tmp_path):
    from bench.run import run

    report = run(
        mode="smoke",
        results_dir=tmp_path / "results",
        results_md=tmp_path / "RESULTS.md",
        external_root=tmp_path / "no-such-data",  # absent => skipped, never fatal
        limit=4,  # machinery check on a slice; CI runs the unrestricted smoke
    )
    assert report["invariants"] == "ok"
    assert report["external"] == []
    assert (tmp_path / "results" / "smoke.json").exists()
    md = (tmp_path / "RESULTS.md").read_text()
    assert "deterministic mode baseline" in md
    rows = report["synthetic"]
    assert len(rows) == 4
    for row in rows:
        scores = row["scores"]
        assert {"detection", "end_to_end", "verification"} <= scores.keys()
        assert "attempted_diseases" in scores and "not_attempted_diseases" in scores
    agg = report["aggregates"]
    assert set(agg) >= {
        "detection_micro_f1_mean",
        "repair_f1_fixable_mean",
        "survived_rate_mean",
    }


def test_readme_rewrite_is_marker_scoped_and_full_only(tmp_path):
    import pytest

    from bench.run import _write_readme, run

    readme = tmp_path / "README.md"
    readme.write_text(
        "# x\n\nkeep-above\n\n<!-- bench:start -->\nold\n<!-- bench:end -->\n\nkeep-below\n"
    )
    report = {
        "date": "2026-09-02",
        "external": [],
        "aggregates": {
            "datasets": 4,
            "detection_micro_f1_mean": 0.5,
            "repair_f1_fixable_mean": None,
            "survived_rate_mean": 1.0,
        },
    }
    _write_readme(readme, report)
    text = readme.read_text()
    assert "keep-above" in text and "keep-below" in text and "old" not in text
    assert "0.500" in text and "—" in text  # None renders as a dash, not "None"

    bare = tmp_path / "bare.md"
    bare.write_text("no markers here")
    with pytest.raises(ValueError, match="markers"):
        _write_readme(bare, report)

    with pytest.raises(ValueError, match="smoke never writes"):
        run(
            mode="smoke",
            write_readme=True,
            results_dir=tmp_path,
            results_md=tmp_path / "r.md",
        )
