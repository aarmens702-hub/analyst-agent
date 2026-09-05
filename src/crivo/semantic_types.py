"""Semantic column typing (capability roadmap B4.1).

Infers what a text column IS from its values with conservative,
evidence-carrying heuristics. A column types only when a strong majority of
its non-null cells match one detector (threshold 0.90, named in the
evidence); an ambiguous column yields nothing. Conservative beats greedy:
this is a receipts tool, so it under-claims rather than over-claims.

Independent of pii.py by design: email and phone shapes overlap that module,
but this one answers "what is this column" (typing), not "is this column
exposed" (PII). Keyless and pure stdlib plus pandas: importing it touches no
network and needs no key.
"""

from __future__ import annotations

import re

import pandas as pd

# a strong majority of non-null cells must match one detector before a column
# types; named in every finding's evidence so the threshold is auditable
THRESHOLD = 0.9

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_URL = re.compile(r"(?:https?://|www\.)[^\s]+\.[^\s]+")
_PHONE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_PHONE_SEP = set("()-. +")
# the unambiguous ZIP+4 form only: a bare 5-digit value cannot be told apart
# from a 5-digit account or record number by its value, so under the
# under-claim contract a plain-5-digit column types as nothing
_ZIP = re.compile(r"\d{5}-\d{4}")
# a value stored as text that is really a date; such a column is not an
# identifier even when its values happen to be unique per row
_DATEISH = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}")
# a number with optional comma grouping and optional decimals, used as the
# amount part of a currency value
_NUMBER = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
# a plain decimal number (no grouping); such a value is numeric, not a code,
# so it can never be an identifier
_PLAIN_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
# a compact code token: alphanumerics plus a few separators, no whitespace
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")

_CUR_SYMBOLS = "$€£¥₹"  # $ EUR GBP JPY INR
_CUR_CODES = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CNY",
        "INR",
        "CAD",
        "AUD",
        "CHF",
        "NZD",
        "SEK",
        "NOK",
        "DKK",
        "HKD",
        "SGD",
        "ZAR",
        "BRL",
        "MXN",
        "KRW",
        "RUB",
        "PLN",
        "TRY",
        "AED",
        "SAR",
        "THB",
    }
)

# a curated common set, not the full ISO-3166 table: an uncommon country
# under-claims (safe) rather than a rare 2-letter token over-claiming
_COUNTRY_ALPHA2 = frozenset(
    {
        "US",
        "CA",
        "GB",
        "MX",
        "BR",
        "AR",
        "DE",
        "FR",
        "ES",
        "IT",
        "PT",
        "NL",
        "BE",
        "CH",
        "AT",
        "SE",
        "NO",
        "DK",
        "FI",
        "IE",
        "PL",
        "CZ",
        "GR",
        "RU",
        "TR",
        "UA",
        "IN",
        "CN",
        "JP",
        "KR",
        "ID",
        "TH",
        "VN",
        "PH",
        "MY",
        "SG",
        "AU",
        "NZ",
        "ZA",
        "NG",
        "EG",
        "KE",
        "SA",
        "AE",
        "IL",
        "PK",
        "BD",
        "CL",
        "CO",
        "PE",
    }
)
_COUNTRY_ALPHA3 = frozenset(
    {
        "USA",
        "CAN",
        "GBR",
        "MEX",
        "BRA",
        "ARG",
        "DEU",
        "FRA",
        "ESP",
        "ITA",
        "PRT",
        "NLD",
        "BEL",
        "CHE",
        "AUT",
        "SWE",
        "NOR",
        "DNK",
        "FIN",
        "IRL",
        "POL",
        "CZE",
        "GRC",
        "RUS",
        "TUR",
        "UKR",
        "IND",
        "CHN",
        "JPN",
        "KOR",
        "IDN",
        "THA",
        "VNM",
        "PHL",
        "MYS",
        "SGP",
        "AUS",
        "NZL",
        "ZAF",
        "NGA",
        "EGY",
        "KEN",
        "SAU",
        "ARE",
        "ISR",
        "PAK",
        "BGD",
        "CHL",
        "COL",
        "PER",
    }
)
_COUNTRY_NAMES = frozenset(
    {
        "united states",
        "united states of america",
        "usa",
        "canada",
        "united kingdom",
        "uk",
        "great britain",
        "mexico",
        "brazil",
        "argentina",
        "germany",
        "france",
        "spain",
        "italy",
        "portugal",
        "netherlands",
        "belgium",
        "switzerland",
        "austria",
        "sweden",
        "norway",
        "denmark",
        "finland",
        "ireland",
        "poland",
        "czechia",
        "czech republic",
        "greece",
        "russia",
        "turkey",
        "ukraine",
        "india",
        "china",
        "japan",
        "south korea",
        "korea",
        "indonesia",
        "thailand",
        "vietnam",
        "philippines",
        "malaysia",
        "singapore",
        "australia",
        "new zealand",
        "south africa",
        "nigeria",
        "egypt",
        "kenya",
        "saudi arabia",
        "united arab emirates",
        "uae",
        "israel",
        "pakistan",
        "bangladesh",
        "chile",
        "colombia",
        "peru",
    }
)


