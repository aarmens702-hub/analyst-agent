"""The 22-disease detection engine (P1 R1-R4).

Runs inside the kernel, next to the data: pandas + stdlib only, no imports from
the rest of the package. Every signal is one function in REGISTRY, so the same
code that *finds* a disease is the code that *verifies* the fix cleared it
(detect_one) — detector and verifier cannot drift.

Findings never carry rows: counts, stats, and truncated samples only. Output is
plain JSON (numpy scalars cast, NaN dropped) because it crosses the kernel
boundary as a stdout line.

Taxonomy and detection signals: build-research PART 2.
"""

import math
import re
import unicodedata
from difflib import SequenceMatcher
from itertools import pairwise

import pandas as pd

# --- taxonomy ---------------------------------------------------------------

SLUGS = {
    1: "numbers-as-strings",
    2: "dates-as-strings",
    3: "mixed-date-formats",
    4: "sentinel-missing",
    5: "suppression-codes",
    6: "whitespace-damage",
    7: "case-spelling-variants",
    8: "encoding-mojibake",
    9: "duplicate-rows",
    10: "near-duplicate-rows",
    11: "key-violations",
    12: "field-contradictions",
    13: "out-of-domain-values",
    14: "broken-coordinates",
    15: "statistical-outliers",
    16: "unit-heterogeneity",
    17: "packed-fields",
    18: "header-damage",
    19: "empty-constant-columns",
    20: "schema-drift",
    21: "aggregate-rows",
    22: "id-numeric-corruption",
}

INDICATORS = frozenset({12, 15})  # detected, evidenced, never auto-fixed (R2)
FAMILY_ONLY = frozenset({20})  # needs several files; see detect_family (R3)
SINGLE_FRAME = tuple(d for d in sorted(SLUGS) if d not in FAMILY_ONLY)

REGISTRY: dict = {}  # disease -> fn(df, cols) -> list[finding]

# BC bounding box, disease 14 (the province our real datasets live in)
BC_LAT_MIN, BC_LAT_MAX = 48.0, 60.0
BC_LON_MIN, BC_LON_MAX = -139.0, -114.0

# --- shared vocabulary ------------------------------------------------------

MISSING_TOKENS = frozenset(
    {
        "n/a",
        "na",
        "n.a.",
        "n/a.",
        "#n/a",
        "none",
        "null",
        "nil",
        "nan",
        "unknown",
        "unk",
        "not available",
        "not applicable",
        "not specified",
        "missing",
        "empty",
        "blank",
        "-",
        "--",
        "?",
        "tbd",
        "no data",
    }
)
NUMERIC_SENTINELS = (-9999, -999, -99, -9, -1, 0, 9999, 99999, 999999)
SENTINEL_DATES = ("1900-01-01", "1899-12-30", "0001-01-01", "1970-01-01")

NUMERIC_WITH_UNIT = re.compile(
    r"^\s*[-+]?\s*[$€£¥]?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"\s*[%a-zA-Z°µ²³/.\- ]{0,20}\s*$"
)
LEADING_NUMBER = re.compile(
    r"^\s*([-+]?)\s*[$€£¥]?\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
)
SUPPRESSION_TOKEN = re.compile(r"^[<>]=?\s?\d+$|^[a-zA-Z*.]{1,3}$")
MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "Ð", "Ñ", "�")
ZERO_WIDTH = ("\u200b", "‌", "‍", "﻿")
# _finding's evidence must never carry a literal newline (single-line display
# downstream). Collapsing every whitespace run to guarantee that also erases
# meaningful evidence — disease 6 reports a double space by showing one — so
# only the characters that actually break single-line text are touched;
# ordinary spaces inside the message survive untouched.
EVIDENCE_LINEBREAKS = re.compile(r"[\r\n\t]+")
# Patterns handed to Series.str.match must carry no re flags: pandas rejects a
# compiled pattern whose flags differ from the call's. Character classes stand
# in for re.DOTALL, and values are case-folded before matching instead of re.I.
ROLLUP_LABEL = re.compile(
    r"^\s*(all\b|total\s*$|grand\s+total|sub\s*-?\s*total|overall\b|"
    r"all\s+others?|combined\s*$|aggregate\b)"
)
LATLON_PAIR = re.compile(r"^\s*-?\d{1,3}\.\d+\s*[,;]\s*-?\d{1,3}\.\d+\s*$")
JSON_ISH = re.compile(r"^\s*[\[{][\s\S]*[\]}]\s*$")
SIBLING_COLUMN = re.compile(r"^(?P<prefix>.+?)[ _-]?(?P<num>\d)$")

