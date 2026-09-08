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
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

RAHA_ROOT = Path(__file__).resolve().parents[1] / "data" / "raha"

# Sentinel for missing cells: no real value can contain NUL, so a plain string
# comparison makes NaN == NaN true and NaN != anything-else true.
_MISSING = "\x00NA\x00"

# Marks a key whose cell was a float64 rather than a written literal. Same NUL
# trick: no real value carries it, and `_same` reads it to decide whether
# representation noise is in play.
_FLOAT = "\x00float\x00"

# What a float64 carries faithfully (15). Past it, `_same` demands an exact
# round-trip instead of rounding.
_FLOAT64_DIGITS = sys.float_info.dig


# A plain number, so "12.0", "12" and "1.2e+1" score as the same value. Leading
# zeros are excluded on purpose: losing them is disease 22, so "02115" must not
# equal 2115.
_PLAIN_NUMBER = re.compile(r"^[-+]?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?$")

# The shape `_canonical_number` emits: a mantissa with no trailing zero and an
# explicit exponent, or plain "0". A key that does not match this one is a
# literal, not a number, and no tolerance applies to it.
_CANONICAL = re.compile(r"0|-?[1-9]\d*e-?\d+")


def _canonical_number(s: str) -> str | None:
    """Exact comparison key for a numeric string, or None if it has no exact
    value: a literal Decimal rejects, or a non-finite one it accepts.

    Built from Decimal's digit tuple, which is exact at any length, rather than
    from a rounded rendering. Both obvious alternatives round: "%.12g" folds two
    17-digit ids differing in the last digit into one value, and Decimal's own
    normalize() does the same at the context precision (28 digits by default).
    Rounding here would score a repair that produced the wrong digits as perfect,
    so this drops no digit at all.

    Trailing zeros move into the exponent, so "1200", "1200.0" and "1.2E+3" share
    a key, and every zero (including "-0") keys as "0". The key is a comparison
    token, not a display form.
    """
    try:
        sign, digits, exponent = Decimal(s).as_tuple()
    except InvalidOperation:  # e.g. an exponent too large for Decimal to hold
        return None
    if not isinstance(exponent, int):
        # Decimal accepts "NaN", "sNaN" and "Infinity", whose digit tuples are
        # empty or (0,) with a letter exponent. Keying them from the digits
        # alone would collapse all of them onto plain 0.
        return None
    if not any(digits):
        return "0"
    kept = len(digits)
    while digits[kept - 1] == 0:
        kept -= 1
    mantissa = "".join(str(d) for d in digits[:kept])
    return f"{'-' if sign else ''}{mantissa}e{exponent + len(digits) - kept}"


def _norm(value: object) -> str:
    """Stripped-string view of one cell; missing and empty collapse to a sentinel,
    and numbers to an exact canonical form so formatting never masks value
    equality (and, being exact, never masks a difference either).

    Every key is exact, floats included: a float64 is keyed from its shortest
    round-trip repr, the one decimal string that reads back as this exact
    float and no other. What a float cell also carries, and a written literal
    does not, is representation noise from arithmetic, so its key is tagged
    and `_same` handles the noise at comparison time, on both sides at once.
    The earlier design rounded the float here instead, which rounded one side
    of a comparison and not the other. Only .parquet input reaches this
    branch; a CSV is read all-strings.

    Applied to every cell of all three frames, so both sides of every comparison
    in `score` are keyed the same way.
    """
    if pd.isna(value):
        return _MISSING
    if isinstance(value, float):  # float64, and np.float64 which subclasses it
        # float() first: numpy 2 reprs an np.float64 as "np.float64(3.3)"
        return _FLOAT + _key(repr(float(value)))
    return _key(str(value).strip())


def _key(s: str) -> str:
    """One cell's exact comparison key: a plain number canonicalized, anything
    else its own literal, an empty literal the missing sentinel."""
    if _PLAIN_NUMBER.match(s):
        canonical = _canonical_number(s)
        if canonical is not None:
            return canonical
    return s or _MISSING


def _significant(canonical: str) -> int:
    """How many digits a canonical key spends on its mantissa."""
    return len(canonical.partition("e")[0].lstrip("-"))


def _same(a: str, b: str) -> bool:
    """Do two keys name the same value?

    Keys are exact, so this is `==` plus one exception: a float64 cell, tagged
    by `_norm`. Exactness is tried first and settles most of it, the tag aside
    - a float that is the shortest round-trip of the truth literal keys
    identically to it. What the tag then allows for is the rest: a float's
    last digits can be noise from arithmetic rather than digits anybody wrote,
    so a comparison involving one rounds BOTH sides to the 15 significant
    digits a float64 carries faithfully. Both, because rounding only the float
    side is what scored an exact 16-digit round-trip as a wrong repair: the
    float was rounded away from a literal that was not.

    Where the tolerance stops is the deliberate refusal in here, and it turns
    on WHICH side is long rather than on how many are.

    - A side past 15 digits that is NOT a float is a written literal whose
      digits somebody typed, and float64 cannot be trusted to carry it. So
      nothing short of an exact round-trip counts against it. That refusal
      covers the lost id (12345678901234567 arriving as a float is lost, not
      rounded) AND the destroyed one: a cleaner writing 4111111111111110.0
      over a truth of "4111111111111111" is not a rounding, because float64
      holds that literal exactly. Asking whether BOTH sides were long let the
      second case through, since the damaged float is one digit SHORTER.
    - When neither side runs past 15 digits, both are inside what a float64
      carries exactly, so there is no noise to absorb and any difference
      between them is a real difference.

    Which leaves the one case the tolerance is for: a long float beside a
    short literal, where the float's extra digits are arithmetic noise.

    Not transitive, as no tolerance is. It is only ever asked about one pair.
    """
    if a == b:
        return True
    a_float, b_float = a.startswith(_FLOAT), b.startswith(_FLOAT)
    if not (a_float or b_float):
        return False
    a, b = a.removeprefix(_FLOAT), b.removeprefix(_FLOAT)
    if a == b:
        return True  # the float IS the exact shortest round-trip of the literal
    if not (_CANONICAL.fullmatch(a) and _CANONICAL.fullmatch(b)):
        return False
    a_long = _significant(a) > _FLOAT64_DIGITS
    b_long = _significant(b) > _FLOAT64_DIGITS
    if (a_long and not a_float) or (b_long and not b_float):
        return False  # a written literal past what a float64 carries faithfully
    if not (a_long or b_long):
        return False  # both exact at float64 precision: the difference is real
    rounded = f".{_FLOAT64_DIGITS - 1}e"
    return format(Decimal(a), rounded) == format(Decimal(b), rounded)


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
            if not _same(dv, tv):
                n_should += 1
            if not _same(dv, cv):
                n_changed += 1
                if _same(cv, tv):
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
