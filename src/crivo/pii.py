"""PII detection and masking (capability roadmap B0.1).

The ship-safety prerequisite for the shareable HTML report: a report with
embedded data is a leak vector, so a "PII scan before share" is a
receipts-native safety feature. Keyless and pure-stdlib: conservative
full-cell regex detectors plus a Luhn check on card candidates, so a
receipts tool does not cry wolf. Every hit carries its count and a masked
sample as evidence, and it is graded HUMAN: masking loses data, so whether
to redact a column is always a person's call, never an auto-fix. Names and
street addresses need NER and are deliberately outside the keyless path
(an optional Presidio-backed extra is the place for them later).
"""

from __future__ import annotations

import re

import pandas as pd

REDACTED = "[REDACTED]"

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_SSN = re.compile(r"\d{3}-\d{2}-\d{4}")
_PHONE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def _luhn(digits: str) -> bool:
    """True when a digit string passes the Luhn checksum (card validity)."""
    nums = [int(c) for c in digits]
    checkdigit = nums[-1]
    body = nums[-2::-1]
    doubled = 0
    for i, d in enumerate(body):
        v = d * 2 if i % 2 == 0 else d
        doubled += v - 9 if v > 9 else v
    return (doubled + checkdigit) % 10 == 0


def _is_card(cell: str) -> bool:
    digits = re.sub(r"[ -]", "", cell)
    return digits.isdigit() and 13 <= len(digits) <= 19 and _luhn(digits)


# priority order: the first type whose detector matches the whole cell wins,
# so a 16-digit card is never also read as a phone
_DETECTORS = (
    ("email", lambda c: bool(_EMAIL.fullmatch(c))),
    ("ssn", lambda c: bool(_SSN.fullmatch(c))),
    ("credit_card", _is_card),
    ("phone", lambda c: bool(_PHONE.fullmatch(c))),
)


def _cell_type(cell: str) -> str | None:
    text = cell.strip()
    for name, matches in _DETECTORS:
        if matches(text):
            return name
    return None


def mask_value(value: str, pii_type: str) -> str:
    """Redact one value of a known PII type. Email keeps its domain so the
    column still reads as email without exposing the local part; everything
    else becomes REDACTED."""
    if pii_type == "email" and "@" in value:
        return "***@" + value.split("@", 1)[1]
    return REDACTED


def mask_column(series: pd.Series, pii_type: str) -> pd.Series:
    """Mask only the cells that match `pii_type`; leave the rest untouched."""

    def one(value):
        if isinstance(value, str) and _cell_type(value) == pii_type:
            return mask_value(value.strip(), pii_type)
        return value

    return series.map(one)


def scan(df: pd.DataFrame) -> list[dict]:
    """One finding per text column carrying PII, typed by the dominant kind.

    Only object-dtype columns are read (a numeric column of card-length ints
    is not PII text). Each finding is graded HUMAN with a masked sample and a
    count as evidence, so exposure is surfaced but never auto-decided.
    """
    findings = []
    for col in df.columns:
        s = df[col]
        # object OR string dtype (pandas may infer pure-string columns as the
        # string dtype, not object); numeric/bool/datetime are never PII text
        if not (pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)):
            continue
        counts: dict[str, int] = {}
        first: dict[str, str] = {}
        for value in df[col]:
            if not isinstance(value, str):
                continue
            kind = _cell_type(value)
            if kind is None:
                continue
            counts[kind] = counts.get(kind, 0) + 1
            first.setdefault(kind, value.strip())
        if not counts:
            continue
        kind = max(counts, key=lambda k: counts[k])
        n = counts[kind]
        findings.append(
            {
                "column": col,
                "pii_type": kind,
                "count": n,
                "sample": mask_value(first[kind], kind),
                "grade": "HUMAN",
                "confidence": 0.9,
                "evidence": f"{n} {kind} value(s) in column {col!r}",
            }
        )
    return findings
