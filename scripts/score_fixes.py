"""Score a cleaned dataset against a Raha dirty/clean pair (dev-only; R16/AC8).

Cell-level semantics: a cell *should change* where dirty != truth, *was changed*
where dirty != cleaned, and a change is *correct* where cleaned == truth. All
equality is NaN-safe (two missing cells are equal) on stripped-string values, so
dtype changes (e.g. "12" parsed to 12.0) don't mask value equality.

Usage: uv run python scripts/score_fixes.py <cleaned_file> <dataset_name>
where cleaned_file is .parquet or .csv and dataset_name picks
data/raha/<name>/{dirty,clean}.csv. Never wired into the agent flow — a private
scoring habit, per the brief.
"""

import argparse
from pathlib import Path

import pandas as pd

RAHA_ROOT = Path(__file__).resolve().parents[1] / "data" / "raha"

# Sentinel for missing cells: no real value can contain NUL, so a plain string
# comparison makes NaN == NaN true and NaN != anything-else true.
_MISSING = "\x00NA\x00"


def _norm(value: object) -> str:
    """Stripped-string view of one cell; missing and empty collapse to a sentinel."""
    if pd.isna(value):
        return _MISSING
    s = str(value).strip()
    return s or _MISSING


def _cells(df: pd.DataFrame, cols: list[str], n_rows: int) -> list[list[str]]:
    """Positional slice of `df` as rows of normalized cell strings."""
    sub = df[cols].head(n_rows)
    return [[_norm(v) for v in row] for row in sub.itertuples(index=False, name=None)]


def score(dirty: pd.DataFrame, cleaned: pd.DataFrame, truth: pd.DataFrame) -> dict:
    """Precision/recall/F1 of cell-level changes in `cleaned`, judged by `truth`.

    P1 alignment limitation: fixes may drop duplicate rows or add columns, and
    Raha pairs sometimes rename columns (beers: beer_name vs beer-name), so we
    compare only the intersection of column names (in dirty's order) and the
    first min-length rows *by position*. Cells outside that window are invisible
    to the score. Fine while fixes are column-local and row-stable; revisit if
    fixes start reordering or inserting rows.
    """
    assert len(dirty) == len(truth), "dirty/truth must be a row-aligned pair"
    cols = [c for c in dirty.columns if c in cleaned.columns and c in truth.columns]
    n_rows = min(len(dirty), len(cleaned), len(truth))
    assert cols and n_rows, "no overlapping cells to score"

    d, c, t = (_cells(df, cols, n_rows) for df in (dirty, cleaned, truth))
    n_changed = n_should = n_correct = 0
    for d_row, c_row, t_row in zip(d, c, t, strict=True):
        for dv, cv, tv in zip(d_row, c_row, t_row, strict=True):
            if dv != tv:
                n_should += 1
            if dv != cv:
                n_changed += 1
                if cv == tv:
                    n_correct += 1

    precision = n_correct / n_changed if n_changed else 0.0
    recall = n_correct / n_should if n_should else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "changed": n_changed,
        "should_change": n_should,
        "correct_changes": n_correct,
    }


def read_table(path: Path) -> pd.DataFrame:
    """Read .parquet as-is; .csv all-strings with sentinels kept literal.

    keep_default_na=False stops pandas turning "N/A" into NaN — the dirt we are
    scoring. Empty cells arrive as "" and _norm folds them into missing.
    """
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cleaned_file", type=Path, help=".parquet or .csv fix output")
    ap.add_argument("dataset_name", help="picks data/raha/<name>/{dirty,clean}.csv")
    args = ap.parse_args()

    pair = RAHA_ROOT / args.dataset_name
    if not (pair / "dirty.csv").exists():
        ap.error(f"no {pair / 'dirty.csv'} — run scripts/fetch_raha.py")
    s = score(
        read_table(pair / "dirty.csv"),
        read_table(args.cleaned_file),
        read_table(pair / "clean.csv"),
    )
    print(
        f"{args.dataset_name}: P={s['precision']:.2f} R={s['recall']:.2f} "
        f"F1={s['f1']:.2f} (changed {s['changed']}, should {s['should_change']}, "
        f"correct {s['correct_changes']})"
    )


if __name__ == "__main__":
    main()
