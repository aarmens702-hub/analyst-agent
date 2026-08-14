import pandas as pd

def test_fix_clears_the_disease():
    dirty = pd.DataFrame({"amount": ["26,594.25", "37,224.00", "121,485.23", "1,234"]})
    out = fix(dirty, ["amount"])
    assert pd.api.types.is_numeric_dtype(out["amount"])
    assert out["amount"].notna().all()
    assert out["amount"].astype(str).str.contains(",").sum() == 0

def test_fix_leaves_absent_and_clean_columns_alone():
    clean = pd.DataFrame({"amount": [1.25, 2.50], "note": ["a", "b"]})
    out = fix(clean, ["missing", "note", "amount"])
    assert out["amount"].equals(clean["amount"])
    assert out["note"].equals(clean["note"])
    assert "missing" not in out.columns
