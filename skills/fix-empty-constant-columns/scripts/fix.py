def fix(df, columns):
    out = df.copy()
    if isinstance(columns, str):
        columns = [columns]
    for col in columns:
        if col in out.columns and out[col].nunique(dropna=False) <= 1:
            out = out.drop(columns=[col])
    return out