def _is_currency(cell: str) -> bool:
    """A number with a leading currency symbol or ISO code, e.g. "$1,234.56"
    or "USD 1,234.56"."""
    s = cell.strip()
    if not s:
        return False
    if s[0] in "+-":  # a leading sign before the symbol, e.g. -$5.00
        s = s[1:]
    if s[:1] in _CUR_SYMBOLS:
        return bool(_NUMBER.fullmatch(s[1:].strip()))
    head = s[:3].upper()
    # require a real amount after the code, and reject a longer word that only
    # starts with a code (e.g. USDT) by refusing a 4th alphabetic character
    if head in _CUR_CODES and len(s) > 3 and not s[3].isalpha():
        return bool(_NUMBER.fullmatch(s[3:].strip()))
    return False


def _is_country(cell: str) -> bool:
    """An ISO-3166 alpha-2/alpha-3 code or a common country name."""
    s = cell.strip()
    u = s.upper()
    if len(u) == 2:
        return u in _COUNTRY_ALPHA2
    if len(u) == 3:
        return u in _COUNTRY_ALPHA3
    return s.lower() in _COUNTRY_NAMES


def _is_phone(cell: str) -> bool:
    """Phone-shaped AND carrying phone formatting: a bare run of digits is
    ambiguous with an account or record number, so a confident phone call
    requires a separator (parens, dash, dot, space) or a leading +."""
    s = cell.strip()
    if not _PHONE.fullmatch(s):
        return False
    return any(ch in _PHONE_SEP for ch in s)


def _is_code(cell: str) -> bool:
    """Code-shaped: a compact token carrying at least one digit, not a plain
    decimal number and not a date. The digit requirement keeps a plain-word
    column (product names) from typing as identifier; the date exclusion
    keeps a text date column from doing the same."""
    s = cell.strip()
    if not s or " " in s or len(s) > 64:
        return False
    if _PLAIN_NUMBER.fullmatch(s) or _DATEISH.fullmatch(s):
        return False
    if not any(ch.isdigit() for ch in s):
        return False
    return bool(_CODE.fullmatch(s))


# specific value detectors, checked before the identifier fallback; the first
# whose match rate clears THRESHOLD types the column
_DETECTORS = (
    ("email", lambda c: bool(_EMAIL.fullmatch(c))),
    ("url", lambda c: bool(_URL.fullmatch(c))),
    ("phone", _is_phone),
    ("zip_code", lambda c: bool(_ZIP.fullmatch(c))),
    ("currency_amount", _is_currency),
    ("country", _is_country),
)


def _present_values(s: pd.Series) -> list:
    """Non-null, non-blank cells. A whitespace-only string is treated as
    missing so a few blanks do not sink an otherwise clean column."""
    out = []
    for v in s:
        if pd.isna(v):
            continue
        if isinstance(v, str):
            t = v.strip()
            if t:
                out.append(t)
        else:
            out.append(v)
    return out


def _rate(present: list, predicate) -> float:
    """Fraction of present cells that are strings matching `predicate`."""
    hits = sum(1 for v in present if isinstance(v, str) and predicate(v))
    return hits / len(present)


def _finding(column, semantic_type: str, match_rate: float, evidence: str) -> dict:
    return {
        "column": column,
        "semantic_type": semantic_type,
        "confidence": round(match_rate, 2),
        "evidence": evidence,
        "match_rate": match_rate,
    }


def _classify(column, present: list) -> dict | None:
    """The best specific detector wins if it clears THRESHOLD; otherwise a
    unique, code-shaped column falls back to identifier."""
    unique = len(present) >= 2 and len(set(present)) == len(present)
    best_name, best_rate = None, 0.0
    for name, predicate in _DETECTORS:
        r = _rate(present, predicate)
        if r > best_rate:
            best_name, best_rate = name, r
    if best_rate >= THRESHOLD:
        evidence = (
            f"{best_rate:.2f} of non-null cells match {best_name} "
            f"(threshold {THRESHOLD:.2f})"
        )
        return _finding(column, best_name, best_rate, evidence)

    # identifier: unique per row and code-shaped, never plain numeric or a date
    code_rate = _rate(present, _is_code)
    if unique and code_rate >= THRESHOLD:
        evidence = (
            f"{code_rate:.2f} of non-null cells code-shaped, all unique per "
            f"row (threshold {THRESHOLD:.2f})"
        )
        return _finding(column, "identifier", code_rate, evidence)
    return None


def infer_types(df: pd.DataFrame) -> list[dict]:
    """Infer the semantic type of each text column (B4.1).

    Returns one dict per column that confidently matches a single detector,
    with keys column, semantic_type, confidence, evidence, and match_rate.
    Only object or string dtype columns are read (pandas may infer a pure
    string column as the string dtype, not object); numeric, bool and
    datetime columns are never treated as text. A column types only when the
    best detector matches at least THRESHOLD of its non-null cells, so an
    ambiguous column yields nothing.
    """
    findings = []
    for col in df.columns:
        s = df[col]
        if not (pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)):
            continue
        present = _present_values(s)
        if not present:
            continue
        result = _classify(col, present)
        if result is not None:
            findings.append(result)
    return findings
