"""Driver decomposition (capability roadmap B1.2): 'why did this metric
change' as ranked per-dimension contributions whose receipt is that they sum
EXACTLY to the observed delta. Additive (sum) metrics only in v1, because
there the decomposition is provably exact; mix-vs-rate for ratios is a
follow-up. Pure pandas, no model, no new dependency."""

import pandas as pd

from crivo.decompose import decompose_sum


def _frame(rows):
    return pd.DataFrame(rows, columns=["region", "amount"])


def test_contributions_sum_exactly_to_the_delta():
    before = _frame([("west", 100), ("east", 50)])
    after = _frame([("west", 120), ("east", 40)])
    out = decompose_sum(before, after, "amount", "region")
    assert out["total_before"] == 150
    assert out["total_after"] == 160
    assert out["delta"] == 10
    assert sum(c["contribution"] for c in out["contributions"]) == out["delta"]
    assert out["receipt"] is True


def test_a_category_only_in_after_contributes_its_full_value():
    before = _frame([("west", 100)])
    after = _frame([("west", 100), ("new", 30)])
    out = decompose_sum(before, after, "amount", "region")
    new = next(c for c in out["contributions"] if c["category"] == "new")
    assert new["before"] == 0 and new["after"] == 30 and new["contribution"] == 30
    assert sum(c["contribution"] for c in out["contributions"]) == out["delta"] == 30


def test_a_category_only_in_before_contributes_negative():
    before = _frame([("west", 100), ("gone", 40)])
    after = _frame([("west", 100)])
    out = decompose_sum(before, after, "amount", "region")
    gone = next(c for c in out["contributions"] if c["category"] == "gone")
    assert gone["contribution"] == -40
    assert out["delta"] == -40


def test_contributions_ranked_by_absolute_impact():
    before = _frame([("a", 100), ("b", 100), ("c", 100)])
    after = _frame([("a", 90), ("b", 130), ("c", 105)])
    out = decompose_sum(before, after, "amount", "region")
    order = [c["category"] for c in out["contributions"]]
    assert order == ["b", "a", "c"]  # +30, -10, +5 by absolute impact


def test_zero_delta_is_reported_without_dividing_by_zero():
    before = _frame([("a", 100), ("b", 50)])
    after = _frame([("a", 90), ("b", 60)])
    out = decompose_sum(before, after, "amount", "region")
    assert out["delta"] == 0
    assert out["receipt"] is True  # -10 and +10 still sum to the (zero) delta
    assert all(c["share"] is None for c in out["contributions"])  # share undefined
