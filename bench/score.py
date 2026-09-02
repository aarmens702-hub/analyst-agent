"""The Proving Ground's scorer (spec R3): detection quality, end-to-end repair
quality, and verification survival, all measured from outside `crivo` — this
module owns its own equivalence rule (`autoclean._same` is private, and bench
never imports core internals) so a scoring bug can never hide behind a shared
helper with the thing it is grading.
"""

from __future__ import annotations

import datetime
import math

import numpy as np
import pandas as pd

from crivo.autoclean import FIXERS, clean
from crivo.detect import detect_all


def _is_missing(x) -> bool:
    """None, float NaN, pd.NaT, pd.NA are one value: the missing family."""
    if x is None or x is pd.NA:
        return True
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


def _is_number(x) -> bool:
    """int/float/np numbers — bool excluded, even though bool subclasses int."""
    return not isinstance(x, bool) and isinstance(
        x, (int, float, np.integer, np.floating)
    )


def _is_datetime(x) -> bool:
    return isinstance(x, (datetime.date, np.datetime64, pd.Timestamp))


def equivalent(a, b) -> bool:
    """Type-aware value equivalence for scoring.

    The missing family collapses to one value; numbers cross-compare within a
    1e-9 relative tolerance; datetime-like values compare by instant;
    everything else needs exact equality in the same type family. A str is
    never equivalent to a number or a timestamp — dtype damage is damage, a
    deliberate scoring decision, not an oversight. A bool likewise compares
    only to another bool: True and 1 are not the same fix.

    Never raises: an incomparable pair is just not equivalent.
    """
    try:
        a_missing, b_missing = _is_missing(a), _is_missing(b)
        if a_missing or b_missing:
            return a_missing and b_missing
        if isinstance(a, bool) or isinstance(b, bool):
            return isinstance(a, bool) and isinstance(b, bool) and a == b
        if _is_number(a) and _is_number(b):
            return math.isclose(float(a), float(b), rel_tol=1e-9)
        a_dt, b_dt = _is_datetime(a), _is_datetime(b)
        if a_dt or b_dt:
            return a_dt and b_dt and bool(pd.Timestamp(a) == pd.Timestamp(b))
        return bool(a == b)
    except Exception:  # noqa: BLE001 - an incomparable pair must never crash the scorer
        # incomparable pair (mismatched exotic types, tz-naive vs tz-aware,
        # a custom __eq__ that raises) — never crash the scorer over one cell
        return False


def _positions(
    columns, n_rows: int, left: pd.DataFrame, right: pd.DataFrame, want_equivalent: bool
) -> set[tuple[int, str]]:
    """Row/column positions (row < n_rows) where `left` and `right` do (or, if
    `want_equivalent` is False, do not) agree, restricted to `columns`. A
    column absent from either frame counts as disagreeing everywhere — a
    renamed or dropped column is column-granular damage, not a per-cell
    question, so it can never contribute to the "agree" side."""
    out = set()
    for column in columns:
        name = str(column)
        if column not in left.columns or column not in right.columns:
            if not want_equivalent:
                out.update((row, name) for row in range(n_rows))
            continue
        l_col, r_col = left[column], right[column]
        l_len, r_len = len(l_col), len(r_col)
        for row in range(n_rows):
            l_val = l_col.iat[row] if row < l_len else None
            r_val = r_col.iat[row] if row < r_len else None
            if equivalent(l_val, r_val) == want_equivalent:
                out.add((row, name))
    return out


def dirty_cells(pristine: pd.DataFrame, dirty: pd.DataFrame) -> set[tuple[int, str]]:
    """Cells where `dirty` diverges from `pristine`, scoped to the pristine's
    own rows and columns — appended rows and renamed columns are row/column-
    granular damage, scored elsewhere, not cell damage."""
    return _positions(pristine.columns, len(pristine), pristine, dirty, False)


def _f1(precision: float | None, recall: float | None) -> float | None:
    """Harmonic mean. None whenever precision or recall is itself undefined,
    or when P+R is 0 — the F1 formula's own zero denominator, never a NaN."""
    if precision is None or recall is None:
        return None
    denom = precision + recall
    return 2 * precision * recall / denom if denom else None


