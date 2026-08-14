import pandas as pd

def test_fix_clears_the_disease():
    dirty = pd.DataFrame({
        "col": [" Professional Services ", "Retail ", "wholesale", None, 123],
        "other": [" keep ", "as is ", " no ", " x ", " y "]
    })

    out = fix(dirty, ["col"])

    assert out["col"].tolist() == ["Professional Services", "Retail", "wholesale", None, 123]
    assert out["other"].tolist() == [" keep ", "as is ", " no ", " x ", " y "]

    # Original frame is not mutated.
    assert dirty.loc[0, "col"] == " Professional Services "

    # Missing columns are left alone rather than raising.
    untouched = fix(dirty, ["missing"])
    assert untouched.equals(dirty)
