import pandas as pd

def test_fix_clears_the_disease():
    dirty = pd.DataFrame({
        "col": ["HM Treasury", "HM Treasury", "HM Treasury"],
        "keep": [1, 2, 3]
    })
    out = fix(dirty, ["col"])
    assert "col" not in out.columns
    assert list(out.columns) == ["keep"]
    assert len(out) == 3

    clean = pd.DataFrame({
        "col": [1, 2],
        "keep": [3, 4]
    })
    out2 = fix(clean, ["col", "missing"])
    assert list(out2.columns) == ["col", "keep"]
