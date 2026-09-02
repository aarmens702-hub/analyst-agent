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
    23: "boolean-chaos",
    24: "stray-structural-rows",
    25: "duplicated-columns",
    26: "truncated-values",
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
SUPPRESSION_TOKEN = re.compile(
    # "<5" bounds, 1-3 letter agency codes, or the literal disclosure words —
    # statistical publishers really do print "SUPPRESSED" in numeric columns
    r"^[<>]=?\s?\d+$|^[a-zA-Z*.]{1,3}$"
    r"|(?i:^(suppressed|redacted|withheld|masked|confidential)$)"
)
MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "Ð", "Ñ", "�")
ZERO_WIDTH = ("\u200b", "‌", "‍", "﻿")
# _finding's evidence must never carry a literal newline (single-line display
# downstream). Collapsing every whitespace run to guarantee that also erases
# meaningful evidence — disease 6 reports a double space by showing one — so
# only the characters that actually break single-line text are touched;
# ordinary spaces inside the message survive untouched.
# Every character str.splitlines() breaks on, which is what the old
# " ".join(evidence.split()) implicitly covered. Narrowing this to \r\n\t was
# weaker than the code it replaced: form feed, NEL and the Unicode line and
# paragraph separators all survived, and column names reach the evidence
# uninterpolated (_d11, _d12, _d20), so a header carrying one of them split a
# report bullet in half.
EVIDENCE_LINEBREAKS = re.compile("[\r\n\t\v\f\x1c\x1d\x1e\x85\u2028\u2029]+")
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
    (
        "iso",
        re.compile(r"^\d{4}-\d{1,2}-\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?)?$"),
    ),
    # RFC 3339 zone suffixes, either separator. Zoned is a separate family
    # from naive iso because the two cannot land in one datetime64 without a
    # decision — the mix is d03's subject, not a d02 column with a long tail.
    (
        "iso-zoned",
        re.compile(
            r"^\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?"
            r"\s?([Zz]|[+-]\d{2}:?\d{2})$"
        ),
    ),
    ("slash", re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")),
    ("dash", re.compile(r"^\d{1,2}-\d{1,2}-\d{2,4}$")),
    ("dot", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$")),
    ("day-month-name", re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}$")),
    ("month-name-day", re.compile(r"^[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}$")),
    ("compact", re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")),
    ("time", re.compile(r"^\d{1,2}:\d{2}(:\d{2})?\s*([aApP]\.?\s?[mM]\.?)?$")),
    # unix seconds bounded to 2001-09..2036-08: outside that band ten digits
    # are far more likely an id than an instant. _date_families additionally
    # refuses to count this family alone — see the guard there.
    ("epoch", re.compile(r"^(?:1\d{9}|20\d{8})$")),
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
# Unicode's Zs category as a literal: the comprehension this replaces scanned
# all 1,114,112 codepoints at import — ~60ms, ~92% of this module's import
# self-time, paid again on every kernel start and crash replay. The suite
# recomputes the category from unicodedata and fails if a Unicode update ever
# moves it.
_UNICODE_SPACES = frozenset(
    [0x0020, 0x00A0, 0x1680] + list(range(0x2000, 0x200B)) + [0x202F, 0x205F, 0x3000]
)
# Invisible and meaningless: delete rather than space, so repairing
# 'Bud<ZWSP>weiser' yields 'Budweiser' and not a split word.
_ZERO_WIDTH_NOISE = {0x200B, 0xFEFF}
# ZWJ and ZWNJ are deliberately absent from both sets. They carry meaning —
# they build emoji sequences and Indic conjuncts — so touching them would both
# report 'woman technologist' as whitespace damage and split it while
# "repairing" it.
_WS_DAMAGE = {c: " " for c in _UNICODE_SPACES} | {c: None for c in _ZERO_WIDTH_NOISE}
# The invisible subset, for the evidence's "N with NBSP/zero-width" clause.
# _WS_DAMAGE itself contains U+0020 — normalising a space to a space is a
# harmless no-op — but counting with it made every value holding an ordinary
# space read as exotic, and the evidence said so out loud.
_WS_EXOTIC = {c for c in _WS_DAMAGE if c != 0x20}
# one vectorised pass instead of a per-character Python loop: the loop was
# measured 23-89x slower and re-ran inside every d06 verify cell
_WS_EXOTIC_RE = re.compile("[" + "".join(map(chr, sorted(_WS_EXOTIC))) + "]")
# every signature _ws_tidy would repair, in one compiled pass: leading or
# trailing whitespace, an internal run of two-plus, any whitespace that is not
# a plain space (NBSP and friends are \s), and the zero-widths (which are not).
# This is the full-column confirmation behind a probe-negative — ~21ms per
# 300k rows — so a sampled silence never becomes a CLEAR claim.
_WS_ANY_DAMAGE_RE = re.compile(
    r"^\s|\s$|\s{2,}|[^\S ]|["
    + "".join(re.escape(chr(c)) for c in sorted(_ZERO_WIDTH_NOISE))
    + "]"
)


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

# A1: the old gate demanded that ONE pattern claim >=90% of a column, so the
# signal got quieter as the damage got worse — five money formats at ~20%
# each meant silence, and the mixed column landed in "checked and clean".
# The gate now asks two questions the old threshold conflated: is this column
# number-shaped at all (the UNION of every family), and does it agree with
# itself (the per-family breakdown). Heterogeneity raises the finding.
KIND_FLOOR = 0.30  # below: not that kind of column, stay silent
MIXED_FLOOR = 0.50  # >=2 families claiming this much: name the conflict
FULL_COVERAGE = 0.90  # the pre-A1 research threshold, kept for one-shape columns

# Ordered specific-to-broad; _number_families gives each value to the FIRST
# match, so the shares partition the column and sum to the union.
NUMBER_FAMILIES = (
    (
        "symbol",
        re.compile(r"^\s*[-+]?\s*[$€£¥]\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*$"),
    ),
    ("thousands-comma", re.compile(r"^\s*[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*$")),
    ("decimal-comma", re.compile(r"^\s*[-+]?\d+,\d{1,2}\s*$")),
    ("code-prefixed", re.compile(r"^\s*[A-Z]{3}\s?[-+]?\d+(?:[.,]\d+)?\s*$")),
    (
        "accounting",
        re.compile(r"^\s*\((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)\s*$"),
    ),
    ("bare", re.compile(r"^\s*[-+]?\d+(?:\.\d+)?\s*$")),
    ("unit-suffix", NUMERIC_WITH_UNIT),  # 12.0 oz, 15 kg, 98% — the broad legacy shape
)


def _number_families(values) -> dict:
    """Per-family shares where the first matching family claims the value."""
    remaining = pd.Series(True, index=values.index)
    shares = {}
    for name, pattern in NUMBER_FAMILIES:
        hit = remaining & values.str.match(pattern)
        share = float(hit.mean())
        if share > 0:
            shares[name] = share
            remaining &= ~hit
    return shares


@register(1)
def _d01(df, cols) -> list:
    out = []
    for i in _targets(df, cols):
        # _present, not _text: missing-data sentinels are d04's cells to name.
        # Counting them here as "match nothing known" reports the same values
        # twice and pushes sentinel-heavy numeric columns into the HUMAN zone.
        values = _present(df, i)
        if values is None or len(values) < 8:
            continue
        if sum(_number_families(_probe(values)).values()) < KIND_FLOOR:
            continue
        direct = pd.to_numeric(values, errors="coerce").notna().mean()
        if direct > 0.95:  # already parses as-is: stored as text, no residue
            continue
        # a uniform code scheme — one constant alpha prefix and fixed-width
        # digits on ~every value ("SIT000123", "TX000042") — is an
        # identifier, not an amount wearing a unit; without this, id columns
        # read as currency residue (bench 2026-09-02). Money keeps varying
        # digit widths and separators, so it never matches this shape.
        scheme = values.str.extract(r"^([A-Za-z]{1,6})(\d+)$", expand=True)
        if (
            scheme[0].notna().mean() >= 0.95
            and scheme[0].nunique(dropna=True) == 1
            and scheme[1].str.len().nunique(dropna=True) == 1
        ):
            continue
        shares = _number_families(values)
        union = sum(shares.values())
        families = {name: s for name, s in shares.items() if s >= 0.05}
        if union < KIND_FLOOR or not families:
            continue
        # the one genuinely ambiguous mix: '2,447' is 2447 under a thousands
        # comma and 2.447 under a decimal comma — same digits, two readings,
        # so the fix needs a human eye on the convention split
        ambiguous = "decimal-comma" in families and (
            "thousands-comma" in families or "symbol" in families
        )
        pulled = values.str.extract(LEADING_NUMBER)
        numeric = pd.to_numeric(
            (pulled[0].fillna("") + pulled[1].fillna("")).str.replace(
                ",", "", regex=False
            ),
            errors="coerce",
        )
        extract_frac = float(numeric.notna().mean())
        stats = {
            "values": len(values),
            "families": families,
            "union": round(union, 3),
            "parse_frac": extract_frac,
            "residue_frac": 1.0 - float(direct),
        }
        ranked = sorted(families.items(), key=lambda kv: -kv[1])
        mixed = len(families) >= 2
        if union >= FULL_COVERAGE or (mixed and union >= MIXED_FLOOR):
            if mixed:
                evidence = (
                    f"{len(families)} number formats in one column ("
                    + ", ".join(f"{name} {s:.0%}" for name, s in ranked)
                    + ")"
                    + (
                        f"; {1 - union:.0%} match no known shape"
                        if union < 0.98
                        else ""
                    )
                    + (
                        "; decimal comma and thousands comma both present — "
                        "the same digits read two ways"
                        if ambiguous
                        else ""
                    )
                )
                grade = "GATE" if ambiguous else "AUTO"
                confidence = union * (0.85 if ambiguous else 0.95)
            else:
                dirty = values[values.str.strip() != numeric.astype(str).str.strip()]
                evidence = (
                    f"{len(values) - int(direct * len(values))}/{len(values)} values "
                    f"carry currency/unit residue; samples: {_samples(dirty)}"
                )
                grade = "AUTO"
                confidence = min(union, extract_frac)
        else:
            # the zone the old gate spelled as silence: enough of the column
            # is number-shaped that "clean" would be a lie, not enough that
            # any automatic fix is defensible
            evidence = (
                f"{union:.0%} of values look like amounts across "
                f"{len(families)} shape(s); {1 - union:.0%} match nothing known"
            )
            grade = "HUMAN"
            confidence = round(union, 3)
        out.append(_finding(1, [_name(df, i)], evidence, stats, grade, confidence))
    return out


# --- 2/3. dates as strings, single or mixed format --------------------------


def _date_families(values) -> dict:
    hits = {}
    for family, pattern in DATE_FAMILIES:
        share = float(values.str.match(pattern).mean())
        if share > 0:
            hits[family] = share
    # ten digits are only a timestamp in company: order ids and account
    # numbers live in the same band as unix seconds, so a lone epoch match is
    # an integer column, and "dates as strings" would be a false claim on it.
    # With any real date family (>= 5%) beside it, epoch counts in full.
    if "epoch" in hits and not any(
        share >= 0.05 for family, share in hits.items() if family != "epoch"
    ):
        del hits["epoch"]
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
    """A1: the scan feeds both d02 (one format) and d03 (a mix), so it gates
    only on the column being date-shaped at all — each detector applies its
    own agreement threshold. Gating here on single-family coverage switched
    d03 off in exactly the columns it names. Dirty date columns still carry a
    tail no format claims (Raha flights: 94%), which is why the detectors'
    thresholds sit below 1.0."""
    for i in _targets(df, cols):
        values = _present(df, i)
        if values is None or len(values) < 8:
            continue
        if sum(_date_families(_probe(values)).values()) < KIND_FLOOR:
            continue
        families = {f: s for f, s in _date_families(values).items() if s >= 0.05}
        covered = sum(families.values())
        if not families or covered < KIND_FLOOR:
            continue
        yield i, values, families, covered


@register(2)
def _d02(df, cols) -> list:
    out = []
    for i, values, families, covered in _date_scan(df, cols):
        if len(families) != 1:
            continue
        family = next(iter(families))
        if covered < FULL_COVERAGE:
            # A1's third outcome: too date-shaped for "clean", too mixed with
            # the unclaimable for an automatic parse — a person decides
            out.append(
                _finding(
                    2,
                    [_name(df, i)],
                    (
                        f"{covered:.0%} of values are {family}-format dates; "
                        f"{1 - covered:.0%} match nothing known"
                    ),
                    {"values": len(values), "family": family, "coverage": covered},
                    "HUMAN",
                    covered,
                )
            )
            continue
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
        if len(families) < 2 or covered < MIXED_FLOOR:
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
                + (
                    f"; {1 - covered:.0%} match no known family"
                    if covered < 0.98
                    else ""
                )
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
        # astype(str) first: the realistic degrade keeps unsuppressed cells
        # as floats in an object column, and `.str` ops on a mixed Series
        # silently NaN every non-string — which read as "nothing parses"
        # and kept d5 quiet on exactly the shape it exists for
        as_text = values.astype(str)
        numeric = pd.to_numeric(
            as_text.str.replace(",", "", regex=False), errors="coerce"
        )
        parse_frac = float(numeric.notna().mean())
        if not 0.90 <= parse_frac < 1.0:
            continue
        residual = as_text[numeric.isna()].str.strip()
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
        if not bool((probe != _ws_tidy(probe)).any()) and not bool(
            values.str.contains(_WS_ANY_DAMAGE_RE).any()
        ):
            continue
        cleaned = _ws_tidy(values)
        dirty = values[values != cleaned]
        count = len(dirty)
        if count == 0:
            continue
        # counted from the same table the repair uses, so the number in the
        # evidence cannot drift from what the fix would actually touch
        exotic = int(values.str.contains(_WS_EXOTIC_RE).sum())
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
    normal = _normalised_frame(df)
    near = int(normal.duplicated().sum()) - exact
    # Leave-one-column-out drift pass: rows identical everywhere but ONE
    # numeric column whose values sit within a hair of each other are the
    # same entity recorded twice with drift (amount +0.01). Text folding
    # above already covers the text-deviation case; and a differing ID is a
    # genuinely different record — ids are never "close" — which is exactly
    # what the closeness requirement encodes (bench 2026-09-02).
    drift_rows: set = set()
    fold_dupes = normal.duplicated(keep=False)
    if df.shape[1] >= 2 and 0 < len(df) <= 100_000:
        for j in range(df.shape[1]):
            col = pd.to_numeric(df.iloc[:, j], errors="coerce")
            if col.notna().mean() < 0.9:
                continue  # closeness is a numeric notion; text deviation is folding's job
            others = normal.drop(columns=[normal.columns[j]])
            cand = others.duplicated(keep=False) & ~fold_dupes
            if not cand.any():
                continue
            keyed: dict = {}
            for idx in others.index[cand]:
                keyed.setdefault(tuple(others.loc[idx]), []).append(idx)
            for idxs in keyed.values():
                if len(idxs) < 2:
                    continue
                vals = col.loc[idxs].dropna()
                if len(vals) < 2:
                    continue
                spread = float(vals.max() - vals.min())
                if spread <= 1e-3 * max(1.0, abs(float(vals.max()))):
                    drift_rows.update(idxs[1:])  # keep-first, like duplicated()
    near += len(drift_rows)
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
        # a float column is never a key: near-unique uniform measurements
        # that collide once are coincidence, not damaged identifiers
        if pd.api.types.is_float_dtype(values):
            continue
        distinct = values.nunique(dropna=True)
        uniqueness = distinct / rows
        # Two ways in: the near-perfect window (any dtype), or — because a
        # damaged key gets LESS unique the more of it is overwritten, so the
        # old 0.995 gate bought more silence with more damage (the A1 shape;
        # bench 2026-09-02) — a damage-tolerant floor for columns that look
        # like keys by name or by being text. Merely-numeric columns
        # (readings, coordinates) never take the wide path: coincidental
        # duplicates there are measurements, not keys.
        # the wide path is only for columns that CLAIM to be keys by name:
        # bare textiness is far too weak a prior — a sentinel-riddled text
        # column (12 x "N/A" beside unique neighbours) reads as a damaged
        # key under it. Unnamed true keys keep the near-perfect window.
        looks_like_key = bool(ID_NAME.search(_key(df, i)))
        # the wide path also demands real damage: >=3 duplicated rows AND
        # uniqueness <= 0.98, because a short random attribute (4-digit
        # account numbers at a few hundred rows) birthday-collides its way
        # to ~0.99 and would impersonate a key forever. The 0.98..0.995
        # band is a known dead zone (0.5-2% damage on a true key) — priced
        # in deliberately, silence there beats accusing every short code.
        wide = looks_like_key and 0.6 <= uniqueness <= 0.98 and rows - distinct >= 3
        if wide:
            # short all-digit codes (zips, PINs, 4-digit account numbers)
            # birthday-collide by construction — 250 draws from 9000
            # four-digit values repeat ~3.5 times with no damage at all —
            # so they never qualify as damaged keys via the wide path
            sample = values.dropna().astype(str).str.strip()
            if (
                len(sample)
                and bool(sample.str.fullmatch(r"\d+").all())
                and int(sample.str.len().mode().iloc[0]) <= 5
            ):
                wide = False
        if not (wide or 0.995 <= uniqueness < 1.0) or distinct < 20:
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
        # datetime columns get domain sense too (taxonomy v2 fold): a
        # far-future timestamp or the exact epoch instant is what damaged
        # integers become, not a date anyone typed. Ordinary history is fine.
        raw = df.iloc[:, i]
        if pd.api.types.is_datetime64_any_dtype(raw):
            stamps = raw.dropna()
            if len(stamps) < 5:
                continue
            far_future = stamps[stamps.dt.year >= 2050]
            epoch = stamps[stamps == pd.Timestamp("1970-01-01")]
            violations = len(far_future) + (len(epoch) if len(epoch) >= 2 else 0)
            if len(far_future) == 0 and len(epoch) < 2:
                continue
            out.append(
                _finding(
                    13,
                    [_name(df, i)],
                    f"{len(far_future)} timestamps sit in 2050+ and "
                    f"{len(epoch)} at the exact epoch instant — cast "
                    "artifacts, not dates anyone typed",
                    {
                        "violations": violations,
                        "far_future": len(far_future),
                        "epoch": len(epoch),
                        "min": str(stamps.min()),
                        "max": str(stamps.max()),
                    },
                    "GATE",
                    0.85,
                )
            )
            continue
        numbers = _numbers(df, i)
        if numbers is None or len(numbers) < 5:
            continue
        key = _key(df, i)
        bounds = next(
            ((lo, hi) for pattern, lo, hi in DOMAIN_BOUNDS if pattern.search(key)), None
        )
        if bounds is None:
            # Data-driven fallback: the name table can never know every
            # domain (the bench caught it silent on planted negatives in
            # "amount"). A column that is overwhelmingly non-negative with a
            # stray few below zero is out of its own domain, whatever it is
            # called; a genuinely signed column (deltas, balances) has far
            # more than a stray few and stays out of reach.
            neg = numbers[numbers < 0]
            if len(numbers) >= 20 and 0 < len(neg) <= 0.25 * len(numbers):
                out.append(
                    _finding(
                        13,
                        [_name(df, i)],
                        f"{len(neg)} negative values in an otherwise "
                        f"non-negative column ({len(neg) / len(numbers):.1%} "
                        f"of it); samples: {_samples(neg)}",
                        {
                            "violations": len(neg),
                            "low": 0.0,
                            "high": None,
                            "min": float(numbers.min()),
                            "max": float(numbers.max()),
                        },
                        "GATE",
                        0.7,
                    )
                )
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
    # exchange signature: when the two columns' robust bands are disjoint, a
    # row sitting in each other's band is a swap even though both values stay
    # globally valid — the case the range checks above can never see. With
    # overlapping bands (regional data straddling the same values) the
    # signature is undefined and contributes nothing.
    lat_num = pd.to_numeric(lat, errors="coerce")
    lon_num = pd.to_numeric(lon, errors="coerce")
    lat_lo, lat_hi = lat_num.quantile(0.05), lat_num.quantile(0.95)
    lon_lo, lon_hi = lon_num.quantile(0.05), lon_num.quantile(0.95)
    swapped = 0
    if pd.notna(lat_hi) and pd.notna(lon_hi) and (lat_hi < lon_lo or lon_hi < lat_lo):
        swapped = int(
            (lat_num.between(lon_lo, lon_hi) & lon_num.between(lat_lo, lat_hi)).sum()
        )
    if zero_zero == 0 and out_of_range == 0 and swapped == 0:
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
            f"{zero_zero} rows sit at null island (0, 0), {out_of_range} fall outside "
            f"valid lat/lon ranges, {swapped} look lat/lon-swapped; "
            f"{in_bc:.0%} of rows are inside the BC bounding box",
            {
                "zero_zero": zero_zero,
                "out_of_range": out_of_range,
                "swapped": swapped,
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
            if label == "comma" and sum(_number_families(probe).values()) >= 0.8:
                # thousands commas are number formatting, not field packing:
                # '26,594.25' is d01's subject, and reporting it here too made
                # one disease look like two. Pipe and semicolon lists are
                # exempt — nothing writes money with those.
                continue
            return f"{label}_list", 0.85
    return None, 0.0


_PACK_DELIMITERS = {"comma": ",", "pipe": "|", "semicolon": ";"}


def _pack_count(values, kind: str) -> int:
    """The full-column count behind the claim _pack_kind sampled. The kind is
    decided on a probe for speed; the number in the evidence is measured where
    it is asserted — one vectorised pass either way."""
    if kind == "latlon_pair":
        return int(values.str.match(LATLON_PAIR).sum())
    if kind == "json":
        return int(values.str.match(JSON_ISH).sum())
    delimiter = _PACK_DELIMITERS[kind.removesuffix("_list")]
    return int((values.str.count(re.escape(delimiter)) >= 1).sum())


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
        packed = _pack_count(values, kind)
        out.append(
            _finding(
                17,
                [_name(df, i)],
                f"{packed}/{len(values)} values pack several fields into one "
                f"cell ({kind}); samples: {_samples(values)}",
                {"kind": kind, "values": len(values), "packed": packed},
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
    # padded/doubled-space names: invisible until the header fixer could
    # repair them (arc W2) — the detector must see everything its fixer fixes
    padded = [n for n in names if n.strip() != "" and (n != n.strip() or "  " in n)]
    header_rows = 0
    scan = df.head(200)
    for pos in range(len(scan)):
        if [str(v) for v in scan.iloc[pos].tolist()] == names:
            header_rows += 1
    if not bom and not duplicated and not unnamed and not padded and not header_rows:
        return []
    problems = []
    if bom:
        problems.append(f"{len(bom)} column names carry a BOM/zero-width character")
    if duplicated:
        problems.append(f"{duplicated} duplicate column names")
    if unnamed:
        problems.append(f"{len(unnamed)} unnamed columns")
    if padded:
        problems.append(f"{len(padded)} column names carry stray whitespace")
    if header_rows:
        problems.append(f"{header_rows} data rows repeat the header")
    affected = bom + unnamed + padded + [n for n in names if names.count(n) > 1]
    return [
        _finding(
            18,
            sorted(set(affected)),
            "; ".join(problems),
            {
                "bom_columns": len(bom),
                "duplicate_columns": duplicated,
                "unnamed_columns": len(unnamed),
                "padded": len(padded),
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
        if count > 0.2 * rows:
            continue
        if count == 1:
            # the classic real-world case is exactly ONE trailing TOTAL row,
            # which the old >= 2 floor silenced. A lone label may speak only
            # when the row corroborates as an aggregate: some numeric column
            # holds ~the sum of every other row there — a merchant that
            # merely NAMES itself "Total" has no such signature.
            row_label = hit[hit].index[0]  # _text keeps the frame's index
            corroborated = False
            for j in range(df.shape[1]):
                col_num = pd.to_numeric(df.iloc[:, j], errors="coerce")
                candidate = col_num.get(row_label)
                rest = col_num.drop(index=row_label).dropna()
                if candidate is None or pd.isna(candidate) or len(rest) < 5:
                    continue
                total = float(rest.sum())
                if total and math.isclose(float(candidate), total, rel_tol=1e-6):
                    corroborated = True
                    break
            if not corroborated:
                continue
        elif count < 1:
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
        values = df.iloc[:, i]
        if _is_texty(values):
            # Partial damage leaves a MIXED column — most ids keep their
            # prefixed/padded shape, a minority collapsed to bare digits or
            # scientific notation by a numeric cast somewhere upstream. That
            # column never parses as numbers, so the numeric path below is
            # structurally blind to it (bench 2026-09-02). An all-digit id
            # column (zips) has no intact majority and stays out of reach.
            vals = _text(df, i)
            if len(vals) >= 10:
                stripped = vals.str.strip()
                # Excel's text-guard leaking into the data: a few ids carry a
                # leading apostrophe the rest lack — same disease family as
                # eaten zeros (a cast mangled the representation), opposite
                # polarity (the guarded minority sits in a digit majority).
                guarded = stripped.str.match(r"'\S")
                n_guarded = int(guarded.sum())
                if 2 <= n_guarded <= 0.5 * len(stripped):
                    out.append(
                        _finding(
                            22,
                            [_name(df, i)],
                            f"{n_guarded} identifiers carry a leading "
                            "apostrophe — Excel's text-guard leaked into the "
                            f"data; samples: {_samples(stripped[guarded])}",
                            {"guarded": n_guarded, "values": len(stripped)},
                            "GATE",
                            0.9,
                        )
                    )
                eaten_mask = stripped.str.fullmatch(r"\d+|\d+(?:\.\d+)?[eE]\+?\d+")
                eaten = int(eaten_mask.sum())
                intact = int((~eaten_mask).sum())
                if eaten and intact >= max(eaten, 0.5 * len(stripped)):
                    out.append(
                        _finding(
                            22,
                            [_name(df, i)],
                            f"{eaten} of {len(stripped)} identifiers collapsed "
                            f"to bare digits or scientific notation while the "
                            f"rest keep a non-numeric shape — a numeric cast "
                            f"ate them; samples: {_samples(stripped[eaten_mask])}",
                            {
                                "eaten": eaten,
                                "intact": intact,
                                "values": len(stripped),
                            },
                            "GATE",
                            0.85,
                        )
                    )
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


def _flat(df):
    """The same frame on a positional index when labels repeat.

    Detectors filter with label-aligned boolean masks; on a non-unique index
    pandas pays a non-unique lookup per row and detection goes quadratic —
    14s at 2,000 rows sharing four labels, never returning at 100k. And
    set_index() on a low-cardinality column is the standard first move on a
    transaction table, so this is an entry-point concern, not a caller bug.
    Shallow copy: the blocks are shared, only the axis is replaced."""
    if df.index.is_unique:
        return df
    flat = df.copy(deep=False)
    flat.index = pd.RangeIndex(len(flat))
    return flat


# --- 24. stray structural rows (header echoes, footer junk) ------------------


@register(24)
def _d24(df, cols) -> list:
    """Concatenated exports leave the header echoed as a data row and
    mostly-empty footer junk at the end. Row-granular structure damage; GATE
    because row deletion is always a judgment call. Bounded scan: header
    echoes over the first 50k rows, footer junk over the last handful."""
    rows = len(df)
    if rows < 10 or df.shape[1] < 2:
        return []
    folded_names = [str(c).strip().lower() for c in df.columns]
    # cheap pre-screen: an echo row needs column 0 to contain its own name,
    # so one Series scan decides whether the expensive full fold ever runs
    first = df.iloc[:, 0].head(50_000).astype(str).str.strip().str.lower()
    header_rows: list[int] = []
    if bool((first == folded_names[0]).any()):
        scan = df.head(50_000).astype(str).apply(lambda s: s.str.strip().str.lower())
        echo_share = scan.eq(folded_names).mean(axis=1)
        header_rows = [int(i) for i, share in enumerate(echo_share) if share >= 0.6]

    tail_n = min(3, rows)
    tail = df.tail(tail_n).astype(str).apply(lambda s: s.str.strip().str.lower())
    tail_missing = df.tail(tail_n).isna() | tail.isin({"", "nan", "none"})
    body_missing = float(
        (df.head(rows - tail_n).isna()).mean(axis=1).mean() if rows > tail_n else 0.0
    )
    footer_rows = [
        int(pos)
        for offset, pos in enumerate(range(rows - tail_n, rows))
        if pos not in header_rows
        and float(tail_missing.iloc[offset].mean()) >= max(0.6, body_missing + 0.4)
    ]
    if not header_rows and not footer_rows:
        return []
    return [
        _finding(
            24,
            [],
            f"{len(header_rows)} row(s) echo the header and {len(footer_rows)} "
            f"trailing row(s) are mostly-empty footer junk mixed into the data",
            {
                "header_rows": len(header_rows),
                "footer_rows": len(footer_rows),
                "positions": (header_rows + footer_rows)[:8],
                "rows": rows,
            },
            "GATE",
            0.9,
        )
    ]


# --- 26. truncated values ----------------------------------------------------


@register(26)
def _d26(df, cols) -> list:
    """A varchar ceiling shows as many DISTINCT values piled at exactly one
    length in an otherwise varied column, or a literal trailing ellipsis.
    Fixed-width codes are all one length by design and stay silent — variety
    plus a shared ceiling is the signature. HUMAN, no fixer: the lost tail is
    unknowable, so repair is never a deterministic call."""
    out = []
    for i in _targets(df, cols):
        values = _text(df, i)
        if values is None or len(values) < 15:
            continue
        stripped = values.str.strip()
        lengths = stripped.str.len()
        ellipsis = int(stripped.str.endswith(("…", "...")).sum())
        n = len(stripped)
        max_len = int(lengths.max()) if n else 0
        varied = int(lengths.nunique()) >= 5
        # Only the two ROBUST signatures fire deterministically: a literal
        # ellipsis, or edge dominance (a varied column where a quarter-plus
        # of distinct values pile at exactly the max — everything was cut).
        # Interior-ceiling heuristics are deliberately absent: templated
        # text produces comb-shaped length distributions whose gaps mimic
        # ceilings, and this detector's first draft fired on exactly that.
        ceiling, at_ceiling = 0, 0
        if varied and max_len >= 8:
            count_at_max = int((lengths == max_len).sum())
            distinct_at_max = int(stripped[lengths == max_len].nunique())
            if (
                count_at_max >= 0.25 * n
                and distinct_at_max / count_at_max >= 0.9
                and int(lengths.nunique()) >= 6
            ):
                ceiling, at_ceiling = max_len, distinct_at_max
        if ellipsis == 0 and at_ceiling < 3:
            continue
        out.append(
            _finding(
                26,
                [_name(df, i)],
                f"{at_ceiling} distinct values sit exactly at the {ceiling}-char "
                f"ceiling and {ellipsis} end in an ellipsis — a varchar limit or "
                "display cut ate the tails",
                {
                    "at_ceiling": at_ceiling,
                    "ceiling": ceiling,
                    "ellipsis": ellipsis,
                    "values": len(stripped),
                },
                "HUMAN",
                0.85,
            )
        )
    return out


# --- 25. duplicated columns --------------------------------------------------


@register(25)
def _d25(df, cols) -> list:
    """Identical content under two names — post-join _x/_y debris. Hash-first
    so wide frames pay one vectorised digest per column, with full equality
    confirmed only on hash twins. GATE: dropping a column is a judgment call
    (which name survives is semantics the data can't tell us)."""
    if len(df) < 10 or df.shape[1] < 2:
        return []
    digests: dict[bytes, list[int]] = {}
    for i in _targets(df, cols):
        col = df.iloc[:, i]
        try:
            key = pd.util.hash_pandas_object(col, index=False).to_numpy().tobytes()
        except TypeError:
            continue  # unhashable exotics: never worth crashing the run over
        digests.setdefault(key, []).append(i)
    out = []
    for twins in digests.values():
        if len(twins) < 2:
            continue
        first = df.iloc[:, twins[0]]
        confirmed = [twins[0]] + [j for j in twins[1:] if first.equals(df.iloc[:, j])]
        if len(confirmed) < 2:
            continue
        names = [_name(df, j) for j in confirmed]
        out.append(
            _finding(
                25,
                names,
                f"{len(confirmed)} columns carry identical content "
                f"({', '.join(repr(n) for n in names[:4])}) — every sum over "
                "them double counts",
                {"columns": names, "rows": len(df)},
                "GATE",
                0.95,
            )
        )
    return out


# --- 23. boolean representation chaos ---------------------------------------

_BOOL_FAMILIES = (
    frozenset({"y", "n"}),
    frozenset({"yes", "no"}),
    frozenset({"true", "false"}),
    frozenset({"t", "f"}),
    frozenset({"1", "0"}),
)
_BOOL_VOCAB = frozenset().union(*_BOOL_FAMILIES)


@register(23)
def _d23(df, cols) -> list:
    """One truth, many spellings: Y/yes/TRUE/1 mixed in a single column. A
    consistent convention (a clean Y/N pair, a 0/1 int column) is healthy —
    the disease is REPRESENTATION mixing, so it needs two spelling families."""
    out = []
    for i in _targets(df, cols):
        values = _present(df, i)
        if values is None or len(values) < 10:
            continue
        distinct = set(values.astype(str).str.strip().str.lower().unique())
        # subset-of-vocab is the real gate; chaos can show every spelling
        if len(distinct) < 2 or not distinct <= _BOOL_VOCAB:
            continue
        families = sum(1 for fam in _BOOL_FAMILIES if distinct & fam)
        if families < 2:
            continue
        out.append(
            _finding(
                23,
                [_name(df, i)],
                f"{len(distinct)} boolean spellings from {families} conventions "
                f"in one column ({', '.join(sorted(distinct))})",
                {
                    "distinct": sorted(distinct),
                    "families": families,
                    "values": len(values),
                },
                "AUTO",
                0.95,
            )
        )
    return out


def detect_all(df, name: str = "df") -> dict:
    """Run every single-frame signal. Returns findings plus the ids that ran
    and found nothing — absence is a checked claim, not a silence (R1) — plus
    the ids that could not run at all. A crashed detector must never look
    like a clean one: silently dropping it from both findings and clear would
    make a systematically broken signal invisible forever."""
    if df is None or len(df) == 0 or df.shape[1] == 0:
        return {"findings": [], "clear": list(SINGLE_FRAME), "broken": {}}
    df = _flat(df)
    findings, broke, broken = [], set(), {}
    try:
        for disease in SINGLE_FRAME:
            try:
                findings.extend(REGISTRY[disease](df, None))
            except Exception as exc:  # noqa: BLE001 - one brittle signal must never sink the run
                broke.add(disease)
                # same collapse and cap as _finding's evidence: this string is
                # rendered raw as a report bullet, and a third-party exception
                # carries whatever line breaks and repr lengths it likes
                reason = EVIDENCE_LINEBREAKS.sub(" ", f"{type(exc).__name__}: {exc}")
                broken[str(disease)] = reason[:240]
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
    df = _flat(df)
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
