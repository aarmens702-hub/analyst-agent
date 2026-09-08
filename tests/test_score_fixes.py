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


def test_a_float_that_round_trips_a_long_truth_literal_is_a_correct_repair() -> None:
    """The tolerance that makes the test above pass was applied to one side
    only: the float cell was rounded to 15 significant digits and the truth
    literal was kept exact. So a cleaned float that is the EXACT shortest
    round-trip of a 16-digit truth literal, the best a float64 can possibly
    do, was rounded away from a truth that was not, and a perfect repair
    scored as a wrong one. Under-counting real repairs is the same dishonesty
    as over-counting them, pointed the other way."""
    literal = "0.1234567890123456"  # 16 significant digits
    value = float(literal)
    assert repr(value) == literal, "the float is the exact shortest round-trip"

    dirty = pd.DataFrame({"x": ["N/A"]})
    cleaned = pd.DataFrame({"x": [value]})
    truth = pd.DataFrame({"x": [literal]})

    s = score(dirty, cleaned, truth)
    assert s["should_change"] == 1
    assert s["changed"] == 1
    assert s["correct_changes"] == 1
    assert s["precision"] == 1.0


def test_the_float_tolerance_never_reaches_a_comparison_without_a_float() -> None:
    """The guard against over-correcting the test above. The tolerance exists
    because a float64 carries representation noise nobody wrote; two written
    literals carry no such noise, so a string-to-string comparison stays exact
    at any length and a repair that wrote the wrong digits is still wrong."""
    dirty = pd.DataFrame({"id": ["N/A"]})
    long_truth = "1234567890123456789"  # 19 digits, past float64 entirely
    near_miss = "1234567890123456788"

    s = score(
        dirty,
        pd.DataFrame({"id": [near_miss]}),
        pd.DataFrame({"id": [long_truth]}),
    )
    assert s["correct_changes"] == 0

    # and a float that is genuinely the wrong value stays wrong, tolerance or
    # not: this one misses in the 11th significant digit, far above the noise
    s = score(
        dirty,
        pd.DataFrame({"id": [0.1234567890999999]}),
        pd.DataFrame({"id": ["0.1234567890123456"]}),
    )
    assert s["correct_changes"] == 0


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


def test_a_destroyed_sixteen_digit_id_is_not_a_correct_repair():
    """The float tolerance refused only when BOTH sides ran past 15
    significant digits, so a short float beside a 16-digit literal opened it
    and rounded the literal's last digit away - even though float64 carries
    "4111111111111111" exactly (its shortest round-trip has 16 digits, and
    4111111111111110.0's has 15). A cleaner that wrote 4111111111111110.0
    over the truth therefore scored as a correct repair.

    This is the same lever as the recovery item 12 measured, so a float lane
    rising cannot be read as recovered repairs until this refuses."""
    assert repr(float("4111111111111111")) == "4111111111111111.0", "premise"

    dirty = pd.DataFrame({"card": [""]})
    truth = pd.DataFrame({"card": ["4111111111111111"]})
    cleaned = pd.DataFrame({"card": [4111111111111110.0]})

    result = score(dirty, cleaned, truth)

    assert result["precision"] == 0.0, result


def test_a_cleaner_that_destroys_a_healthy_sixteen_digit_id_is_counted():
    """The other half, and the worse one: with dirty already equal to truth
    the same tolerance made the destroyed value read as no change at all, so
    the damage never reached precision. A cleaner-introduced corruption
    disappeared from the score instead of costing it."""
    dirty = pd.DataFrame({"card": ["4111111111111111"]})
    truth = pd.DataFrame({"card": ["4111111111111111"]})
    cleaned = pd.DataFrame({"card": [4111111111111110.0]})

    result = score(dirty, cleaned, truth)

    assert result["changed"] == 1, result
    assert result["correct_changes"] == 0, result
