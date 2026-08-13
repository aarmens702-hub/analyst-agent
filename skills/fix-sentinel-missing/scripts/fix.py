import pandas as pd

SENTINEL_TOKENS = {
    "", "empty", "missing", "null", "none", "nan", "n/a", "na",
    "unknown", "not available", "not applicable", "-", "--", "?",
}

def _is_missing_sentinel(value):
    if not isinstance(value, str):
        return False
    return value.strip().lower() in SENTINEL_TOKENS

def fix(df, columns):
    out = df.copy()
    if isinstance(columns, str):
        columns = [columns]
    for col in columns:
        if col in out:
            mask = out[col].map(_is_missing_sentinel).astype(bool)
            out[col] = out[col].mask(mask, float("nan"))
    return out
