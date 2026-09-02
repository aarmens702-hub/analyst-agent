"""Corruption injectors (spec R2): one function per plantable disease,
registered in INJECTORS by @injector(id).

Anti-circularity: written only from the taxonomy's description of what each
disease IS, never from src/crivo/detect.py or autoclean.py — this bench
grades that code later, and a detector-shaped injector would be circular.

Excluded: disease 20 (schema-drift-across-files) is inherently multi-file —
it needs two datasets sharing a key with divergently-named/typed columns to
reconcile, not a single frame, so it has no injector here. Every other
disease in 1..22 is implemented.

Coordinate stability: cell-granular injectors modify values in place; row-
granular injectors (9, 10, 21) only ever append at the end, positions
recorded in Corruption.rows; column-granular injectors (18, 19) touch no
cell coordinates at all. Existing rows are never reordered.

Each injector's "pristine" is its own `frame` argument, not some separately-
threaded original — when corrupt() chains diseases, stage N's truth is
relative to stage N-1's output. That is what makes a positional pristine-to-
dirty diff for a SINGLE disease reconstructible even from a multi-disease
corpus, and why each injector can be tested standalone.

Auto-pick (columns=None) is deterministic — the first applicable column in
frame order, never rng-drawn — so which column gets hit doesn't move when an
earlier injector's rng consumption changes for unrelated reasons.
"""

from collections.abc import Callable

import numpy as np
import pandas as pd

from bench.truth import Cell, Corruption, GroundTruth, frame_sha256

INJECTORS: dict[int, Callable] = {}


def injector(disease_id: int):
    """Register a plant function under its taxonomy id."""

    def decorate(fn: Callable) -> Callable:
        INJECTORS[disease_id] = fn
        return fn

    return decorate


