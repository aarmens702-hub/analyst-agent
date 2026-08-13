import pandas as pd

def test_fix_clears_the_disease():
    dirty = pd.DataFrame({
        "col": ["empty", "EMPTY ", 5, "missing", "not available", "ok"]
    })
    out = fix(dirty, ["col"])
    assert out["col"].isna().sum() == 4
    assert (out["col"].astype(str).str.lower().str.strip() == "empty").sum() == 0
    assert (out["col"].astype(str).str.lower().str.strip() == "missing").sum() == 0
    assert out.loc[2, "col"] == 5
    assert out.loc[5, "col"] == "ok"

def test_fix_leaves_absent_columns_alone():
    clean = pd.DataFrame({"other": ["a", "b"]})
    out = fix(clean, ["missing_col"])
    assert out.equals(clean)
