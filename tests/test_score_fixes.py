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
