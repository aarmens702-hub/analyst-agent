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


def test_sweep_hunts_exceptions_and_reports_a_ledger(monkeypatch):
    """Arc W3/H2: the corpus doubles as a fuzzer — sweep() runs detect+clean
    over expanded entries hunting EXCEPTIONS (not scores) and returns the
    ledger. A healthy slice yields an empty ledger; a booby-trapped detector
    lands in it with the dataset name and error, never a crash."""
    from bench import run as bench_run
    from bench.corpus import SMOKE

    ledger = bench_run.sweep(entries=SMOKE[:2])
    assert ledger == []

    def boom(df, name="df"):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(bench_run, "detect_all", boom)
    ledger = bench_run.sweep(entries=SMOKE[:2])
    assert len(ledger) == 2
    assert all("kaboom" in entry["error"] for entry in ledger)
    assert ledger[0]["name"] == SMOKE[0]["name"]


def _synthetic_row(name: str, survived, repair_f1, not_attempted=()) -> dict:
    """One `score_pair` row, cut down to the fields `_aggregate` reads."""
    return {
        "name": name,
        "diseases": [1],
        "scores": {
            "detection": {"micro": {"f1": 1.0}},
            "end_to_end": {
                "dirt_targeting": {"f1": 1.0},
                "repair": {"f1": repair_f1},
            },
            "verification": {"survived_rate": survived},
            "not_attempted_diseases": list(not_attempted),
        },
    }


def test_survival_mean_publishes_the_denominator_it_averaged_over():
    """A dataset where crivo attempted nothing has an UNDEFINED survival rate,
    not a good one, so it stays out of the mean by design. The danger is the
    silence: without a published count the headline can rest on a handful of
    datasets while reading as if it covered the whole corpus. So the count of
    contributing datasets ships in the aggregates next to the rate."""
    from bench.run import _aggregate

    synthetic = [
        _synthetic_row("attempted-all-survived", 1.0, 1.0),
        _synthetic_row("attempted-half-survived", 0.5, 1.0),
        _synthetic_row("attempted-nothing", None, None),
        _synthetic_row("attempted-nothing-either", None, None),
    ]
    agg = _aggregate(synthetic)

    assert agg["datasets"] == 4
    assert agg["survived_rate_mean"] == 0.75  # the two defined rates only
    assert agg["survival_defined_datasets"] == 2  # not 4: the denominator used


def test_survival_and_repair_denominators_reach_every_published_surface(
    monkeypatch, capsys
):
    """The aggregates dict is the least-read surface. RESULTS.md and the
    one-line smoke summary are what humans quote, so the denominator has to
    appear there too. A bare "survived 1.000" is the failure being fixed.

    A published count also has to name the universe it counts and the
    statistic it belongs to, or it just moves the hiding place. Repair's count
    is over the datasets whose repair was defined, which is a subset of the
    FULLY FIXABLE datasets, itself a subset of the corpus: phrased like
    survival's "over 2 of 4" on the same line, "over 2 of 3" reads as 67%
    coverage when the truth is 2 of 4. So both counts denominate against the
    corpus and keep the fixable split as a parenthetical. And both numbers are
    means of per-dataset ratios rather than pooled rates, so the word `mean`
    has to survive everywhere they are printed."""
    from bench import run as bench_run

    synthetic = [
        _synthetic_row("a", 1.0, 1.0),
        _synthetic_row("b", 0.5, 1.0),
        _synthetic_row("c", None, None),
        _synthetic_row("d", None, None, not_attempted=[3]),
    ]
    report = {
        "mode": "smoke",
        "date": "2026-09-06",
        "synthetic": synthetic,
        "external": [],
        "aggregates": bench_run._aggregate(synthetic),
    }

    md = bench_run._markdown(report)
    assert "survived-verification rate, mean over the 2/4 datasets" in md
    assert "repair F1, mean over the 2/4 datasets with repair defined" in md
    assert "2 of the 3 fully fixable" in md  # the subset, still disclosed
    assert "rate, mean:" not in md  # the bare, un-denominated old label

    monkeypatch.setattr(bench_run, "run", lambda **kwargs: report)
    assert bench_run.main(["--smoke"]) == 0
    out = capsys.readouterr().out
    assert "survived mean 0.750 over 2 of 4" in out
    assert "repair F1 mean 1.000 over 2 of 4 (2 of 3 fixable)" in out


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
            "fully_fixable_datasets": 3,
            "repair_defined_datasets": 2,
            "survival_defined_datasets": 2,
            "detection_micro_f1_mean": 0.5,
            "repair_f1_fixable_mean": None,
            "survived_rate_mean": 1.0,
        },
    }
    _write_readme(readme, report)
    text = readme.read_text()
    assert "keep-above" in text and "keep-below" in text and "old" not in text
    assert "0.500" in text and "—" in text  # None renders as a dash, not "None"
    # the storefront copy carries the denominators too: a rate published
    # without the count it averaged over is the bug this pins shut, and a
    # count published against a subset nothing on the page names is the same
    # bug one level up. So both rates denominate against the 4 datasets the
    # header advertises, and repair keeps its fixable split as well.
    assert "with repair defined (2/4; 2 of the 3 fully fixable)" in text
    assert "attempted a fix (2/4)" in text
    assert "mean over" in text  # both are means of per-dataset ratios

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
