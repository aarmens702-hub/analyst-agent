"""Scorer tests (bench/score.py, spec R3/R7) — dense TDD: one test function
per RED step, each table-driven so the whole scorer needs few of them."""

import numpy as np
import pandas as pd

from bench.score import equivalent, score_detection, score_end_to_end, score_pair
from bench.truth import Cell, Corruption, GroundTruth, frame_sha256


def test_equivalent_type_aware_cases():
    # (a, b, expected) — the missing family collapses to one value; numerics
    # cross-compare within 1e-9 relative tolerance; datetime-like values
    # compare by instant; everything else needs exact equality in the same
    # type family — a str is never a stand-in for a number or a timestamp.
    cases = [
        (None, np.nan, True),
        (np.nan, pd.NaT, True),
        (pd.NA, None, True),
        (5, 5.0, True),
        (5, 5 * (1 + 1e-6), False),  # 5 vs 5.000005 — well outside 1e-9 rel tol
        (5, 5 * (1 + 1e-12), True),  # 5 vs 5.000000000005 — inside 1e-9 rel tol
        (pd.Timestamp("2024-01-01"), np.datetime64("2024-01-01"), True),
        ("12.5", 12.5, False),  # str is never a stand-in for a number
        ("a", "a", True),
        ("a", "a ", False),  # trailing space is a real difference
    ]
    for a, b, expected in cases:
        assert equivalent(a, b) is expected, f"equivalent({a!r}, {b!r})"
        assert equivalent(b, a) is expected, f"equivalent({b!r}, {a!r}) [symmetry]"


def test_score_end_to_end_hand_computed_fixture():
    # 3x2 grid. Dirty cells vs pristine: (0,b) "xx"!="x", (1,a) "two"!=2,
    # (2,b) "zz"!="z" -> D = {(0,b),(1,a),(2,b)}, |D|=3.
    # Cleaned: (0,b) left untouched (a miss); (1,a) fixed to 2 (correct);
    # (2,b) touched but fixed to "ZZ", still != pristine "z" (fixed wrong).
    # C = cells where cleaned != dirty = {(1,a),(2,b)}, |C|=2.
    # K = cells where cleaned == pristine = {(0,a),(1,a),(1,b),(2,a)}, |K|=4.
    # C∩D = {(1,a),(2,b)} -> 2. D∩K = {(1,a)} -> 1. C∩D∩K = {(1,a)} -> 1.
    # dirt_targeting: P = 2/2 = 1.0, R = 2/3, F1 = 2*1*(2/3)/(1+2/3) = 0.8
    # repair:         P = 1/2 = 0.5, R = 1/3, F1 = 2*.5*(1/3)/(.5+1/3) = 0.4
    pristine = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    dirty = pd.DataFrame({"a": [1, "two", 3], "b": ["xx", "y", "zz"]})
    cleaned = pd.DataFrame({"a": [1, 2, 3], "b": ["xx", "y", "ZZ"]})
    truth = GroundTruth(seed=0, base="fixture", n_rows=3, n_cols=2, frame_sha256="x")

    result = score_end_to_end(pristine, dirty, cleaned, truth)

    assert result["dirt_targeting"] == {"precision": 1.0, "recall": 2 / 3, "f1": 0.8}
    assert result["repair"]["precision"] == 0.5
    assert result["repair"]["recall"] == 1 / 3
    assert result["repair"]["f1"] == 2 * 0.5 * (1 / 3) / (0.5 + 1 / 3)  # == 0.4
    assert result["counts"] == {"dirty": 3, "changed": 2, "repaired": 1}


def test_score_end_to_end_oracle_and_no_op_invariants():
    pristine = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    dirty = pd.DataFrame({"a": [1, 99], "b": ["x", "y"]})  # D = {(1,a)}, |D|=1
    truth = GroundTruth(seed=0, base="fixture", n_rows=2, n_cols=2, frame_sha256="x")

    # Oracle: the cleaner produces the pristine frame exactly -> every dirty
    # cell got touched and landed correct, so repair P = R = 1.0.
    oracle = score_end_to_end(pristine, dirty, pristine.copy(), truth)
    assert oracle["repair"]["precision"] == 1.0
    assert oracle["repair"]["recall"] == 1.0
    assert oracle["repair"]["f1"] == 1.0

    # No-op: the cleaner changes nothing, so C = set() -> both precisions are
    # 0/0 (None, the zero-denominator rule), while both recalls have a real,
    # nonzero denominator (|D|=1) and come out 0.0 — never a crash either way.
    noop = score_end_to_end(pristine, dirty, dirty.copy(), truth)
    assert noop["dirt_targeting"]["precision"] is None
    assert noop["dirt_targeting"]["recall"] == 0.0
    assert noop["dirt_targeting"]["f1"] is None
    assert noop["repair"]["precision"] is None
    assert noop["repair"]["recall"] == 0.0
    assert noop["repair"]["f1"] is None
    assert noop["counts"] == {"dirty": 1, "changed": 0, "repaired": 0}