DATE_FAMILIES = (
    ("iso", re.compile(r"^\d{4}-\d{1,2}-\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")),
    ("slash", re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")),
    ("dash", re.compile(r"^\d{1,2}-\d{1,2}-\d{2,4}$")),
    ("dot", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$")),
    ("day-month-name", re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}$")),
    ("month-name-day", re.compile(r"^[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}$")),
    ("compact", re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")),
    ("time", re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*([aApP]\.?\s?[mM]\.?)?$")),
)
SLOTTED_FAMILIES = {"slash", "dash", "dot"}

DOMAIN_BOUNDS = (
    (re.compile(r"(^|_)age(_|$)"), 0.0, 120.0),
    (re.compile(r"(^|_)(pct|percent|percentage|rate|share)(_|$)"), 0.0, 100.0),
    (re.compile(r"(^|_)(year|yr)(_|$)"), 1800.0, 2100.0),
    (re.compile(r"(^|_)month(_|$)"), 1.0, 12.0),
    (re.compile(r"(^|_)hour(_|$)"), 0.0, 23.0),
    (
        re.compile(r"(^|_)(count|qty|quantity|num|number|population)(_|$)"),
        0.0,
        math.inf,
    ),
)

# unit conversions worth naming when a column turns out to be bimodal (16)
KNOWN_RATIOS = (
    (3.28084, "metres -> feet"),
    (2.54, "inches -> centimetres"),
    (1.60934, "miles -> kilometres"),
    (2.20462, "kilograms -> pounds"),
    (3.78541, "gallons -> litres"),
    (1.09361, "metres -> yards"),
    (6.89476, "psi -> kPa"),
    (100.0, "hundreds"),
    (1000.0, "thousands"),
)

ID_NAME = re.compile(r"(^|_)(zip|postal|code|folio|id|number|no|acct|account)(_|$)")
LAT_NAME = re.compile(r"(^|_)(lat|latitude)(_|$)")
LON_NAME = re.compile(r"(^|_)(lon|lng|long|longitude)(_|$)")
UNIT_NAME = re.compile(r"(^|_)(unit|units|uom|measure_unit)(_|$)")
PARAM_NAME = re.compile(
    r"(^|_)(param|parameter|measure|metric|variable|analyte|pollutant)(_|$)"
)

SHAPE_SAMPLE = 5_000  # rows consulted when deciding a column's shape
MAX_FUZZY_VALUES = 150  # pairwise similarity is O(n^2); cap the candidate set
FUZZY_THRESHOLD = 0.92
MAX_FD_PAIRS = 200
SAMPLE_LIMIT = 3  # how many example values ever leave the kernel


# --- plumbing ---------------------------------------------------------------


def register(disease: int):
    def wrap(fn):
        REGISTRY[disease] = fn
        return fn

    return wrap


def _jsonable(value):
    """Coerce numpy scalars, sets, and NaN into JSON that round-trips equal."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_jsonable(v) for v in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bool):
        return value
    if not isinstance(value, (str, bytes, int, float)) and hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            return str(value)
    if isinstance(value, bool):  # numpy bools land here after .item()
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else round(value, 6)
    if value is None or isinstance(value, (int, str)):
        return value
    return str(value)


def _finding(disease, columns, evidence, stats, grade, confidence) -> dict:
    return {
        "disease": int(disease),
        "slug": SLUGS[disease],
        "columns": [str(c) for c in columns],
        "evidence": EVIDENCE_LINEBREAKS.sub(" ", str(evidence)).strip(),
        "stats": _jsonable(stats),
        "grade": grade,
        "confidence": round(float(confidence), 3),
        "indicator": disease in INDICATORS,
    }


def _indices(df, columns) -> list:
    if not columns:
        return []
    wanted = {str(c) for c in columns}
    return [i for i in range(df.shape[1]) if str(df.columns[i]) in wanted]


def _targets(df, cols) -> list:
    """Positional column indices — positional so duplicate names still work."""
    return list(range(df.shape[1])) if cols is None else list(cols)


def _name(df, i) -> str:
    return str(df.columns[i])


def _key(df, i) -> str:
    """Column name normalised for name-pattern matching."""
    return re.sub(r"[^a-z0-9]+", "_", str(df.columns[i]).strip().lower())


def _is_texty(series) -> bool:
    return series.dtype == object or pd.api.types.is_string_dtype(series)


_VIEWS: dict = {}  # (id(df), kind, column) -> materialised column view


def _cached(df, i, kind, build):
    """Twenty-two signals read the same forty columns; deriving each view once
    per run is the difference between seconds and a minute on a wide frame.
    detect_all clears this, so a view never outlives the frame it came from."""
    key = (id(df), kind, i)
    if key not in _VIEWS:
        _VIEWS[key] = build()
    return _VIEWS[key]


def _text(df, i):
    """Non-blank values as stripped strings. Missing tokens stay in — several
    detectors are about them — but true blanks and NaN drop out."""

    def build():
        series = df.iloc[:, i]
        if not _is_texty(series):
            return None
        values = series.dropna().astype(str)
        return values[values.str.strip() != ""]

    return _cached(df, i, "text", build)


def _present(df, i):
    """Text values with missing-sentinels also treated as absent."""

    def build():
        values = _text(df, i)
        if values is None:
            return None
        return values[~values.str.strip().str.lower().isin(MISSING_TOKENS)]

    return _cached(df, i, "present", build)


def _numbers(df, i):
    def build():
        series = df.iloc[:, i]
        if pd.api.types.is_bool_dtype(series) or not pd.api.types.is_numeric_dtype(
            series
        ):
            return None
        return series.dropna()

    return _cached(df, i, "numbers", build)


def _folded(df, i):
    """Every row of a text column, case- and whitespace-normalised. Diseases 7
    and 10 both need it; computing it twice on a wide frame costs seconds."""

    def build():
        series = df.iloc[:, i]
        return _tidy(series.astype(str), fold=True) if _is_texty(series) else None

    return _cached(df, i, "folded", build)


def _norm(value: str) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value)).strip().casefold().split()
    )


def _tidy(values, fold: bool = False):
    """_norm over a whole column, vectorised — the per-cell version costs
    seconds on a 150k-row frame."""
    out = values.str.normalize("NFKC").str.replace(r"\s+", " ", regex=True).str.strip()
    return out.str.casefold() if fold else out


# disease 6 is whitespace/NBSP/zero-width damage (build-research PART 2, row
# 6) — not general Unicode compatibility folding. NFKC (what _tidy applies)
# expands characters like (TM) into multi-character forms, so comparing
# against it reports unrelated trademark signs as whitespace damage. Map only
# NBSP and the zero-width family to an ordinary space; everything else is
# left alone.
# Every Unicode space separator, mapped to an ordinary space. Built from the
# category rather than a hand-list because the hand-list was NBSP only, and
# pandas cannot close the gap: on Arrow-backed strings .str.replace uses RE2,
# whose \s is ASCII-only, so U+202F and U+2007 — exactly what Canadian
# government exports use for digit grouping — were neither normalised nor seen.
_UNICODE_SPACES = {c for c in range(0x110000) if unicodedata.category(chr(c)) == "Zs"}
# Invisible and meaningless: delete rather than space, so repairing
# 'Bud<ZWSP>weiser' yields 'Budweiser' and not a split word.
_ZERO_WIDTH_NOISE = {0x200B, 0xFEFF}
# ZWJ and ZWNJ are deliberately absent from both sets. They carry meaning —
# they build emoji sequences and Indic conjuncts — so touching them would both
# report 'woman technologist' as whitespace damage and split it while
# "repairing" it.
_WS_DAMAGE = {c: " " for c in _UNICODE_SPACES} | {c: None for c in _ZERO_WIDTH_NOISE}


def _ws_tidy(values):
    """Whitespace-only normalisation: Unicode spaces become an ordinary space,
    zero-width noise is removed, runs collapse, ends trim. Unlike _tidy, no
    NFKC — that expanded compatibility characters like the trademark sign and
    reported them as whitespace."""
    mapped = values.str.translate(_WS_DAMAGE)
    return mapped.str.replace(r"\s+", " ", regex=True).str.strip()


def _probe(values, limit: int = SHAPE_SAMPLE):
    """An evenly spaced subset. Shape questions ("do 80% of these look like
    coordinates?") are settled on a sample; counts that reach the user are
    always recomputed on the full column."""
    step = len(values) // limit
    return values.iloc[:: step + 1] if step else values


def _samples(values, limit: int = SAMPLE_LIMIT) -> str:
    seen = list(dict.fromkeys(str(v) for v in values))[:limit]
    return ", ".join(repr(s[:40]) for s in seen)


# --- 1. numbers as strings --------------------------------------------------


@register(1)
def _d01(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        values = _text(df, i)
        if values is None or len(values) < 8:
            continue
        if float(_probe(values).str.match(NUMERIC_WITH_UNIT).mean()) < 0.90:
            continue
        match_frac = float(values.str.match(NUMERIC_WITH_UNIT).mean())
        if match_frac < 0.90:
            continue
        direct = pd.to_numeric(values, errors="coerce").notna().mean()
        if direct > 0.95:  # already parses as-is: stored as text, no residue
            continue
        pulled = values.str.extract(LEADING_NUMBER)
        numeric = pd.to_numeric(
            (pulled[0].fillna("") + pulled[1].fillna("")).str.replace(
                ",", "", regex=False
            ),
            errors="coerce",
        )
        extract_frac = float(numeric.notna().mean())
        if extract_frac < 0.99:
            continue
        dirty = values[values.str.strip() != numeric.astype(str).str.strip()]
        out.append(
            _finding(
                1,
                [_name(df, i)],
                f"{len(values) - int(direct * len(values))}/{len(values)} values carry "
                f"currency/unit residue; samples: {_samples(dirty)}",
                {
                    "values": len(values),
                    "match_frac": match_frac,
                    "parse_frac": extract_frac,
                    "residue_frac": 1.0 - float(direct),
                },
                "AUTO",
                min(match_frac, extract_frac),
            )
        )
    return out


# --- 2/3. dates as strings, single or mixed format --------------------------


def _date_families(values) -> dict:
    hits = {}
    for family, pattern in DATE_FAMILIES:
        share = float(values.str.match(pattern).mean())
        if share > 0:
            hits[family] = share
    return hits


def _slot_ambiguity(values) -> bool:
    """True when every day/month slot is <= 12, so D/M vs M/D cannot be settled."""
    slots = values.str.extract(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-]")
    left = pd.to_numeric(slots[0], errors="coerce").dropna()
    right = pd.to_numeric(slots[1], errors="coerce").dropna()
    if left.empty or right.empty:
        return False
    return bool(left.max() <= 12 and right.max() <= 12)


def _date_scan(df, cols):
    for i in _targets(df, cols):
        values = _present(df, i)
        if values is None or len(values) < 8:
            continue
        # 0.90, not the research's 0.95: dirty date columns carry a tail of
        # one-off manglings that no single format claims (Raha flights: 94%).
        if sum(_date_families(_probe(values)).values()) < 0.90:
            continue
        families = {f: s for f, s in _date_families(values).items() if s >= 0.05}
        covered = sum(families.values())
        if not families or covered < 0.90:
            continue
        yield i, values, families, covered


@register(2)
def _d02(df, cols) -> list:
    out = []
    for i, values, families, covered in _date_scan(df, cols):
        if len(families) != 1:
            continue
        family = next(iter(families))
        out.append(
            _finding(
                2,
                [_name(df, i)],
                f"{len(values)} values are {family}-format date/time strings, "
                f"not datetime64; samples: {_samples(values)}",
                {"values": len(values), "family": family, "coverage": covered},
                "AUTO",
                covered,
            )
        )
    return out


@register(3)
def _d03(df, cols) -> list:
    out = []
    for i, values, families, covered in _date_scan(df, cols):
        if len(families) < 2:
            continue
        slotted = values[
            values.str.match(re.compile(r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$"))
        ]
        ambiguous = _slot_ambiguity(slotted) if len(slotted) else False
        ranked = sorted(families.items(), key=lambda kv: -kv[1])
        out.append(
            _finding(
                3,
                [_name(df, i)],
                f"{len(families)} date formats in one column "
                f"({', '.join(f'{f} {s:.0%}' for f, s in ranked)})"
                + ("; day/month order is ambiguous" if ambiguous else ""),
                {
                    "values": len(values),
                    "families": dict(ranked),
                    "ambiguous": ambiguous,
                    "coverage": covered,
                },
                "GATE",
                covered * (0.8 if ambiguous else 1.0),
            )
        )
    return out


# --- 4. sentinel missing ----------------------------------------------------


@register(4)
def _d04(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        values = _text(df, i)
        if values is not None and len(values) >= 8:
            lowered = values.str.strip().str.lower()
            hits = lowered.isin(MISSING_TOKENS) | lowered.isin(SENTINEL_DATES)
            count = int(hits.sum())
            if count >= 2 and count < len(values):
                # the tokens as they really appear, not normalised: the model
                # writes .replace(<this string>) and a case-folded quote
                # produces a fix that matches nothing
                tokens = sorted(set(values[hits].str.strip()))
                out.append(
                    _finding(
                        4,
                        [_name(df, i)],
                        f"{count}/{len(values)} values are missing-data tokens "
                        f"({', '.join(repr(t) for t in tokens[:4])}) masquerading as data",
                        {
                            "sentinel_count": count,
                            "tokens": tokens[:8],
                            "frac": count / len(values),
                        },
                        "AUTO",
                        min(0.99, 0.9 + count / len(values) / 10),
                    )
                )
            continue
        numbers = _numbers(df, i)
        if numbers is None or len(numbers) < 12:
            continue
        counts = numbers.value_counts()
        if len(counts) < 2:
            continue
        top, second = counts.index[0], counts.iloc[1]
        if top not in NUMERIC_SENTINELS or counts.iloc[0] <= 5 * second:
            continue
        out.append(
            _finding(
                4,
                [_name(df, i)],
                f"{int(counts.iloc[0])} rows hold {top!r} — "
                f"{counts.iloc[0] / second:.0f}x the next most common value, "
                "the signature of a missing-value placeholder",
                {
                    "sentinel_value": top,
                    "sentinel_count": int(counts.iloc[0]),
                    "next_count": int(second),
                    "frac": float(counts.iloc[0] / len(numbers)),
                },
                "HUMAN",  # a numeric 0 may be a real reading
                0.75,
            )
        )
    return out


# --- 5. suppression codes ---------------------------------------------------


@register(5)
def _d05(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        values = _text(df, i)
        if values is None or len(values) < 12:
            continue
        probe = _probe(values)
        if float(pd.to_numeric(probe, errors="coerce").notna().mean()) < 0.85:
            continue
        numeric = pd.to_numeric(
            values.str.replace(",", "", regex=False), errors="coerce"
        )
        parse_frac = float(numeric.notna().mean())
        if not 0.90 <= parse_frac < 1.0:
            continue
        residual = values[numeric.isna()].str.strip()
        residual = residual[~residual.str.lower().isin(MISSING_TOKENS)]
        tokens = sorted(set(residual))
        if not tokens or len(tokens) > 10:
            continue
        if not all(SUPPRESSION_TOKEN.match(t) for t in tokens):
            continue
        out.append(
            _finding(
                5,
                [_name(df, i)],
                f"{len(residual)} cells in an otherwise numeric column hold "
                f"suppression flags ({', '.join(repr(t) for t in tokens[:4])})",
                {
                    "tokens": tokens,
                    "flagged": len(residual),
                    "parse_frac": parse_frac,
                },
                "AUTO",
                0.9,
            )
        )
    return out


# --- 6. whitespace damage ---------------------------------------------------


@register(6)
def _d06(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        values = _text(df, i)
        if values is None or len(values) < 5:
            continue
        probe = _probe(values)
        if not bool((probe != _ws_tidy(probe)).any()):
            continue
        cleaned = _ws_tidy(values)
        dirty = values[values != cleaned]
        count = len(dirty)
        if count == 0:
            continue
        # counted from the same table the repair uses, so the number in the
        # evidence cannot drift from what the fix would actually touch
        exotic = int(values.map(lambda v: any(ord(c) in _WS_DAMAGE for c in v)).sum())
        out.append(
            _finding(
                6,
                [_name(df, i)],
                f"{count}/{len(values)} values carry stray whitespace"
                + (f" ({exotic} with NBSP/zero-width characters)" if exotic else "")
                + f"; samples: {_samples(dirty)}",
                {"count": count, "exotic": exotic, "values": len(values)},
                "AUTO",
                0.99,
            )
        )
    return out


# --- 7. case / spelling variants --------------------------------------------


def _digits_only_difference(a: str, b: str) -> bool:
    """True when the two strings differ only in digits — a real code, not a typo."""
    changed = ""
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b).get_opcodes():
        if tag != "equal":
            changed += a[i1:i2] + b[j1:j2]
    return bool(changed) and all(c.isdigit() or not c.isalnum() for c in changed)


def _fuzzy_pairs(counts) -> list:
    candidates = [v for v in counts.index if len(v) >= 6][:MAX_FUZZY_VALUES]
    pairs = []
    for x, a in enumerate(candidates):
        for b in candidates[x + 1 :]:
            if abs(len(a) - len(b)) > 3:
                continue
            matcher = SequenceMatcher(None, a, b)
            if matcher.real_quick_ratio() < FUZZY_THRESHOLD:
                continue
            if matcher.quick_ratio() < FUZZY_THRESHOLD:
                continue
            ratio = matcher.ratio()
            if ratio < FUZZY_THRESHOLD or _digits_only_difference(a, b):
                continue
            pairs.append((round(ratio, 3), a, b))
            if len(pairs) >= 50:
                return pairs
    return pairs


@register(7)
def _d07(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        values = _present(df, i)
        if values is None or len(values) < 10:
            continue
        raw = values.nunique()
        if raw < 2:
            continue
        normalised = _folded(df, i).loc[values.index]
        shrink = (raw - normalised.nunique()) / raw
        if shrink > 0.1:
            examples = values[normalised.duplicated(keep=False)]
            out.append(
                _finding(
                    7,
                    [_name(df, i)],
                    f"{raw} distinct values collapse to {normalised.nunique()} once case "
                    f"and whitespace are normalised; samples: {_samples(examples)}",
                    {
                        "distinct": int(raw),
                        "normalised": int(normalised.nunique()),
                        "shrink": shrink,
                    },
                    "AUTO",
                    0.95,
                )
            )
        if normalised.nunique() > max(50, 0.2 * len(values)):
            continue  # free text, not a category column: fuzzy clustering is noise
        pairs = _fuzzy_pairs(normalised.value_counts())
        if pairs:
            # _fuzzy_pairs clusters on folded text; report the value as it
            # really appears, or a model matching on the folded pair finds
            # nothing to fix — the same lesson as disease 4's quoting.
            original = values.groupby(normalised).first()
            shown = "; ".join(
                f"{original[a]!r}~{original[b]!r}" for _, a, b in pairs[:2]
            )
            out.append(
                _finding(
                    7,
                    [_name(df, i)],
                    f"{len(pairs)} value pairs are >={FUZZY_THRESHOLD:.0%} similar — "
                    f"probable spelling variants of one category: {shown}",
                    {
                        "fuzzy_pairs": len(pairs),
                        "distinct": int(normalised.nunique()),
                        "examples": [
                            [original[a], original[b]] for _, a, b in pairs[:5]
                        ],
                    },
                    "HUMAN",  # a canonical mapping is a judgement call
                    float(min(0.9, pairs[0][0])),
                )
            )
    return out


# --- 8. encoding mojibake ---------------------------------------------------


@register(8)
def _d08(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        values = _text(df, i)
        if values is None or len(values) < 5:
            continue
        hit = values.str.contains(
            "|".join(re.escape(m) for m in MOJIBAKE_MARKERS), regex=True
        )
        count = int(hit.sum())
        if count == 0:
            continue
        affected = values[hit]
        repaired = 0
        for value in affected.head(50):
            try:
                fixed = value.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if fixed != value and not any(m in fixed for m in MOJIBAKE_MARKERS):
                repaired += 1
        repair_frac = repaired / min(len(affected), 50)
        out.append(
            _finding(
                8,
                [_name(df, i)],
                f"{count} values contain mojibake markers; a latin-1 -> utf-8 round trip "
                f"repairs {repair_frac:.0%} of the sample: {_samples(affected)}",
                {"count": count, "repair_frac": repair_frac, "values": len(values)},
                "GATE",
                0.95 if repair_frac >= 0.9 else 0.6,
            )
        )
    return out


# --- 9/10. duplicate and near-duplicate rows --------------------------------


def _normalised_frame(df):
    data = {}
    for i in range(df.shape[1]):
        folded = _folded(df, i)
        data[i] = df.iloc[:, i] if folded is None else folded
    return pd.DataFrame(data)


@register(9)
def _d09(df, cols) -> list:
    dupes = df.duplicated()
    count = int(dupes.sum())
    if count == 0:
        return []
    return [
        _finding(
            9,
            [],
            f"{count} of {len(df)} rows are byte-identical repeats of an earlier row",
            {"dup_rows": count, "dup_count": count, "rows": len(df)},
            "AUTO",
            1.0,
        )
    ]


@register(10)
def _d10(df, cols) -> list:
    exact = int(df.duplicated().sum())
    near = int(_normalised_frame(df).duplicated().sum()) - exact
    if near <= 0:
        return []
    return [
        _finding(
            10,
            [],
            f"{near} rows duplicate another row once case, whitespace, and unicode "
            "are normalised — the same entity recorded twice",
            {"near_dup_rows": near, "pair_count": near, "exact_dup_rows": exact},
            "HUMAN",  # survivorship (which copy wins) is a judgement call
            0.8,
        )
    ]


# --- 11. key violations -----------------------------------------------------


@register(11)
def _d11(df, cols) -> list:
    out = []
    rows = len(df)
    if rows < 20:
        return out
    for i in _targets(df, cols):
        values = df.iloc[:, i]
        distinct = values.nunique(dropna=True)
        if not (0.995 <= distinct / rows < 1.0) or distinct < 20:
            continue
        others = [j for j in range(df.shape[1]) if j != i]
        if not others:
            continue
        grouped = df.iloc[:, others].groupby(values.values, observed=True)
        spread = grouped.nunique()
        contradicted = spread.max(axis=1) > 1
        if not bool(contradicted.any()):
            continue
        columns = [str(c) for c in spread.columns[(spread > 1).any()]]
        out.append(
            _finding(
                11,
                [_name(df, i)] + columns,
                f"{_name(df, i)} is {distinct / rows:.1%} unique (a de-facto key) but "
                f"{int(contradicted.sum())} of its values carry contradicting attributes "
                f"in {', '.join(columns[:3])}",
                {
                    "key": _name(df, i),
                    "uniqueness": float(distinct / rows),
                    "contradicted_keys": int(contradicted.sum()),
                    "attributes": columns[:8],
                },
                "HUMAN",  # which record wins needs recency or source rules
                0.85,
            )
        )
    return out


# --- 12. cross-field contradictions (indicator) -----------------------------


def _fd_candidates(df, cols, rows):
    """Columns coarse enough to be an entity key: enough rows per group that a
    dissenting minority means something."""
    sizes = []
    for i in _targets(df, cols):
        distinct = df.iloc[:, i].nunique(dropna=True)
        if 2 <= distinct <= max(50, rows / 5):
            sizes.append((distinct, i))
    sizes.sort()
    return [i for _, i in sizes[:12]]


@register(12)
def _d12(df, cols) -> list:
    rows = len(df)
    if rows < 40 or df.shape[1] < 2:
        return []
    determinants = _fd_candidates(df, cols, rows)
    dependents = list(range(df.shape[1]))[:20]
    best: dict = {}
    budget = MAX_FD_PAIRS if rows <= 20_000 else 16
    cache: dict = {}

    def stripped(k):  # every column is read by many pairs; coerce it once
        if k not in cache:
            cache[k] = df.iloc[:, k].astype(str).str.strip()
        return cache[k]

    for i in determinants:
        left = stripped(i)
        blank_or_missing = (left == "") | left.str.lower().isin(MISSING_TOKENS)
        for j in dependents:
            if i == j or budget <= 0:
                continue
            budget -= 1
            right = stripped(j)
            live = ~blank_or_missing & (right != "")
            if int(live.sum()) < 40:
                continue
            pairs = (
                pd.DataFrame({"x": left[live], "y": right[live]})
                .groupby(["x", "y"])
                .size()
            )
            modal = pairs.groupby(level=0).max()
            group_sizes = pairs.groupby(level=0).sum()
            conf = float(modal.sum() / live.sum())
            if conf >= 1.0:
                continue
            dissent = (
                (group_sizes >= 4)
                & (modal / group_sizes >= 0.6)
                & (modal < group_sizes)
            )
            violating = int(dissent.sum())
            # two ways in: a near-perfect FD with any dissent (the classic approximate
            # FD), or a merely strong one where dissent is systemic (typo-scale dirt).
            if not (
                (conf >= 0.98 and violating >= 1) or (conf >= 0.85 and violating >= 5)
            ):
                continue
            name_j = _name(df, j)
            if name_j not in best or conf > best[name_j][0]:
                best[name_j] = (conf, _name(df, i), violating, len(group_sizes))
    out = []
    for name_j, (conf, name_i, violating, groups) in sorted(
        best.items(), key=lambda kv: -kv[1][0]
    )[:5]:
        out.append(
            _finding(
                12,
                [name_i, name_j],
                f"{name_i} determines {name_j} for {conf:.1%} of rows, but {violating} of "
                f"{groups} groups contain a dissenting minority — the records contradict "
                "each other, so one side is wrong",
                {
                    "determinant": name_i,
                    "dependent": name_j,
                    "confidence": conf,
                    "violating_groups": violating,
                    "groups": groups,
                },
                "HUMAN",
                float(conf),
            )
        )
    return out


# --- 13. out-of-domain values -----------------------------------------------


@register(13)
def _d13(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        numbers = _numbers(df, i)
        if numbers is None or len(numbers) < 5:
            continue
        key = _key(df, i)
        bounds = next(
            ((lo, hi) for pattern, lo, hi in DOMAIN_BOUNDS if pattern.search(key)), None
        )
        if bounds is None:
            continue
        low, high = bounds
        bad = numbers[(numbers < low) | (numbers > high)]
        if bad.empty:
            continue
        out.append(
            _finding(
                13,
                [_name(df, i)],
                f"{len(bad)} values fall outside the plausible range "
                f"[{low:g}, {high:g}] for this column; samples: {_samples(bad)}",
                {
                    "violations": len(bad),
                    "low": low,
                    "high": None if math.isinf(high) else high,
                    "min": float(numbers.min()),
                    "max": float(numbers.max()),
                },
                "GATE",
                0.9,
            )
        )
    return out


# --- 14. broken coordinates -------------------------------------------------


@register(14)
def _d14(df, cols) -> list:
    targets = _targets(df, cols)
    lats = [
        i
        for i in targets
        if LAT_NAME.search(_key(df, i)) and _numbers(df, i) is not None
    ]
    lons = [
        i
        for i in targets
        if LON_NAME.search(_key(df, i)) and _numbers(df, i) is not None
    ]
    if not lats or not lons:
        return []
    i, j = lats[0], lons[0]
    lat, lon = df.iloc[:, i], df.iloc[:, j]
    zero_zero = int(((lat == 0) & (lon == 0)).sum())
    out_of_range = int((~lat.between(-90, 90) | ~lon.between(-180, 180)).sum())
    if zero_zero == 0 and out_of_range == 0:
        return []
    in_bc = float(
        (
            lat.between(BC_LAT_MIN, BC_LAT_MAX) & lon.between(BC_LON_MIN, BC_LON_MAX)
        ).mean()
    )
    return [
        _finding(
            14,
            [_name(df, i), _name(df, j)],
            f"{zero_zero} rows sit at null island (0, 0) and {out_of_range} fall outside "
            f"valid lat/lon ranges; {in_bc:.0%} of rows are inside the BC bounding box",
            {
                "zero_zero": zero_zero,
                "out_of_range": out_of_range,
                "in_bc_frac": in_bc,
                "rows": len(df),
            },
            "GATE",
            0.9,
        )
    ]


# --- 15. statistical outliers (indicator) -----------------------------------


@register(15)
def _d15(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        numbers = _numbers(df, i)
        if numbers is None or len(numbers) < 20 or numbers.nunique() < 5:
            continue
        median = float(numbers.median())
        mad = float((numbers - median).abs().median())
        if mad <= 0:
            continue
        scores = 0.6745 * (numbers - median).abs() / mad
        extreme = numbers[scores > 3.5]
        if extreme.empty:
            continue
        out.append(
            _finding(
                15,
                [_name(df, i)],
                f"{len(extreme)} values are beyond a modified z-score of 3.5 "
                f"(median {median:g}, MAD {mad:g}); samples: {_samples(extreme)} — "
                "flagged only: extremes are often the real signal",
                {
                    "count": len(extreme),
                    "median": median,
                    "mad": mad,
                    "max_score": float(scores.max()),
                },
                "HUMAN",
                0.7,
            )
        )
    return out


# --- 16. unit heterogeneity -------------------------------------------------


def _bimodal_ratio(numbers):
    positive = _probe(numbers[numbers > 0], 20_000)
    if len(positive) < 20:
        return None
    logs = positive.astype(float).map(math.log10).sort_values().to_numpy()
    gaps = logs[1:] - logs[:-1]
    if not len(gaps):
        return None
    cut = int(gaps.argmax())
    if not (0.25 <= (cut + 1) / len(logs) <= 0.75):
        return None
    widest = float(gaps[cut])
    remaining = (float(logs[-1] - logs[0]) - widest) / max(len(gaps) - 1, 1)
    if widest < 5 * remaining or widest <= 0:
        return None
    low = float(pd.Series(logs[: cut + 1]).median())
    high = float(pd.Series(logs[cut + 1 :]).median())
    return 10 ** (high - low)


@register(16)
def _d16(df, cols) -> list:
    out = []
    targets = _targets(df, cols)
    units = [i for i in targets if UNIT_NAME.search(_key(df, i))]
    params = [i for i in range(df.shape[1]) if PARAM_NAME.search(_key(df, i))]
    if units and params:
        i, j = params[0], units[0]
        spread = df.iloc[:, j].groupby(df.iloc[:, i].values, observed=True).nunique()
        mixed = spread[spread > 1]
        seen = _text(df, j)
        if len(mixed):
            out.append(
                _finding(
                    16,
                    sorted({_name(df, i), _name(df, j)}),
                    f"{len(mixed)} parameters are reported in more than one unit "
                    f"(e.g. {_samples(mixed.index)}) — values are not comparable as stored",
                    {
                        "mixed_parameters": len(mixed),
                        "units": sorted(set(seen))[:8] if seen is not None else [],
                        "kind": "explicit",
                    },
                    "AUTO",
                    0.9,
                )
            )
    for i in targets:
        numbers = _numbers(df, i)
        if numbers is None:
            continue
        ratio = _bimodal_ratio(numbers)
        if ratio is None or ratio < 1.05:
            continue
        match = next(
            ((r, label) for r, label in KNOWN_RATIOS if abs(ratio - r) / r <= 0.03),
            None,
        )
        if match is None:
            continue
        out.append(
            _finding(
                16,
                [_name(df, i)],
                f"values cluster into two bands {ratio:.4g}x apart — the {match[1]} "
                "conversion factor, so this column mixes units",
                {
                    "ratio": ratio,
                    "known_ratio": match[0],
                    "conversion": match[1],
                    "kind": "inferred",
                },
                "HUMAN",  # which band is which needs domain knowledge
                0.75,
            )
        )
    return out


# --- 17. packed fields ------------------------------------------------------


def _pack_kind(values):
    probe = _probe(values)
    if float(probe.str.match(LATLON_PAIR).mean()) >= 0.8:
        return "latlon_pair", 0.95
    if float(probe.str.match(JSON_ISH).mean()) >= 0.8:
        return "json", 0.9
    for delimiter, label in ((",", "comma"), ("|", "pipe"), (";", "semicolon")):
        # count separators rather than splitting: str.split allocates a list per
        # cell, which is seconds of work on a large frame
        separators = probe.str.count(re.escape(delimiter))
        if float((separators >= 1).mean()) >= 0.8 and float(separators.mean()) >= 1.0:
            return f"{label}_list", 0.85
    return None, 0.0


def _sibling_groups(df, targets):
    groups: dict = {}
    for i in targets:
        match = SIBLING_COLUMN.match(_key(df, i))
        if match and _is_texty(df.iloc[:, i]):
            groups.setdefault(match.group("prefix"), []).append(
                (int(match.group("num")), i)
            )
    return {p: [i for _, i in sorted(m)] for p, m in groups.items() if len(m) >= 2}


@register(17)
def _d17(df, cols) -> list:
    out = []
    targets = _targets(df, cols)
    for i in targets:
        values = _present(df, i)
        if values is None or len(values) < 10:
            continue
        kind, confidence = _pack_kind(values)
        if kind is None:
            continue
        out.append(
            _finding(
                17,
                [_name(df, i)],
                f"{len(values)} values pack several fields into one cell ({kind}); "
                f"samples: {_samples(values)}",
                {"kind": kind, "values": len(values)},
                "AUTO",
                confidence,
            )
        )
    for prefix, members in _sibling_groups(df, targets).items():
        for left_i, right_i in pairwise(members):
            left, right = _present(df, left_i), _present(df, right_i)
            if left is None or right is None:
                continue
            both = _probe(left.index.intersection(right.index).to_series())
            if len(both) < 5:
                continue
            head, tail = left.loc[both], right.loc[both]
            joins = head.str[-1].str.isalnum().fillna(False) & tail.str[
                0
            ].str.isalnum().fillna(False)
            if float(joins.mean()) < 0.8:
                continue
            last = head.str.split().str[-1]
            first = tail.str.split().str[0]
            if float(last.str.len().combine(first.str.len(), min).le(3).mean()) < 0.6:
                continue
            # the decisive test: gluing the fragments back together yields words
            # the family already uses. Two ordinary neighbouring columns fail it.
            vocabulary = set(
                _probe(head, 2000).str.split().explode().str.lower()
            ) | set(_probe(tail, 2000).str.split().explode().str.lower())
            healed = (last + first).str.lower().isin(vocabulary)
            if float(healed.mean()) < 0.4:
                continue
            out.append(
                _finding(
                    17,
                    [_name(df, left_i), _name(df, right_i)],
                    f"{int(healed.sum())} rows split mid-word across {prefix}* columns — "
                    f"the text heals only when concatenated; samples: "
                    f"{_samples(head + '|' + tail)}",
                    {
                        "kind": "split_columns",
                        "rows": int(healed.sum()),
                        "healed_frac": float(healed.mean()),
                        "members": [_name(df, i) for i in members],
                    },
                    "AUTO",
                    0.85,
                )
            )
    return out


# --- 18. header damage ------------------------------------------------------


@register(18)
def _d18(df, cols) -> list:
    names = [str(c) for c in df.columns]
    bom = [n for n in names if any(z in n for z in ZERO_WIDTH)]
    duplicated = int(pd.Index(names).duplicated().sum())
    unnamed = [n for n in names if n.startswith("Unnamed:") or n.strip() == ""]
    header_rows = 0
    scan = df.head(200)
    for pos in range(len(scan)):
        if [str(v) for v in scan.iloc[pos].tolist()] == names:
            header_rows += 1
    if not bom and not duplicated and not unnamed and not header_rows:
        return []
    problems = []
    if bom:
        problems.append(f"{len(bom)} column names carry a BOM/zero-width character")
    if duplicated:
        problems.append(f"{duplicated} duplicate column names")
    if unnamed:
        problems.append(f"{len(unnamed)} unnamed columns")
    if header_rows:
        problems.append(f"{header_rows} data rows repeat the header")
    affected = bom + unnamed + [n for n in names if names.count(n) > 1]
    return [
        _finding(
            18,
            sorted(set(affected)),
            "; ".join(problems),
            {
                "bom_columns": len(bom),
                "duplicate_columns": duplicated,
                "unnamed_columns": len(unnamed),
                "header_rows": header_rows,
            },
            "AUTO",
            0.98,
        )
    ]


# --- 19. empty / constant columns -------------------------------------------


@register(19)
def _d19(df, cols) -> list:
    out = []
    if len(df) < 5:
        return out
    for i in _targets(df, cols):
        series = df.iloc[:, i]
        null_frac = float(series.isna().mean())
        try:
            distinct = int(series.nunique(dropna=True))
        except TypeError:
            continue
        if null_frac >= 0.995:
            reason, stats = (
                "holds no values at all",
                {"null_frac": null_frac, "distinct": 0},
            )
        elif distinct <= 1:
            only = _samples(series.dropna().head(1)) or "''"
            reason = f"holds one value ({only}) in every row"
            stats = {"null_frac": null_frac, "distinct": distinct}
        else:
            continue
        out.append(
            _finding(
                19,
                [_name(df, i)],
                f"column {reason} — it carries no information",
                stats,
                "AUTO",
                1.0,
            )
        )
    return out


# --- 21. aggregate rows mixed with detail -----------------------------------


@register(21)
def _d21(df, cols) -> list:
    out = []
    rows = len(df)
    if rows < 10:
        return out
    for i in _targets(df, cols):
        values = _text(df, i)
        if values is None or len(values) < 10:
            continue
        if values.nunique() > max(50, 0.5 * rows):
            continue  # a high-cardinality label column, not a grouping dimension
        hit = values.str.lower().str.match(ROLLUP_LABEL)
        count = int(hit.sum())
        if count < 2 or count > 0.2 * rows:
            continue
        labels = sorted(set(values[hit]))
        out.append(
            _finding(
                21,
                [_name(df, i)],
                f"{count} rows are rollups ({', '.join(repr(v) for v in labels[:3])}) "
                "mixed in with detail rows — any sum over this column double counts",
                {"rows": count, "rollup_count": count, "labels": labels[:8]},
                "GATE",
                0.9,
            )
        )
    return out


# --- 22. ID corruption from numeric casting ---------------------------------


@register(22)
def _d22(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        if not ID_NAME.search(_key(df, i)):
            continue
        numbers = _numbers(df, i)
        if numbers is None or len(numbers) < 10:
            continue
        integral = numbers[numbers == numbers.round()]
        if len(integral) < 0.99 * len(numbers) or (integral < 0).any():
            continue
        distinct = int(integral.nunique())
        span = float(integral.max() - integral.min()) + 1
        # A row counter fills its numeric range (density ~1.0); a real code is
        # sparse in it (US zips: 1e-4). Cut in the wide empty middle.
        if distinct >= 0.5 * span:
            continue
        widths = integral.astype("int64").astype(str).str.len()
        modal_width = int(widths.mode().iloc[0])
        if modal_width < 4 or float((widths == modal_width).mean()) < 0.5:
            continue
        shorter = int((widths < modal_width).sum())
        if shorter == 0:
            continue
        out.append(
            _finding(
                22,
                [_name(df, i)],
                f"{shorter} identifiers are shorter than the modal width of "
                f"{modal_width} digits — leading zeros were eaten by a numeric cast; "
                f"samples: {_samples(integral[widths < modal_width].astype('int64'))}",
                {
                    "modal_width": modal_width,
                    "shorter": shorter,
                    "values": len(integral),
                    "modal_frac": float((widths == modal_width).mean()),
                },
                "GATE",
                0.85,
            )
        )
    return out


# --- public API -------------------------------------------------------------


def detect_all(df, name: str = "df") -> dict:
    """Run every single-frame signal. Returns findings plus the ids that ran
    and found nothing — absence is a checked claim, not a silence (R1) — plus
    the ids that could not run at all. A crashed detector must never look
    like a clean one: silently dropping it from both findings and clear would
    make a systematically broken signal invisible forever."""
    if df is None or len(df) == 0 or df.shape[1] == 0:
        return {"findings": [], "clear": list(SINGLE_FRAME), "broken": {}}
    findings, broke, broken = [], set(), {}
    try:
        for disease in SINGLE_FRAME:
            try:
                findings.extend(REGISTRY[disease](df, None))
            except Exception as exc:  # noqa: BLE001 - one brittle signal must never sink the run
                broke.add(disease)
                broken[str(disease)] = f"{type(exc).__name__}: {exc}"
    finally:
        _VIEWS.clear()
    findings.sort(key=lambda f: (f["disease"], f["columns"]))
    found = {f["disease"] for f in findings}
    return {
        "findings": findings,
        "clear": [d for d in SINGLE_FRAME if d not in found and d not in broke],
        "broken": broken,
    }


def detect_one(df, disease: int, columns) -> dict | None:
    """Re-run one signal scoped to one target. This is verification layer 1:
    the fix worked when the signal that found the disease stops firing (R4)."""
    if disease not in REGISTRY:
        raise ValueError(
            f"disease {disease} is not a single-frame signal "
            f"(expected one of {list(REGISTRY)})"
        )
    if df is None or len(df) == 0 or df.shape[1] == 0:
        return None
    wanted = [str(c) for c in (columns or [])]
    scoped = _indices(df, columns) or None
    # No except here, deliberately. This is verification layer 1 — verify.py
    # asserts `detect_one(...) is None` to mean the fix worked, so swallowing a
    # crash would turn every fix for this disease into a silent pass. A raise
    # fails the verify cell, which reverts the fix, which is the honest answer:
    # we could not check, therefore it is not verified. detect_all keeps its
    # per-detector catch because there a crash hides a disease rather than
    # manufacturing proof, and it reports what broke.
    try:
        findings = REGISTRY[disease](df, scoped)
    finally:
        _VIEWS.clear()  # the frame is mutating between fixes; never reuse a view
    for finding in findings:
        if finding["columns"] == wanted:
            return finding
    return None


def detect_family(dfs: dict) -> list:
    """Disease 20: schema drift across same-family files. Not reachable from
    detect_all — /clean holds one variable; the harmonizer is P2 (R3)."""
    names = list(dfs)
    if len(names) < 2:
        return []
    findings = []
    base = names[0]
    for other in names[1:]:
        left, right = dfs[base], dfs[other]
        cols_a = [str(c) for c in left.columns]
        cols_b = [str(c) for c in right.columns]
        only_a = [c for c in cols_a if c not in cols_b]
        only_b = [c for c in cols_b if c not in cols_a]
        shared = [c for c in cols_a if c in cols_b]
        dtype_changes = {
            c: [str(left[c].dtype), str(right[c].dtype)]
            for c in shared
            if str(left[c].dtype) != str(right[c].dtype)
        }
        reordered = [c for c in shared if cols_a.index(c) != cols_b.index(c)]
        if not (only_a or only_b or dtype_changes):
            continue
        problems = []
        if only_a:
            problems.append(
                f"{len(only_a)} columns only in {base} ({', '.join(only_a[:3])})"
            )
        if only_b:
            problems.append(
                f"{len(only_b)} columns only in {other} ({', '.join(only_b[:3])})"
            )
        if dtype_changes:
            problems.append(f"{len(dtype_changes)} columns change dtype")
        findings.append(
            _finding(
                20,
                sorted(set(only_a + only_b + list(dtype_changes))),
                f"{base} vs {other}: " + "; ".join(problems),
                {
                    "files": [base, other],
                    "only_in_a": only_a,
                    "only_in_b": only_b,
                    "dtype_changes": dtype_changes,
                    "reordered": reordered,
                },
                "GATE",
                0.95,
            )
        )
    return findings
