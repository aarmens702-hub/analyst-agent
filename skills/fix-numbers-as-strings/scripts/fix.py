import pandas as pd

def fix(df, columns):
    out = df.copy()
    if isinstance(columns, str):
        columns = [columns]

    for col in columns:
        if col not in out.columns:
            continue
        if pd.api.types.is_numeric_dtype(out[col]):
            continue
        if not (pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col])):
            continue

        str_series = out[col].astype(str)
        cleaned = (
            str_series
            .str.replace(',', '', regex=False)
            .str.replace(r'[^\d.+-]', '', regex=True)
        )
        parsed = pd.to_numeric(cleaned, errors='coerce')

        missing_before = out[col].isna() | (str_series.str.strip() == '')
        if (parsed.notna() | missing_before).all():
            out[col] = parsed

    return out