def test_score_detection_counts_and_disease_zero_exclusion():
    # disease 1: finding matches truth on "amount" -> TP. disease 2: truth
    # has a corruption but no finding claims it -> FN. disease 3: a finding
    # claims it but truth has no such corruption -> FP (wrong disease).
    # disease 0 (external/unknown) appears on both sides and must vanish.
    detect_result = {
        "findings": [
            {"disease": 1, "columns": ["amount"]},
            {"disease": 3, "columns": ["amount"]},
            {"disease": 0, "columns": ["x"]},
        ]
    }
    truth = GroundTruth(
        seed=0,
        base="fixture",
        n_rows=1,
        n_cols=1,
        frame_sha256="x",
        corruptions=[
            Corruption(disease=1, columns=("amount",), granularity="cell"),
            Corruption(disease=2, columns=("date",), granularity="cell"),
            Corruption(disease=0, columns=("x",), granularity="cell"),
        ],
    )

    result = score_detection(detect_result, truth)

    # Matching here is one-to-one, so both numerators agree and every metric
    # value is the same as before the numerators were split; what changed is
    # the schema, which now names each numerator and its own denominator and
    # carries no count that would fit either ratio. Misses are a subtraction:
    # findings missed = n_truth - matched_truth, spurious = n_findings -
    # matched_findings.
    # disease 1: 1 of 1 finding matched, 1 of 1 corruption matched
    #            -> P=1/1=1.0, R=1/1=1.0, F1=1.0
    assert result["per_disease"]["1"] == {
        "matched_findings": 1,
        "n_findings": 1,
        "matched_truth": 1,
        "n_truth": 1,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    # disease 2: no findings at all, 0 of 1 corruption matched
    #            -> P: 0/0 -> None, R=0/1=0.0, F1 None
    assert result["per_disease"]["2"] == {
        "matched_findings": 0,
        "n_findings": 0,
        "matched_truth": 0,
        "n_truth": 1,
        "precision": None,
        "recall": 0.0,
        "f1": None,
    }
    # disease 3: 0 of 1 finding matched, no corruptions at all
    #            -> P=0/1=0.0, R: 0/0 -> None, F1 None
    assert result["per_disease"]["3"] == {
        "matched_findings": 0,
        "n_findings": 1,
        "matched_truth": 0,
        "n_truth": 0,
        "precision": 0.0,
        "recall": None,
        "f1": None,
    }
    assert "0" not in result["per_disease"]
    # macro_f1: diseases present in truth (excluding 0) are {1, 2}; disease
    # 2's None f1 counts as 0.0 in the average -> (1.0 + 0.0) / 2 = 0.5
    assert result["macro_f1"] == 0.5
    # micro: matched_findings=1 over 2 findings -> P=0.5; matched_truth=1 over
    # 2 truth corruptions -> R=0.5; F1=0.5
    assert result["micro"] == {"precision": 0.5, "recall": 0.5, "f1": 0.5}


def test_score_detection_recall_counts_truth_not_findings():
    # Matching is not one-to-one: three findings all match the SAME corruption,
    # and a second corruption is matched by nothing. Only 1 of 2 corruptions was
    # recalled, so recall is 0.5. Counting the recall numerator over findings
    # gives 3/(3+1) = 0.75, the inflation this test exists to forbid.
    detect_result = {
        "findings": [
            {"disease": 1, "columns": ["amount"]},
            {"disease": 1, "columns": ["amount"]},
            {"disease": 1, "columns": ["amount"]},
        ]
    }
    truth = GroundTruth(
        seed=0,
        base="fixture",
        n_rows=1,
        n_cols=1,
        frame_sha256="x",
        corruptions=[
            Corruption(disease=1, columns=("amount",), granularity="cell"),
            Corruption(disease=1, columns=("date",), granularity="cell"),
        ],
    )

    result = score_detection(detect_result, truth)
    d1 = result["per_disease"]["1"]

    # precision numerator is over findings, recall numerator is over truth
    assert (d1["matched_findings"], d1["n_findings"]) == (3, 3)
    assert (d1["matched_truth"], d1["n_truth"]) == (1, 2)

    assert d1["precision"] == 1.0
    assert d1["recall"] == 0.5
    assert d1["f1"] == 2 * 1.0 * 0.5 / 1.5

    # the micro aggregate must use the same per-ratio numerators
    assert result["micro"]["precision"] == 1.0
    assert result["micro"]["recall"] == 0.5

    # The metric fields are only half the artifact: bench/results/*.json
    # publishes these counts too, and a reader recomputing from them has to
    # land on the published ratio. A count of matched FINDINGS paired with a
    # truth-side miss count rebuilds 3/(3+1) = 0.75, the recall this scorer
    # retracted, from a file whose metric fields are correct. So every count
    # names the universe it was counted over, and no field IS that number.
    # The comment here used to go one further and say no such pair could be
    # drawn at all; the test below that one shows it can be, exactly.
    assert set(d1) == {
        "matched_findings",
        "n_findings",
        "matched_truth",
        "n_truth",
        "precision",
        "recall",
        "f1",
    }
    assert d1["matched_findings"] / d1["n_findings"] == d1["precision"]
    assert d1["matched_truth"] / d1["n_truth"] == d1["recall"]


def test_the_retracted_recall_is_constructible_from_the_published_counts():
    """The wave 1 docstring and commit message claimed a reader "cannot land
    on the retracted number by reaching for a plausible-looking pair". That
    claim is false, and it is the kind of false claim that stops the next
    reader checking: matched_findings over matched_findings plus the truth
    side's misses rebuilds the retracted 0.75 exactly, from the four counts
    this scorer publishes. The counts stay published, because hiding the
    inputs of a wrong ratio would hide the inputs of the right one too. What
    has to go is the guarantee."""
    detect_result = {
        "findings": [
            {"disease": 1, "columns": ["amount"]},
            {"disease": 1, "columns": ["amount"]},
            {"disease": 1, "columns": ["amount"]},
        ]
    }
    truth = GroundTruth(
        seed=0,
        base="fixture",
        n_rows=1,
        n_cols=1,
        frame_sha256="x",
        corruptions=[
            Corruption(disease=1, columns=("amount",), granularity="cell"),
            Corruption(disease=1, columns=("date",), granularity="cell"),
        ],
    )

    d1 = score_detection(detect_result, truth)["per_disease"]["1"]
    misses = d1["n_truth"] - d1["matched_truth"]
    retracted = d1["matched_findings"] / (d1["matched_findings"] + misses)

    assert retracted == 0.75
    assert d1["recall"] == 0.5, "the published recall is still the honest one"

    doc = score_detection.__doc__ or ""
    assert "cannot land on" not in doc, "the retracted number is constructible"
    assert "matched_findings + (n_truth - matched_truth)" in doc, (
        "the docstring has to name the wrong pairing it cannot prevent"
    )


def test_a_finding_that_names_no_column_cannot_recall_a_column_scoped_disease():
    """The empty-columns wildcard was written for the whole-row diseases and
    then applied to every disease in both directions, so ONE finding naming no
    column recalled every cell-scoped corruption of its disease and published
    recall 1.0 off a finding that located nothing."""
    detect_result = {"findings": [{"disease": 1, "columns": []}]}
    truth = GroundTruth(
        seed=0,
        base="fixture",
        n_rows=1,
        n_cols=3,
        frame_sha256="x",
        corruptions=[
            Corruption(disease=1, columns=("amount",), granularity="cell"),
            Corruption(disease=1, columns=("date",), granularity="cell"),
            Corruption(disease=1, columns=("qty",), granularity="cell"),
        ],
    )

    d1 = score_detection(detect_result, truth)["per_disease"]["1"]

    assert (d1["matched_truth"], d1["n_truth"]) == (0, 3)
    assert d1["recall"] == 0.0
    assert (d1["matched_findings"], d1["n_findings"]) == (0, 1)
    assert d1["precision"] == 0.0


def test_a_whole_row_disease_still_matches_the_finding_that_names_no_column():
    """The guard against over-correcting the test above. crivo's own d09
    finding carries `columns: []` because duplicate rows are not about any one
    column, while the injector records every column of the frame. That pair is
    a real match and closing the wildcard entirely would score crivo's most
    reliable detector at zero."""
    detect_result = {"findings": [{"disease": 9, "columns": []}]}
    truth = GroundTruth(
        seed=0,
        base="fixture",
        n_rows=2,
        n_cols=2,
        frame_sha256="x",
        corruptions=[
            Corruption(disease=9, columns=("a", "b"), granularity="row", rows=(2,)),
        ],
    )

    d9 = score_detection(detect_result, truth)["per_disease"]["9"]

    assert d9["recall"] == 1.0
    assert d9["precision"] == 1.0


def test_equivalent_numpy_bool_is_a_bool_not_a_number():
    # np.bool_ is not a Python bool and (in numpy 2) not an np.integer either,
    # so an unguarded numpy True falls to plain == and compares equal to 1.
    # A bool compares only to another bool: True and 1 are not the same fix.
    assert equivalent(np.bool_(True), 1) is False
    assert equivalent(1, np.bool_(True)) is False
    assert equivalent(np.bool_(False), 0) is False
    assert equivalent(0, np.bool_(False)) is False
    # ...and a numpy bool IS a bool, so it matches its Python twin.
    assert equivalent(np.bool_(True), True) is True
    assert equivalent(True, np.bool_(True)) is True
    assert equivalent(np.bool_(True), np.bool_(True)) is True
    assert equivalent(np.bool_(True), np.bool_(False)) is False
    assert equivalent(np.bool_(True), False) is False


def test_score_pair_keyless_integration_smoke():
    # Real detect_all + clean, no LLM key needed — just structural correctness
    # of the composition and sane metric ranges, not exact detector recall.
    pristine = pd.DataFrame(
        {
            "amount": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0],
            "name": [
                "alice",
                "bob",
                "carol",
                "dave",
                "erin",
                "frank",
                "grace",
                "heidi",
                "ivan",
                "judy",
            ],
        }
    )
    dirty = pristine.copy()
    dirty.loc[0, "name"] = "  alice  "  # whitespace damage (d06)
    dirty.loc[1, "amount"] = -999  # sentinel missing (d04)

    truth = GroundTruth(
        seed=1,
        base="fixture",
        n_rows=len(pristine),
        n_cols=len(pristine.columns),
        frame_sha256=frame_sha256(dirty),
        corruptions=[
            Corruption(
                disease=6,
                columns=("name",),
                granularity="cell",
                cells=(
                    Cell(row=0, column="name", original="alice", corrupted="  alice  "),
                ),
            ),
            Corruption(
                disease=4,
                columns=("amount",),
                granularity="cell",
                cells=(Cell(row=1, column="amount", original=20.0, corrupted=-999),),
            ),
        ],
    )

    result = score_pair(pristine, dirty, truth)

    assert set(result) == {
        "detection",
        "end_to_end",
        "verification",
        "attempted_diseases",
        "not_attempted_diseases",
    }
    assert set(result["detection"]) == {"per_disease", "macro_f1", "micro"}
    assert set(result["end_to_end"]) == {"dirt_targeting", "repair", "counts"}
    assert set(result["verification"]) == {
        "attempted",
        "applied",
        "survived_rate",
        "needs_review",
    }

    def _bounded(node):  # every float in this schema is a ratio metric
        if isinstance(node, dict):
            for v in node.values():
                _bounded(v)
        elif isinstance(node, list):
            for v in node:
                _bounded(v)
        elif isinstance(node, float):
            assert 0.0 <= node <= 1.0, node

    _bounded(result)


