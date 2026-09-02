"""FIXERS[23]: boolean-chaos is AUTO-fixable — one truth, many spellings,
canonicalised to a real boolean dtype and verified by the d23 signal going
quiet (arc W2)."""

import pandas as pd

from crivo.autoclean import clean


def test_boolean_chaos_is_fixed_to_real_booleans_and_verified():
    frame = pd.DataFrame(
        {
            "active": ["Y", "N", "yes", "no", "TRUE", "FALSE", "1", "0"] * 5,
            "amount": [float(i) for i in range(40)],
        }
    )
    cleaned, summary = clean(frame)
    applied = {a["disease"] for a in summary.applied}
    assert 23 in applied, summary.needs_review
    assert cleaned["active"].dtype == pd.BooleanDtype()
    assert bool(cleaned["active"].iloc[0]) is True  # "Y"
    assert bool(cleaned["active"].iloc[1]) is False  # "N"
    assert bool(cleaned["active"].iloc[4]) is True  # "TRUE"
    # input purity + a consistent column stays untouched
    assert frame["active"].iloc[0] == "Y"
    consistent = pd.DataFrame({"flag": ["Y", "N"] * 20, "v": range(40)})
    same, s2 = clean(consistent)
    assert 23 not in {a["disease"] for a in s2.applied}
    assert list(same["flag"]) == list(consistent["flag"])
