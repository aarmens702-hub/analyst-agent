"""Deterministic cleaning — the wedge against pandas-ai (Phase 1).

`aa.clean(df)` fixes the safe diseases with no LLM and no kernel, verifies each
fix the way the agent does (the detector re-runs clean or the fix reverts), and
returns a cleaned frame plus a summary of what it did and what it left for a
human. pandas-ai has no answer to this: it cannot clean your data
trustworthily, only chat with it.
"""

import pandas as pd

import analyst_agent as aa
from analyst_agent.autoclean import changed_cells


def test_changed_cells_reports_the_before_and_after_of_a_value_fix() -> None:
    before = pd.DataFrame({"amount": ["$1,200", "$15"], "note": ["a", "b"]})
    after = pd.DataFrame({"amount": [1200.0, 15.0], "note": ["a", "b"]})
    applied = [{"disease": 1, "slug": "numbers-as-strings", "columns": ["amount"]}]

    out = changed_cells(before, after, applied)

    assert len(out) == 1
    fix = out[0]
    assert fix["disease"] == 1 and fix["columns"] == ["amount"]
    pairs = {(e["old"], e["new"]) for e in fix["examples"]}
    assert ("$1,200", 1200.0) in pairs
    assert all(e["column"] == "amount" for e in fix["examples"])


def test_changed_cells_reports_a_dropped_constant_column_as_removed() -> None:
    before = pd.DataFrame({"amount": [1.0, 2.0], "constant": ["x", "x"]})
    after = pd.DataFrame({"amount": [1.0, 2.0]})
    applied = [{"disease": 19, "slug": "constant-column", "columns": ["constant"]}]

    out = changed_cells(before, after, applied)

    assert out[0]["examples"] == [{"column": "constant", "removed": True, "value": "x"}]


def test_changed_cells_is_empty_when_a_fix_changed_nothing() -> None:
    frame = pd.DataFrame({"note": ["a", "b"]})
    applied = [{"disease": 6, "slug": "whitespace", "columns": ["note"]}]

    out = changed_cells(frame, frame.copy(), applied)

    assert out[0]["examples"] == []


def test_summary_samples_are_the_changed_cells_of_the_applied_fixes() -> None:
    df = pd.DataFrame({"amount": ["$1,200", "$3,400.50", "$15", "$980"] * 5})
    _cleaned, summary = aa.clean(df)

    samples = summary.samples()

    assert any(
        s["disease"] == 1 and any(e.get("new") == 1200.0 for e in s["examples"])
        for s in samples
    )


def test_styler_diff_returns_a_styler_highlighting_the_changed_cells() -> None:
    import pytest

    pytest.importorskip("jinja2")  # pandas Styler needs it; diff() is opt-in extra
    from pandas.io.formats.style import Styler

    from analyst_agent.autoclean import styler_diff

    before = pd.DataFrame({"amount": ["$1,200", "$15"]})
    after = pd.DataFrame({"amount": [1200.0, 15.0]})

    styled = styler_diff(before, after)

    assert isinstance(styled, Styler)
    html = styled.to_html()
    assert "background-color: #1e3a32" in html


def test_clean_fixes_the_safe_diseases_and_defers_the_judgement_calls() -> None:
    df = pd.DataFrame(
        {
            "amount": ["$1,200", "$3,400.50", "$15", "$980"] * 5,  # d01 AUTO
            "status": (["ok", "late", "N/A"] * 6) + ["ok", "N/A"],  # d04 AUTO
            "note": ["  spaced  ", "clean", "two  spaces", "x"] * 5,  # d06 AUTO
            "constant": ["same"] * 20,  # d19 AUTO
            # decimal-comma and symbol coexisting => the same digits read two
            # ways => d01 grades GATE, so clean must defer, not guess
            "amount_eu": ["27,29", "$1,200", "3,50", "$980"] * 5,
        }
    )
    original = df.copy()

    cleaned, summary = aa.clean(df)

    # the input is never mutated — the cleaned frame is a new object
    assert df.equals(original), "clean must not touch the input frame"
    # safe fixes applied and verified
    assert pd.api.types.is_numeric_dtype(cleaned["amount"]), "d01 coerced"
    assert cleaned["status"].isna().sum() == 7, "d04 sentinels -> NaN"
    assert cleaned["note"].tolist()[:3] == ["spaced", "clean", "two spaces"], "d06"
    assert "constant" not in cleaned.columns, "d19 constant column dropped"
    applied = {a["disease"] for a in summary.applied}
    assert {1, 4, 6, 19} <= applied
    # the ambiguous mixed-money column is deferred, not guessed
    assert any(
        r["disease"] == 1 and r["columns"] == ["amount_eu"]
        for r in summary.needs_review
    )
    assert not pd.api.types.is_numeric_dtype(cleaned["amount_eu"]), (
        "GATE not auto-fixed"
    )


def test_a_fix_that_would_not_verify_is_reverted_not_applied(monkeypatch) -> None:
    """The whole promise: a deterministic fix is trusted only because the
    detector re-runs clean. If a fixer produced garbage that still tripped the
    signal, the frame must come back untouched for that column and the finding
    must land in review — never silently 'fixed'."""
    from analyst_agent import autoclean

    def broken_whitespace(frame, cols):
        out = frame.copy()
        for c in cols:
            out[c] = out[c]  # a no-op 'fix' — the signal will still fire
        return out

    monkeypatch.setitem(autoclean.FIXERS, 6, broken_whitespace)
    df = pd.DataFrame({"note": ["  spaced  ", "clean", "two  spaces", "x"] * 5})

    cleaned, summary = aa.clean(df)

    assert cleaned["note"].equals(df["note"]), "an unverified fix must not stick"
    assert not any(a["disease"] == 6 for a in summary.applied)
    assert any(
        r["disease"] == 6 and "did not clear" in r.get("reason", "")
        for r in summary.needs_review
    )


def test_clean_makes_a_real_file_pass_its_own_detectors() -> None:
    """The end-to-end promise on real data: read a government spending file,
    clean it, and the columns the safe fixes touched no longer trip their
    detectors — proven by re-diagnosing the cleaned frame."""
    from pathlib import Path

    src = Path("data/takehome/hmt-spend-2026-03.csv")
    if not src.exists():
        import pytest

        pytest.skip("run scripts/fetch_takehome.py for the real file")

    df = aa.read(src)
    cleaned, summary = aa.clean(df)
    after = {f["disease"] for f in aa.diagnose(cleaned).findings}

    assert summary.applied, "a real messy file should get at least one safe fix"
    # every disease clean claims it applied is gone from the re-diagnosis
    for a in summary.applied:
        assert a["disease"] not in after or a["disease"] in {
            r["disease"] for r in summary.needs_review
        }, f"claimed d{a['disease']} fixed but it still fires"
    assert df.equals(aa.read(src)), "the input frame was mutated"
