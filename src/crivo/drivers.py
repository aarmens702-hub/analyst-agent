"""Driver decomposition (capability roadmap B1.2).

"Why did this metric change" as ranked per-dimension contributions. The
receipt is arithmetic and exact: for an additive (sum) metric the total is
the sum over categories, so each category's contribution is simply its
after-minus-before, and those contributions sum to the observed delta by
construction. That exactness is the whole point for a receipts tool, so v1
covers sum metrics only; mix-vs-rate decomposition for ratios (where the
split into volume and rate effects is a modelling choice, not an identity)
is a follow-up. Pure pandas, no model call.
"""

from __future__ import annotations

import pandas as pd


def decompose_sum(
    before: pd.DataFrame, after: pd.DataFrame, value_col: str, by_col: str
) -> dict:
    """Decompose the change in sum(value_col) into per-category contributions.

    Each category's contribution is its after-total minus its before-total
    (a category on only one side counts as 0 on the other), so the
    contributions sum exactly to total_after - total_before. Returns the
    totals, the delta, the contributions ranked by absolute impact, and a
    `receipt` flag confirming the sum identity held.
    """
    b = before.groupby(by_col)[value_col].sum()
    a = after.groupby(by_col)[value_col].sum()
    categories = sorted(set(b.index) | set(a.index))

    total_before = float(b.sum())
    total_after = float(a.sum())
    delta = total_after - total_before

    contributions = []
    for cat in categories:
        cb = float(b.get(cat, 0.0))
        ca = float(a.get(cat, 0.0))
        contrib = ca - cb
        contributions.append(
            {
                "category": cat,
                "before": cb,
                "after": ca,
                "contribution": contrib,
                # share of the delta a contribution explains; undefined when
                # the net delta is zero (offsetting moves), reported as None
                "share": (contrib / delta) if delta else None,
            }
        )
    contributions.sort(key=lambda c: abs(c["contribution"]), reverse=True)

    summed = sum(c["contribution"] for c in contributions)
    receipt = abs(summed - delta) < 1e-9

    return {
        "metric": value_col,
        "by": by_col,
        "total_before": _clean(total_before),
        "total_after": _clean(total_after),
        "delta": _clean(delta),
        "contributions": [
            {
                **c,
                "before": _clean(c["before"]),
                "after": _clean(c["after"]),
                "contribution": _clean(c["contribution"]),
            }
            for c in contributions
        ],
        "receipt": receipt,
    }


def _clean(x: float):
    """Present a whole-number float as an int so receipts read cleanly."""
    return int(x) if float(x).is_integer() else x
