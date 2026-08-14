def fix(df, columns):
    out = df.copy()
    if columns is None:
        return out
    if isinstance(columns, str):
        columns = [columns]

    for col in columns:
        if col not in out.columns:
            continue
        out[col] = out[col].map(
            lambda value: value.strip() if isinstance(value, str) else value
        )

    return out