def score_end_to_end(
    pristine: pd.DataFrame, dirty: pd.DataFrame, cleaned: pd.DataFrame, truth
) -> dict:
    """Cell-level dirt-targeting and repair quality (Baran-comparable),
    scored over the pristine grid only: rows < truth.n_rows, pristine's own
    columns. D = truly dirty, C = touched by the cleaner, K = ended correct."""
    n = truth.n_rows
    columns = pristine.columns
    D = _positions(columns, n, pristine, dirty, False)
    C = _positions(columns, n, dirty, cleaned, False)
    K = _positions(columns, n, pristine, cleaned, True)

    c_and_d = C & D
    d_and_k = D & K
    c_and_d_and_k = C & D & K

    dt_precision = len(c_and_d) / len(C) if C else None
    dt_recall = len(c_and_d) / len(D) if D else None
    rp_precision = len(c_and_d_and_k) / len(C) if C else None
    rp_recall = len(d_and_k) / len(D) if D else None

    return {
        "dirt_targeting": {
            "precision": dt_precision,
            "recall": dt_recall,
            "f1": _f1(dt_precision, dt_recall),
        },
        "repair": {
            "precision": rp_precision,
            "recall": rp_recall,
            "f1": _f1(rp_precision, rp_recall),
        },
        "counts": {"dirty": len(D), "changed": len(C), "repaired": len(d_and_k)},
    }


def _columns_match(a_columns, b_columns) -> bool:
    """Column-set intersection, with a wildcard for whole-row/whole-frame
    scope: disease 9 (duplicate rows) and disease 18 (header damage) findings
    carry `columns: []` because they are not about any one column. Literal
    set intersection would make an empty set match nothing — not even another
    empty set — so an empty side matches anything of the same disease rather
    than nothing of it."""
    a, b = set(a_columns), set(b_columns)
    if not a or not b:
        return True
    return bool(a & b)


def score_detection(detect_result: dict, truth) -> dict:
    """Column x disease precision/recall/F1. A finding is a TP iff truth
    holds a same-disease corruption with intersecting columns; a truth
    corruption is recalled iff some finding matches it the same way. Disease
    0 (external/unknown) carries no taxonomy and is excluded on both sides."""
    findings = [f for f in detect_result["findings"] if f["disease"] != 0]
    corruptions = [c for c in truth.corruptions if c.disease != 0]

    diseases = sorted(
        {f["disease"] for f in findings} | {c.disease for c in corruptions}
    )
    per_disease = {}
    for disease in diseases:
        d_findings = [f for f in findings if f["disease"] == disease]
        d_truth = [c for c in corruptions if c.disease == disease]
        tp = sum(
            any(_columns_match(f["columns"], c.columns) for c in d_truth)
            for f in d_findings
        )
        fp = len(d_findings) - tp
        fn = sum(
            not any(_columns_match(c.columns, f["columns"]) for f in d_findings)
            for c in d_truth
        )
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        per_disease[str(disease)] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }

    truth_diseases = sorted({c.disease for c in corruptions})
    macro_f1 = None
    if truth_diseases:
        f1s = [per_disease[str(d)]["f1"] or 0.0 for d in truth_diseases]
        macro_f1 = sum(f1s) / len(f1s)

    total_tp = sum(v["tp"] for v in per_disease.values())
    total_fp = sum(v["fp"] for v in per_disease.values())
    total_fn = sum(v["fn"] for v in per_disease.values())
    micro_precision = (
        total_tp / (total_tp + total_fp) if (total_tp + total_fp) else None
    )
    micro_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else None

    return {
        "per_disease": per_disease,
        "macro_f1": macro_f1,
        "micro": {
            "precision": micro_precision,
            "recall": micro_recall,
            "f1": _f1(micro_precision, micro_recall),
        },
    }


def score_verification(dirty: pd.DataFrame, summary) -> dict:
    """Verification stats from outside crivo: how many AUTO-grade, fixer-
    backed findings survived clean()'s own re-verification to become an
    applied fix, with no core changes needed to observe it."""
    findings = detect_all(dirty)["findings"]
    attempted = sum(
        1 for f in findings if f["grade"] == "AUTO" and f["disease"] in FIXERS
    )
    applied = len(summary.applied)
    return {
        "attempted": attempted,
        "applied": applied,
        "survived_rate": applied / attempted if attempted else None,
        "needs_review": len(summary.needs_review),
    }


def score_pair(
    pristine: pd.DataFrame, dirty: pd.DataFrame, truth, name: str = "df"
) -> dict:
    """The full per-dataset scoring composition: detection quality, end-to-
    end repair quality, and verification survival — plus an honest split of
    which truth diseases deterministic mode could even attempt, so aggregate
    tables never blend "not attempted" into a blended score."""
    truth.verify_frame(dirty)  # refuse a frame/manifest pair that drifted apart
    detect_result = detect_all(dirty, name=name)
    cleaned, summary = clean(dirty)
    truth_diseases = sorted({c.disease for c in truth.corruptions if c.disease != 0})
    return {
        "detection": score_detection(detect_result, truth),
        "end_to_end": score_end_to_end(pristine, dirty, cleaned, truth),
        "verification": score_verification(dirty, summary),
        "attempted_diseases": sorted(d for d in truth_diseases if d in FIXERS),
        "not_attempted_diseases": sorted(d for d in truth_diseases if d not in FIXERS),
    }