def _json_safe(value):
    """Cell.original/corrupted must be JSON-safe: timestamps as str, numpy
    scalars unwrapped to plain float/int, everything else passed through."""
    if isinstance(value, pd.Timestamp):
        return str(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _count(rate: float, n: int) -> int:
    """Rows/cells a rate-driven disease hits — always at least one, so a
    sweep over every injector at the default rate never plants zero."""
    return max(1, int(rate * n))


def _pick_column(
    candidates: list[str], columns: list[str] | None, disease_id: int, name: str
) -> str:
    """columns=None auto-picks the first applicable column (deterministic:
    frame order, not rng — so which column gets hit doesn't move when an
    earlier injector's rng consumption changes for unrelated reasons).
    Nothing applicable is never silently skipped."""
    if columns:
        chosen = [c for c in columns if c in candidates]
        if not chosen:
            raise ValueError(
                f"disease {disease_id} ({name}): none of {columns} is an applicable column"
            )
        return chosen[0]
    if not candidates:
        raise ValueError(f"disease {disease_id} ({name}): no applicable column found")
    return candidates[0]


def _holder_for(frame: pd.DataFrame, col: str) -> pd.Series:
    """A copy of `col` able to hold a foreign scalar (e.g. a sentinel string)
    without raising — pandas 3's StringDtype already can; anything else needs
    casting to object first (this IS part of several diseases: a numeric or
    datetime column degrading to object is the disease, not a side effect)."""
    if pd.api.types.is_string_dtype(frame[col]):
        return frame[col].copy()
    return frame[col].astype(object)


_MONEY_VARIANTS = 3


def _money_variant(value: float, kind: int) -> str:
    """One damaged text rendering of a positive float."""
    if kind == 0:
        return f"{value:,.2f}"  # thousands comma: "1,234.50"
    if kind == 1:
        return f"${value:.2f}"  # currency prefix: "$12.00"
    return f"({value:.2f})"  # accounting negation: "(45.00)"


@injector(1)
def inject_d1(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """numbers-as-strings: a float column becomes object dtype; every cell
    becomes its (clean, 2dp) string form, and the rate-selected cells get a
    damaged format on top. Only the damaged cells are recorded — the dtype
    degrade to object is part of the disease, not a separately-tracked cell."""
    candidates = [c for c in frame.columns if pd.api.types.is_float_dtype(frame[c])]
    col = _pick_column(candidates, columns, 1, "numbers-as-strings")
    n = len(frame)
    stringified = frame[col].map(lambda v: f"{v:.2f}").astype(object)
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    variant_idx = rng.integers(0, _MONEY_VARIANTS, size=k)
    cells = []
    for row, v in zip(rows, variant_idx):
        original = frame[col].iat[int(row)]
        baseline = f"{float(original):.2f}"
        corrupted = _money_variant(float(original), int(v))
        if corrupted == baseline:
            # thousands-comma is a no-op below 1000 — recorded cells must be
            # real corruptions, so fall back to a form that always differs
            corrupted = _money_variant(float(original), 2)
        stringified.iat[int(row)] = corrupted
        cells.append(
            Cell(
                row=int(row),
                column=col,
                original=_json_safe(original),
                corrupted=corrupted,
            )
        )
    out = frame.copy()
    out[col] = stringified
    truth.corruptions.append(
        Corruption(
            disease=1,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="column stringified",
        )
    )
    return out


_SENTINELS = ("-999", "N/A", "NULL", "?", "")


@injector(4)
def inject_d4(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """sentinel-missing: rate of cells in a chosen column replaced by a
    sentinel drawn from a fixed vocabulary; untouched cells keep their exact
    pristine value (only their column's dtype may degrade to hold the mix)."""
    col = _pick_column(list(frame.columns), columns, 4, "sentinel-missing")
    n = len(frame)
    holder = _holder_for(frame, col)
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    sentinel_idx = rng.integers(0, len(_SENTINELS), size=k)
    cells = []
    for row, s in zip(rows, sentinel_idx):
        original = frame[col].iat[int(row)]
        sentinel = _SENTINELS[s]
        holder.iat[int(row)] = sentinel
        cells.append(
            Cell(
                row=int(row),
                column=col,
                original=_json_safe(original),
                corrupted=sentinel,
            )
        )
    out = frame.copy()
    out[col] = holder
    truth.corruptions.append(
        Corruption(
            disease=4,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="sentinel missing-value codes",
        )
    )
    return out


def _numeric_columns(frame: pd.DataFrame) -> list[str]:
    return [
        c
        for c in frame.columns
        if pd.api.types.is_numeric_dtype(frame[c])
        and not pd.api.types.is_bool_dtype(frame[c])
    ]


def _nonneg_numeric_columns(frame: pd.DataFrame) -> list[str]:
    return [c for c in _numeric_columns(frame) if (frame[c].dropna() >= 0).all()]


def _datetime_columns(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if pd.api.types.is_datetime64_any_dtype(frame[c])]


def _text_columns(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if pd.api.types.is_string_dtype(frame[c])]


def _id_columns(frame: pd.DataFrame) -> list[str]:
    """String columns that are fully unique — the closest a frame gets to
    declaring "this is a key" without out-of-band schema metadata."""
    return [c for c in _text_columns(frame) if frame[c].is_unique]


def _nonascii_text_columns(frame: pd.DataFrame) -> list[str]:
    out = []
    for c in _text_columns(frame):
        if frame[c].astype(str).str.contains(r"[^\x00-\x7f]", regex=True).any():
            out.append(c)
    return out


def _find_start_end(
    frame: pd.DataFrame, columns: list[str] | None
) -> tuple[str, str] | None:
    """A start/end datetime pair has no schema metadata to find it by except
    column names — "start"/"end" are the canonical names typed_frame uses."""
    if columns:
        if len(columns) != 2:
            raise ValueError(
                "disease 12 (field-contradictions): columns must be [start, end]"
            )
        return columns[0], columns[1]
    dt_cols = _datetime_columns(frame)
    starts = [c for c in dt_cols if "start" in c.lower()]
    ends = [c for c in dt_cols if "end" in c.lower()]
    if starts and ends:
        return starts[0], ends[0]
    return None


def _find_lat_lon(
    frame: pd.DataFrame, columns: list[str] | None
) -> tuple[str, str] | None:
    if columns:
        if len(columns) != 2:
            raise ValueError(
                "disease 14 (broken-coordinates): columns must be [lat, lon]"
            )
        return columns[0], columns[1]
    lats = [
        c
        for c in frame.columns
        if "lat" in c.lower() and pd.api.types.is_numeric_dtype(frame[c])
    ]
    lons = [
        c
        for c in frame.columns
        if "lon" in c.lower() and pd.api.types.is_numeric_dtype(frame[c])
    ]
    if lats and lons:
        return lats[0], lons[0]
    return None


@injector(2)
def inject_d2(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """dates-as-strings: datetime column -> object; every cell rendered in
    one consistent "%Y-%m-%d" string form. All cells recorded."""
    col = _pick_column(_datetime_columns(frame), columns, 2, "dates-as-strings")
    original = frame[col]
    formatted = original.dt.strftime("%Y-%m-%d").astype(object)
    out = frame.copy()
    out[col] = formatted
    cells = tuple(
        Cell(
            row=i, column=col, original=str(original.iat[i]), corrupted=formatted.iat[i]
        )
        for i in range(len(frame))
    )
    truth.corruptions.append(
        Corruption(
            disease=2,
            columns=(col,),
            granularity="cell",
            cells=cells,
            note="uniform %Y-%m-%d string format",
        )
    )
    return out


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%b %d, %Y")


@injector(3)
def inject_d3(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """mixed-date-formats: datetime column -> object; each cell rendered in
    a randomly-chosen one of four incompatible string formats. All recorded."""
    col = _pick_column(_datetime_columns(frame), columns, 3, "mixed-date-formats")
    original = frame[col]
    n = len(frame)
    fmt_idx = rng.integers(0, len(_DATE_FORMATS), size=n)
    corrupted_values = [
        original.iat[i].strftime(_DATE_FORMATS[fmt_idx[i]]) for i in range(n)
    ]
    out = frame.copy()
    out[col] = pd.Series(corrupted_values, dtype=object)
    cells = tuple(
        Cell(
            row=i,
            column=col,
            original=str(original.iat[i]),
            corrupted=corrupted_values[i],
        )
        for i in range(n)
    )
    truth.corruptions.append(
        Corruption(
            disease=3,
            columns=(col,),
            granularity="cell",
            cells=cells,
            note="mixed date string formats",
        )
    )
    return out


@injector(5)
def inject_d5(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """suppression-codes: numeric column: rate of cells replaced by a
    suppression marker ("<5" or "SUPPRESSED"); column degrades to object."""
    col = _pick_column(_numeric_columns(frame), columns, 5, "suppression-codes")
    n = len(frame)
    holder = frame[col].astype(object)
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    variant_idx = rng.integers(0, 2, size=k)
    cells = []
    for row, v in zip(rows, variant_idx):
        row = int(row)
        original = frame[col].iat[row]
        corrupted = "<5" if v == 0 else "SUPPRESSED"
        holder.iat[row] = corrupted
        cells.append(
            Cell(
                row=row, column=col, original=_json_safe(original), corrupted=corrupted
            )
        )
    out = frame.copy()
    out[col] = holder
    truth.corruptions.append(
        Corruption(
            disease=5,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="suppression codes",
        )
    )
    return out


def _whitespace_variant(value: str, kind: int) -> str:
    if kind == 0:
        return "  " + value
    if kind == 1:
        return value + "  "
    if kind == 2:
        return value.replace(" ", "  ") if " " in value else value + "  "
    return " " + value  # nbsp prefix — looks like a space, isn't one


@injector(6)
def inject_d6(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """whitespace-damage: text/category column: rate of cells get a leading,
    trailing, doubled-internal, or nbsp-prefixed space variant."""
    col = _pick_column(_text_columns(frame), columns, 6, "whitespace-damage")
    n = len(frame)
    out = frame.copy()
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    variant_idx = rng.integers(0, 4, size=k)
    cells = []
    for row, v in zip(rows, variant_idx):
        row = int(row)
        original = frame[col].iat[row]
        corrupted = _whitespace_variant(str(original), int(v))
        out.at[row, col] = corrupted
        cells.append(
            Cell(
                row=row, column=col, original=_json_safe(original), corrupted=corrupted
            )
        )
    truth.corruptions.append(
        Corruption(
            disease=6,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="whitespace damage",
        )
    )
    return out


def _case_variant(value: str, kind: int) -> str:
    # case-only on purpose: a whitespace-padded variant is d6's disease, and
    # planting it here would teach the bench to reward misdiagnosis
    if kind == 0:
        return value.upper()
    if kind == 1:
        return value.lower()
    return value.capitalize()


@injector(7)
def inject_d7(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """case-spelling-variants: a REPEATED-vocab text column: rate of cells
    re-cased variants of the SAME value. The disease is one entity under
    several spellings, so it needs values that recur — a near-unique id
    column recased is undetectable by construction and an unfair plant."""
    repeated = [
        c
        for c in _text_columns(frame)
        if len(frame) and frame[c].nunique(dropna=True) / len(frame) <= 0.5
    ]
    col = _pick_column(repeated, columns, 7, "case-spelling-variants")
    n = len(frame)
    out = frame.copy()
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    variant_idx = rng.integers(0, 3, size=k)
    cells = []
    for row, v in zip(rows, variant_idx):
        row = int(row)
        original = frame[col].iat[row]
        text = str(original)
        corrupted = _case_variant(text, int(v))
        if corrupted == text:
            corrupted = text.swapcase()  # always differs for alphabetic vocab
        if corrupted == text:
            continue  # no alphabetic material to recase — record no lie
        out.at[row, col] = corrupted
        cells.append(
            Cell(
                row=row, column=col, original=_json_safe(original), corrupted=corrupted
            )
        )
    truth.corruptions.append(
        Corruption(
            disease=7,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="case/spelling variants of the same value",
        )
    )
    return out


@injector(8)
def inject_d8(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """encoding-mojibake: text/category column with non-ascii content:
    affected cells re-encoded utf-8 -> latin-1 ("é" becomes "Ã©")."""
    col = _pick_column(_nonascii_text_columns(frame), columns, 8, "encoding-mojibake")
    values = frame[col].astype(str)
    eligible = [i for i, v in enumerate(values) if not v.isascii()]
    k = min(_count(rate, len(eligible)), len(eligible))
    rows = rng.choice(eligible, size=k, replace=False)
    out = frame.copy()
    cells = []
    for row in rows:
        row = int(row)
        original = frame[col].iat[row]
        corrupted = str(original).encode("utf-8").decode("latin-1")
        out.at[row, col] = corrupted
        cells.append(
            Cell(
                row=row, column=col, original=_json_safe(original), corrupted=corrupted
            )
        )
    truth.corruptions.append(
        Corruption(
            disease=8,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="utf-8 -> latin-1 mojibake",
        )
    )
    return out


@injector(9)
def inject_d9(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """duplicate-rows: append max(1, int(rate*n)) exact copies of rng-chosen
    rows at the end. Whole-row disease — `columns` targets nothing here."""
    n = len(frame)
    k = max(1, int(rate * n))
    sources = rng.integers(0, n, size=k)
    new_rows = frame.iloc[sources].reset_index(drop=True)
    out = pd.concat([frame, new_rows], ignore_index=True)
    truth.corruptions.append(
        Corruption(
            disease=9,
            columns=tuple(frame.columns),
            granularity="row",
            rows=tuple(range(n, n + k)),
            note=f"exact duplicates of rows {[int(s) for s in sources]}",
        )
    )
    return out


@injector(10)
def inject_d10(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """near-duplicate-rows: like disease 9, but each appended copy gets one
    field tweaked (a numeric column +0.01, else a text column's case swapped)."""
    n = len(frame)
    k = max(1, int(rate * n))
    sources = rng.integers(0, n, size=k)
    num_cols = _numeric_columns(frame)
    text_cols = _text_columns(frame)
    if columns:
        tweak_col = columns[0]
    elif num_cols:
        tweak_col = num_cols[0]
    elif text_cols:
        tweak_col = text_cols[0]
    else:
        raise ValueError(
            "disease 10 (near-duplicate-rows): no numeric or text column to tweak"
        )
    is_numeric = tweak_col in num_cols
    appended = []
    for src in sources:
        row = frame.iloc[int(src)].copy()
        if is_numeric:
            row[tweak_col] = row[tweak_col] + 0.01
        else:
            text = str(row[tweak_col])
            row[tweak_col] = text.swapcase() if text.swapcase() != text else text + " "
        appended.append(row)
    new_rows = pd.DataFrame(appended).reset_index(drop=True)
    out = pd.concat([frame, new_rows], ignore_index=True)
    truth.corruptions.append(
        Corruption(
            disease=10,
            columns=(tweak_col,),
            granularity="row",
            rows=tuple(range(n, n + k)),
            note=f"near-duplicates of rows {[int(s) for s in sources]}, tweaked {tweak_col}",
        )
    )
    return out


@injector(11)
def inject_d11(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """key-violations: id column: rate of cells overwritten with a DIFFERENT
    existing id from the same column (its uniqueness is what makes any swap
    a genuine violation)."""
    col = _pick_column(_id_columns(frame), columns, 11, "key-violations")
    n = len(frame)
    values = frame[col].to_numpy()
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    out = frame.copy()
    cells = []
    for row in rows:
        row = int(row)
        original = values[row]
        others = np.delete(np.arange(n), row)
        donor = int(rng.choice(others))
        corrupted = values[donor]
        out.at[row, col] = corrupted
        cells.append(
            Cell(
                row=row,
                column=col,
                original=_json_safe(original),
                corrupted=_json_safe(corrupted),
            )
        )
    truth.corruptions.append(
        Corruption(
            disease=11,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="overwritten with a different existing id",
        )
    )
    return out


@injector(12)
def inject_d12(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """field-contradictions: needs a start/end datetime pair — for rate rows,
    set end = start - (1..30 days), which can never coincide with a genuine
    end (always >= start)."""
    pair = _find_start_end(frame, columns)
    if pair is None:
        raise ValueError(
            "disease 12 (field-contradictions): no start/end datetime pair found"
        )
    start_col, end_col = pair
    n = len(frame)
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    offsets = rng.integers(1, 31, size=k)
    out = frame.copy()
    cells = []
    for row, days in zip(rows, offsets):
        row = int(row)
        original = frame[end_col].iat[row]
        new_end = frame[start_col].iat[row] - pd.Timedelta(days=int(days))
        out.at[row, end_col] = new_end
        cells.append(
            Cell(
                row=row, column=end_col, original=str(original), corrupted=str(new_end)
            )
        )
    truth.corruptions.append(
        Corruption(
            disease=12,
            columns=(start_col, end_col),
            granularity="cell",
            cells=tuple(cells),
            note="end set before start (1-30 days)",
        )
    )
    return out


@injector(13)
def inject_d13(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """out-of-domain-values: numeric column whose pristine values are all
    >= 0: rate of cells negated, planting values outside the domain."""
    col = _pick_column(
        _nonneg_numeric_columns(frame), columns, 13, "out-of-domain-values"
    )
    n = len(frame)
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    out = frame.copy()
    cells = []
    for row in rows:
        row = int(row)
        original = frame[col].iat[row]
        corrupted = float(original) * -1
        out.at[row, col] = corrupted
        cells.append(
            Cell(
                row=row, column=col, original=_json_safe(original), corrupted=corrupted
            )
        )
    truth.corruptions.append(
        Corruption(
            disease=13,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="negative values planted outside the non-negative domain",
        )
    )
    return out


@injector(14)
def inject_d14(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """broken-coordinates: lat+lon columns: for rate rows, either swap lat/lon
    or set lat=999.0. Only the column(s) that actually changed get a cell —
    the 999.0 branch never touches lon."""
    pair = _find_lat_lon(frame, columns)
    if pair is None:
        raise ValueError(
            "disease 14 (broken-coordinates): no lat/lon column pair found"
        )
    lat_col, lon_col = pair
    n = len(frame)
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    swap_mask = rng.integers(0, 2, size=k)
    out = frame.copy()
    cells = []
    for row, swap in zip(rows, swap_mask):
        row = int(row)
        orig_lat = frame[lat_col].iat[row]
        orig_lon = frame[lon_col].iat[row]
        if swap:
            new_lat, new_lon = float(orig_lon), float(orig_lat)
            out.at[row, lat_col] = new_lat
            out.at[row, lon_col] = new_lon
            cells.append(
                Cell(
                    row=row,
                    column=lat_col,
                    original=_json_safe(orig_lat),
                    corrupted=new_lat,
                )
            )
            cells.append(
                Cell(
                    row=row,
                    column=lon_col,
                    original=_json_safe(orig_lon),
                    corrupted=new_lon,
                )
            )
        else:
            new_lat = 999.0
            out.at[row, lat_col] = new_lat
            cells.append(
                Cell(
                    row=row,
                    column=lat_col,
                    original=_json_safe(orig_lat),
                    corrupted=new_lat,
                )
            )
    truth.corruptions.append(
        Corruption(
            disease=14,
            columns=(lat_col, lon_col),
            granularity="cell",
            cells=tuple(cells),
            note="swapped lat/lon or lat set to 999.0",
        )
    )
    return out


@injector(15)
def inject_d15(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """statistical-outliers: numeric column: rate of cells scaled x1000."""
    col = _pick_column(_numeric_columns(frame), columns, 15, "statistical-outliers")
    n = len(frame)
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    out = frame.copy()
    cells = []
    for row in rows:
        row = int(row)
        original = frame[col].iat[row]
        corrupted = float(original) * 1000
        out.at[row, col] = corrupted
        cells.append(
            Cell(
                row=row, column=col, original=_json_safe(original), corrupted=corrupted
            )
        )
    truth.corruptions.append(
        Corruption(
            disease=15,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="values scaled x1000 into outlier range",
        )
    )
    return out


_UNIT_FACTORS = (0.3048, 100.0)


@injector(16)
def inject_d16(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """unit-heterogeneity: numeric column: rate of cells multiplied by ONE
    systematic factor (0.3048 or 100), same factor for every affected cell."""
    col = _pick_column(_numeric_columns(frame), columns, 16, "unit-heterogeneity")
    n = len(frame)
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    factor = float(rng.choice(_UNIT_FACTORS))
    out = frame.copy()
    cells = []
    for row in rows:
        row = int(row)
        original = frame[col].iat[row]
        corrupted = float(original) * factor
        out.at[row, col] = corrupted
        cells.append(
            Cell(
                row=row, column=col, original=_json_safe(original), corrupted=corrupted
            )
        )
    truth.corruptions.append(
        Corruption(
            disease=16,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note=f"unit factor x{factor} applied inconsistently",
        )
    )
    return out


@injector(17)
def inject_d17(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """packed-fields: text/category column: rate of cells become "a|b" or
    "a; b" joining the original value with another vocabulary value."""
    col = _pick_column(_text_columns(frame), columns, 17, "packed-fields")
    n = len(frame)
    all_values = frame[col].astype(str).unique().tolist()
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    sep_idx = rng.integers(0, 2, size=k)
    out = frame.copy()
    cells = []
    for row, s in zip(rows, sep_idx):
        row = int(row)
        original = frame[col].iat[row]
        others = [v for v in all_values if v != str(original)]
        donor = str(rng.choice(others)) if others else str(original)
        sep = "|" if s == 0 else "; "
        corrupted = f"{original}{sep}{donor}"
        out.at[row, col] = corrupted
        cells.append(
            Cell(
                row=row, column=col, original=_json_safe(original), corrupted=corrupted
            )
        )
    truth.corruptions.append(
        Corruption(
            disease=17,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="packed multi-value field",
        )
    )
    return out


@injector(18)
def inject_d18(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """header-damage: rename the target column to "Unnamed: 3" or add
    surrounding spaces. Apply this one LAST when combining with cell/row
    diseases in the same corrupt() call — a rename orphans any later
    injector's column-name lookup."""
    target = _pick_column(list(frame.columns), columns, 18, "header-damage")
    variant = int(rng.integers(0, 2))
    new_name = "Unnamed: 3" if variant == 0 else f" {target} "
    out = frame.rename(columns={target: new_name})
    truth.corruptions.append(
        Corruption(disease=18, columns=(target,), granularity="column", note=new_name)
    )
    return out


@injector(19)
def inject_d19(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """empty-constant-columns: entire column set to one constant, or to
    all-NaN."""
    target = _pick_column(list(frame.columns), columns, 19, "empty-constant-columns")
    out = frame.copy()
    if bool(rng.integers(0, 2)):
        out[target] = np.nan
        note = f"{target} set to all-NaN"
    else:
        constant = frame[target].iloc[0]
        out[target] = constant
        note = f"{target} set to constant {constant!r}"
    truth.corruptions.append(
        Corruption(disease=19, columns=(target,), granularity="column", note=note)
    )
    return out


@injector(21)
def inject_d21(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """aggregate-rows: append one TOTAL row — numeric columns summed, id/text
    columns get "TOTAL", datetime columns get NaT (mixing a "TOTAL" string
    into a datetime64 column would force the WHOLE column to object dtype,
    corrupting the untouched pristine rows' dtype, not just the new one)."""
    n = len(frame)
    num_cols = set(_numeric_columns(frame))
    dt_cols = set(_datetime_columns(frame))
    total = {
        c: (frame[c].sum() if c in num_cols else pd.NaT if c in dt_cols else "TOTAL")
        for c in frame.columns
    }
    out = pd.concat([frame, pd.DataFrame([total])], ignore_index=True)
    truth.corruptions.append(
        Corruption(
            disease=21,
            columns=tuple(frame.columns),
            granularity="row",
            rows=(n,),
            note="appended aggregate TOTAL row",
        )
    )
    return out


def _trailing_digits(value: str) -> str | None:
    i = len(value)
    while i > 0 and value[i - 1].isdigit():
        i -= 1
    digits = value[i:]
    return digits or None


@injector(22)
def inject_d22(
    frame: pd.DataFrame,
    truth: GroundTruth,
    rng: np.random.Generator,
    columns: list[str] | None = None,
    rate: float = 0.1,
) -> pd.DataFrame:
    """id-numeric-corruption: id column: rate of cells Excel-damaged — strip
    prefix+leading zeros, or render as scientific notation."""
    col = _pick_column(_id_columns(frame), columns, 22, "id-numeric-corruption")
    n = len(frame)
    k = _count(rate, n)
    rows = rng.choice(n, size=k, replace=False)
    variant_idx = rng.integers(0, 2, size=k)
    out = frame.copy()
    cells = []
    for row, v in zip(rows, variant_idx):
        row = int(row)
        original = frame[col].iat[row]
        digits = _trailing_digits(str(original))
        if digits is None:
            continue
        value = int(digits)
        corrupted = str(value) if v == 0 else f"{value:.2E}"
        out.at[row, col] = corrupted
        cells.append(
            Cell(
                row=row, column=col, original=_json_safe(original), corrupted=corrupted
            )
        )
    if not cells:
        raise ValueError(
            "disease 22 (id-numeric-corruption): no digit-suffixed ids found to damage"
        )
    truth.corruptions.append(
        Corruption(
            disease=22,
            columns=(col,),
            granularity="cell",
            cells=tuple(cells),
            note="Excel-style id damage (stripped prefix or scientific form)",
        )
    )
    return out


def corrupt(
    frame: pd.DataFrame, diseases: list[int], seed: int, base: str
) -> tuple[pd.DataFrame, GroundTruth]:
    """Apply `diseases` in order over one rng stream; ground truth's
    n_rows/n_cols come from the pristine `frame`, frame_sha256 from the final
    dirty frame. Same (frame, diseases, seed, base) => identical dirty frame
    and manifest, forever.

    Put disease 18 (header-damage) last when combining with cell/row
    diseases: a rename would orphan any later injector's column-name lookup.
    """
    rng = np.random.default_rng(seed)
    truth = GroundTruth(
        seed=seed,
        base=base,
        n_rows=len(frame),
        n_cols=len(frame.columns),
        frame_sha256="",
    )
    dirty = frame
    for disease_id in diseases:
        fn = INJECTORS.get(disease_id)
        if fn is None:
            raise ValueError(
                f"corrupt(): no injector registered for disease {disease_id}"
            )
        dirty = fn(dirty, truth, rng)
    truth.frame_sha256 = frame_sha256(dirty)
    return dirty, truth
