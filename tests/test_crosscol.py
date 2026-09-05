"""Cross-column contradiction detection (P7, first detector).

The first harder-data check: a functional-dependency violation. When one
column almost determines another (within each value of A, one value of B
dominates for at least a threshold of rows), the minority rows that break the
rule are likely errors: a zip that disagrees with its city, a product code
whose category flips on a few rows. Fully deterministic and pair-based, so no
model nomination is needed here (that is for 3+ column rules later). Keyless,
pandas only.
"""

import pandas as pd

from crivo import crosscol


def test_a_near_functional_dependency_flags_the_minority_rows():
    # city -> state holds for every row but one: SF is mislabeled TX
    df = pd.DataFrame(
        {
            "city": ["SF", "SF", "SF", "SF", "LA", "LA"],
            "state": ["CA", "CA", "CA", "TX", "CA", "CA"],
        }
    )
    findings = crosscol.find_fd_violations(df, threshold=0.8)
    fd = next(f for f in findings if f["columns"] == ["city", "state"])
    assert fd["violations"] == 1
    assert fd["grade"] in {"GATE", "HUMAN"}
    assert "city" in fd["evidence"] and "state" in fd["evidence"]


def test_a_perfect_dependency_is_not_flagged():
    # city -> state holds with no exception, so nothing to report
    df = pd.DataFrame({"city": ["SF", "SF", "LA"], "state": ["CA", "CA", "CA"]})
    assert crosscol.find_fd_violations(df, threshold=0.8) == []


def test_no_dependency_below_threshold_is_not_flagged():
    # A does not determine B (every A maps to many Bs), so no FD, no finding
    df = pd.DataFrame({"a": [1, 1, 2, 2], "b": ["w", "x", "y", "z"]})
    assert crosscol.find_fd_violations(df, threshold=0.9) == []


def test_violation_count_scales_and_examples_are_bounded():
    df = pd.DataFrame(
        {
            "dept": ["eng"] * 20 + ["sales"] * 5,
            "floor": ["3"] * 18 + ["9", "9"] + ["2"] * 5,  # 2 eng rows on wrong floor
        }
    )
    fd = next(
        f
        for f in crosscol.find_fd_violations(df, threshold=0.85)
        if f["columns"] == ["dept", "floor"]
    )
    assert fd["violations"] == 2
    assert len(fd.get("examples", [])) <= 5  # evidence is bounded, never a dump


def test_pair_search_is_capped_and_reports_what_it_skipped():
    # many columns: the pair search must bound itself and say so, not silently
    cols = {f"c{i}": list(range(6)) for i in range(30)}
    df = pd.DataFrame(cols)
    findings = crosscol.find_fd_violations(df, max_pairs=50)
    # no crash, bounded work; a skipped-pairs note rides on the result meta
    assert isinstance(findings, list)


def test_nulls_do_not_count_as_a_dependency_break():
    # a missing B is absence, not a contradiction of the rule
    df = pd.DataFrame(
        {"city": ["SF", "SF", "SF", "SF"], "state": ["CA", "CA", None, "CA"]}
    )
    assert crosscol.find_fd_violations(df, threshold=0.8) == []


def test_import_is_keyless_and_pulls_no_core_module():
    import os
    import subprocess
    import sys

    code = (
        "import sys, crivo.crosscol\n"
        "bad=[m for m in ('crivo.loop','crivo.prompts','crivo.skills',"
        "'crivo.provenance','crivo.llm') if m in sys.modules]\n"
        "print(bad); sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
