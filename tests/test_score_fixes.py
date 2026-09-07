"""Tests for scripts/score_fixes.py — cell-level P/R/F1 of fixes (R16/AC8).

Pure-frame tests, no live agent runs. The Raha smoke reads real files from
data/raha/ (scripts/fetch_raha.py) and skips when the data is absent.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

from score_fixes import read_table, score

RAHA = _ROOT / "data" / "raha"

needs_raha = pytest.mark.skipif(
    not RAHA.exists(), reason="data/raha absent — run scripts/fetch_raha.py"
)


def test_perfect_fix_is_p1_r1() -> None:
    dirty = pd.DataFrame({"a": ["1", "2"], "b": ["x", "bad"]})
    truth = pd.DataFrame({"a": ["1", "2"], "b": ["x", "good"]})
    assert score(dirty, truth.copy(), truth) == {
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "changed": 1,
        "should_change": 1,
        "correct_changes": 1,
    }


def test_no_changes_reports_zero_not_nan() -> None:
    dirty = pd.DataFrame({"a": ["bad", "2"]})
    truth = pd.DataFrame({"a": ["good", "2"]})
    s = score(dirty, dirty.copy(), truth)
    assert s["changed"] == 0
    assert s["precision"] == 0.0  # 0/0 is undefined; report 0.0
    assert s["recall"] == 0.0
    assert s["f1"] == 0.0


def test_wrong_changes_are_p0() -> None:
    dirty = pd.DataFrame({"a": ["bad", "2"]})
    truth = pd.DataFrame({"a": ["good", "2"]})
    cleaned = pd.DataFrame({"a": ["worse", "2"]})
    s = score(dirty, cleaned, truth)
    assert s["changed"] == 1
    assert s["correct_changes"] == 0
    assert s["precision"] == 0.0
    assert s["recall"] == 0.0


def test_partial_fix_exact_counts() -> None:
    dirty = pd.DataFrame(
        {"a": ["1", "2", "3"], "b": ["x", "y", "z"], "c": ["p", "q", "r"]}
    )
    truth = pd.DataFrame(
        {"a": ["1", "22", "3"], "b": ["xx", "y", "z"], "c": ["p", "qq", "rr"]}
    )
    # Fixes a1 and b0 (correct), spuriously edits b2, misses c1 and c2.
    cleaned = pd.DataFrame(
        {"a": ["1", "22", "3"], "b": ["xx", "y", "zz"], "c": ["p", "q", "r"]}
    )
    s = score(dirty, cleaned, truth)
    assert s["changed"] == 3
    assert s["should_change"] == 4
    assert s["correct_changes"] == 2
    assert s["precision"] == pytest.approx(2 / 3)
    assert s["recall"] == pytest.approx(2 / 4)
    assert s["f1"] == pytest.approx(4 / 7)


def test_sentinel_to_nan_counts_as_correct_change() -> None:
    dirty = pd.DataFrame({"v": ["N/A", None, "7"]})
    truth = pd.DataFrame({"v": [float("nan"), float("nan"), "7"]})
    cleaned = pd.DataFrame({"v": [None, None, "7"]})
    s = score(dirty, cleaned, truth)
    assert s["should_change"] == 1  # row 1: two missing cells are equal
    assert s["changed"] == 1
    assert s["correct_changes"] == 1
    assert s["precision"] == 1.0
    assert s["recall"] == 1.0


def test_dtype_and_whitespace_do_not_mask_equality() -> None:
    dirty = pd.DataFrame({"n": ["12.0 oz", "5"]})
    truth = pd.DataFrame({"n": ["12", " 5 "]})  # padded truth still equals "5"
    cleaned = pd.DataFrame({"n": [12, 5]})  # ints compare as strings after strip
    s = score(dirty, cleaned, truth)
    assert s["changed"] == 1
    assert s["should_change"] == 1
    assert s["correct_changes"] == 1
    assert s["precision"] == 1.0


def test_float_parse_of_an_integer_truth_is_a_correct_fix() -> None:
    """to_numeric turns "12.0 oz" into 12.0; the truth CSV says "12". Same value,
    and scoring formatting instead of values would report a perfect fix as wrong."""
    dirty = pd.DataFrame({"oz": ["12.0 oz", "16.0 oz."], "ibu": ["N/A", "60"]})
    truth = pd.DataFrame({"oz": ["12", "16"], "ibu": ["", "60"]})
    cleaned = pd.DataFrame({"oz": [12.0, 16.0], "ibu": [float("nan"), 60.0]})
    s = score(dirty, cleaned, truth)
    assert s["changed"] == s["should_change"] == s["correct_changes"] == 3
    assert s["precision"] == 1.0
    assert s["recall"] == 1.0


def test_leading_zeros_still_count_as_a_difference() -> None:
    """Disease 22 is exactly the loss of leading zeros, so "02115" and 2115 must
    not be folded together by the numeric comparison."""
    dirty = pd.DataFrame({"zip": [2115, 90210]})
    truth = pd.DataFrame({"zip": ["02115", "90210"]})
    assert score(dirty, dirty.copy(), truth)["should_change"] == 1
    assert score(dirty, truth.copy(), truth)["correct_changes"] == 1


@pytest.mark.parametrize(
    ("truth_id", "wrong_id"),
    [
        ("12345678901234567", "12345678901234568"),
        # 33 digits, past decimal's default 28-digit context: canonicalizing with
        # Decimal.normalize() would fold this pair back together.
        ("123456789012345678901234567890123", "123456789012345678901234567890124"),
    ],
)
def test_wrong_digits_in_a_long_id_are_not_a_correct_repair(
    truth_id: str, wrong_id: str
) -> None:
    """Canonicalizing a number must not round it. Two long ids differing only in
    the final digit are different values, so writing the wrong one is a wrong
    repair, not a perfect one."""
    dirty = pd.DataFrame({"id": ["N/A"]})
    truth = pd.DataFrame({"id": [truth_id]})
    cleaned = pd.DataFrame({"id": [wrong_id]})
    s = score(dirty, cleaned, truth)
    assert s["should_change"] == 1
    assert s["changed"] == 1
    assert s["correct_changes"] == 0
    assert s["precision"] == 0.0
    assert s["recall"] == 0.0


def test_formatting_never_masks_numeric_equality() -> None:
    """1200, 1200.0 and 1.2E+3 are one value written three ways, so no cell here
    differs from its truth and none should change."""
    dirty = pd.DataFrame({"n": ["1200", "1200.0", "1.2E+3"]})
    truth = pd.DataFrame({"n": ["1.2E+3", "1200", "1200.0"]})
    s = score(dirty, dirty.copy(), truth)
    assert s["should_change"] == 0
    assert s["changed"] == 0


def test_float_noise_from_arithmetic_is_still_a_correct_repair() -> None:
    """The other half of exactness: a float64 cell is not a written literal.

    Mean-imputing a missing cell gives 3.3000000000000003, and parsing money
    gives 1234.5600000000002. Both are correct repairs of a truth that reads
    "3.3" and "1234.56". Their 17th digit is float64 representation noise, not
    a digit anyone wrote, so keying it as if it were exact scores a real repair
    as wrong. .parquet input is the live path for this (read_table keeps its
    dtypes), which is why an all-string CSV fixture cannot see it.
    """
    dirty = pd.DataFrame({"m": [None, "1,234.56"]})
    truth = pd.DataFrame({"m": ["3.3", "1234.56"]})
    cleaned = pd.DataFrame({"m": [3.3000000000000003, 1234.5600000000002]})
    s = score(dirty, cleaned, truth)
    assert s["should_change"] == 2
    assert s["changed"] == 2
    assert s["correct_changes"] == 2
    assert s["precision"] == 1.0
    assert s["recall"] == 1.0

    # ...and the tolerance stops where float64 does. A 17-digit id cannot be
    # carried by a float at all, so a float that lost its last digits is still
    # a wrong repair, not a rounding detail.
    ids_truth = pd.DataFrame({"id": ["12345678901234567"]})
    ids_cleaned = pd.DataFrame({"id": [1.2345678901234568e16]})
    assert (
        score(pd.DataFrame({"id": ["N/A"]}), ids_cleaned, ids_truth)["precision"] == 0
    )


def test_non_finite_numbers_never_key_as_zero() -> None:
    """Decimal accepts "NaN" and "Infinity"; their digit tuples are empty, so a
    canonical key built from the digits alone collapses them onto plain 0. A
    cell reading "Infinity" is not a cell reading "0"."""
    import score_fixes

    for spelling in ("NaN", "sNaN", "Infinity", "-Infinity"):
        assert score_fixes._canonical_number(spelling) is None, spelling
        assert score_fixes._norm(spelling) != score_fixes._norm("0"), spelling


def test_malformed_numeric_looking_cells_do_not_raise() -> None:
    """Canonicalization is a comparison aid, not a parser. A cell Decimal rejects
    (here an exponent too large for it) falls back to its stripped literal instead
    of raising, and unequal literals stay unequal."""
    huge = "1e" + "9" * 40
    dirty = pd.DataFrame({"n": [huge, "1.2.3"]})
    truth = pd.DataFrame({"n": [huge, "7"]})
    s = score(dirty, dirty.copy(), truth)
    assert s["should_change"] == 1  # the huge exponents match, "1.2.3" != "7"


@needs_raha
def test_raha_identity_checks() -> None:
    dirty = read_table(RAHA / "beers" / "dirty.csv")
    clean = read_table(RAHA / "beers" / "clean.csv")

    # Untouched output: nothing changed, so recall is 0 (and there is work to do).
    untouched = score(dirty, dirty.copy(), clean)
    assert untouched["changed"] == 0
    assert untouched["recall"] == 0.0
    assert untouched["should_change"] > 0

    # Oracle output: hand in the ground truth, get perfect precision and recall
    # (on the intersection of column names — beers renames beer_name/brewery_name).
    oracle = score(dirty, clean.copy(), clean)
    assert oracle["precision"] == 1.0
    assert oracle["recall"] == 1.0
    assert oracle["changed"] == oracle["should_change"] == oracle["correct_changes"]