def test_string_space_equivalence_scores_external_pairs():
    # Raha pairs are all-string; crivo.clean re-types. Typed equivalence makes
    # external repair structurally unwinnable (str is never a number), so
    # external scoring needs string-space equality: blank == missing,
    # "12.0" == "12", but "02115" != "2115" (leading zeros are disease 22).
    import numpy as np
    import pandas as pd

    from bench.score import equivalent_str, score_end_to_end
    from bench.truth import GroundTruth, frame_sha256

    cases = [
        ("12.0", "12", True),
        ("12.0", 12.0, True),  # typed cleaned value vs string pristine
        ("02115", "2115", False),
        ("  x ", "x", True),
        ("", np.nan, True),
        (pd.Timestamp("2004-01-01"), "2004-01-01", True),
        ("a", "b", False),
    ]
    for a, b, want in cases:
        assert equivalent_str(a, b) is want, (a, b)
        assert equivalent_str(b, a) is want, (b, a)

    # 2x2 string pair: cell (0,'n') dirty "12,0"->cleaned typed 12.0 == "12" ✓
    # cell (1,'d') dirty "bad"->cleaned NaT vs pristine "2004-01-01" ✗ (missed)
    pristine = pd.DataFrame({"n": ["12", "3"], "d": ["2004-01-01", "2004-01-02"]})
    dirty = pd.DataFrame({"n": ["12,0", "3"], "d": ["2004-01-01", "bad"]})
    cleaned = pd.DataFrame(
        {"n": [12.0, 3.0], "d": [pd.Timestamp("2004-01-01"), pd.NaT]}
    )
    truth = GroundTruth(
        seed=0, base="external", n_rows=2, n_cols=2, frame_sha256=frame_sha256(dirty)
    )
    result = score_end_to_end(
        pristine, dirty, cleaned, truth, values_equal=equivalent_str
    )
    # D = {(0,n),(1,d)}; C = {(0,n),(1,d)}; repaired = {(0,n)} only
    assert result["counts"] == {"dirty": 2, "changed": 2, "repaired": 1}
    assert result["repair"]["precision"] == 0.5
    assert result["repair"]["recall"] == 0.5
