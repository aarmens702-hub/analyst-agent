"""Deterministic cleaning — the wedge (Phase 1.1).

`clean(df)` applies the mechanical fixes the detectors already imply — no LLM,
no kernel — and verifies each one the way the agent does: the detector that
found the disease must re-run clean, or the fix is discarded. Nothing is
trusted because a rule ran; it's trusted because the signal went quiet.

Only value-level fixes and the universally-safe constant-column drop auto-apply.
Anything that deletes rows (dedup, near-dup merges) or needs a judgement call
(ambiguous money conventions, contradictions, outliers) is *reported*, never
decided — the same AUTO / GATE / HUMAN line the agent honours.
"""

import json as _json

import pandas as pd

from analyst_agent.detect import (
    LEADING_NUMBER,
    MISSING_TOKENS,
    _ws_tidy,
    detect_all,
    detect_one,
)


def _fix_numbers(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = frame.copy()
    for c in cols:
        pulled = out[c].astype(str).str.extract(LEADING_NUMBER)
        out[c] = pd.to_numeric(
            (pulled[0].fillna("") + pulled[1].fillna("")).str.replace(
                ",", "", regex=False
            ),
            errors="coerce",
        )
    return out


def _fix_dates(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = frame.copy()
    for c in cols:
        out[c] = pd.to_datetime(out[c], errors="coerce")
    return out


def _fix_sentinels(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = frame.copy()
    for c in cols:
        low = out[c].astype(str).str.strip().str.lower()
        out.loc[low.isin(MISSING_TOKENS), c] = None
    return out


def _fix_whitespace(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = frame.copy()
    for c in cols:
        mask = out[c].notna()
        out.loc[mask, c] = _ws_tidy(out[c][mask].astype(str))
    return out


def _fix_case_variants(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = frame.copy()
    for c in cols:
        mask = out[c].notna()
        vals = out[c][mask].astype(str)
        tidy = vals.str.replace(r"\s+", " ", regex=True).str.strip()
        key = tidy.str.lower()
        # each normalised key -> its most frequent real spelling, so 'IT' is
        # preserved over 'it' rather than lowercased
        canon = {k: tidy[key == k].value_counts().index[0] for k in key.unique()}
        out.loc[mask, c] = key.map(canon).to_numpy()
    return out


def _drop_constant(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    return frame.drop(columns=[c for c in cols if c in frame.columns])


# disease -> deterministic fixer. Row-deleting diseases (9 dup-rows, 10
# near-dup) are deliberately absent: dropping a row is destructive and a
# judgement call, so it is reported, never auto-applied.
FIXERS = {
    1: _fix_numbers,
    2: _fix_dates,
    4: _fix_sentinels,
    6: _fix_whitespace,
    7: _fix_case_variants,
    19: _drop_constant,
}
# apply order: clear sentinels and whitespace before coercing types; structural
# drops last, so a value fix never runs against an already-mutated shape
_ORDER = [4, 6, 7, 1, 2, 19]


class CleanSummary:
    """What `clean` did and what it left for a human. Reads for a person,
    serialises for a machine."""

    def __init__(self, before, after, applied, needs_review):
        self._before = before
        self._after = after
        self.applied = applied
        self.needs_review = needs_review

    def samples(self, per_fix: int = 3) -> list[dict]:
        """The before/after receipts for each applied fix — what actually
        changed, for the notebook renderer and anyone who wants the diff."""
        return changed_cells(self._before, self._after, self.applied, per_fix)

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "needs_review": self.needs_review,
            "rows_before": len(self._before),
            "rows_after": len(self._after),
            "columns_before": len(self._before.columns),
            "columns_after": len(self._after.columns),
        }

    def to_json(self, indent: int | None = 2) -> str:
        return _json.dumps(self.to_dict(), indent=indent, default=str)

    def __repr__(self) -> str:
        lines = [
            (
                f"cleaned: {len(self.applied)} fix(es) applied, "
                f"{len(self.needs_review)} left for review"
            )
        ]
        for a in self.applied:
            where = ", ".join(a["columns"]) or "whole table"
            lines.append(f"  ✓ d{a['disease']:02d} {a['slug']} [{where}]")
        for r in self.needs_review:
            where = ", ".join(r["columns"]) or "whole table"
            why = f" — {r['reason']}" if r.get("reason") else ""
            lines.append(f"  · d{r['disease']:02d} {r['slug']} [{where}]{why}")
        return "\n".join(lines)

    def _repr_html_(self) -> str:
        from analyst_agent import notebook as _notebook

        return _notebook.clean_html(self)

    def diff(self, max_rows: int = 200):
        """A pandas Styler of the cleaned frame with every changed cell
        highlighted — the opt-in full-frame view (needs jinja2; see
        `styler_diff`). The inline before/after in the notebook card needs no
        such dependency."""
        return styler_diff(self._before, self._after, max_rows)


def _slim(finding: dict, **extra) -> dict:
    return {
        "disease": finding["disease"],
        "slug": finding["slug"],
        "columns": finding["columns"],
        "grade": finding["grade"],
        **extra,
    }


def changed_cells(
    before: pd.DataFrame, after: pd.DataFrame, applied: list[dict], per_fix: int = 3
) -> list[dict]:
    """The concrete cells each applied fix changed — the receipts for `clean`.

    For every fix in `applied`, up to `per_fix` example cells whose value differs
    between `before` and `after` in that fix's columns. Rows align because clean
    never deletes rows (dedup is deferred), so a cell change is well-defined by
    (column, position). A column present in `before` but gone from `after` is a
    constant-drop (disease 19): reported as a single `removed` example carrying
    the dropped constant, not a value pair.
    """
    out: list[dict] = []
    for fix in applied:
        examples: list[dict] = []
        for col in fix["columns"]:
            if col not in before.columns:
                continue
            if col not in after.columns:  # column dropped (d19 constant-drop)
                series = before[col]
                value = series.iloc[0] if len(series) else None
                examples.append({"column": col, "removed": True, "value": value})
                continue
            b = before[col].to_numpy()
            a = after[col].to_numpy()
            for row in range(min(len(b), len(a))):
                ov, nv = b[row], a[row]
                # NaN == NaN is False, so guard: both-null is not a change
                if _same(ov, nv):
                    continue
                examples.append({"column": col, "row": row, "old": ov, "new": nv})
                if len(examples) >= per_fix:
                    break
            if len(examples) >= per_fix:
                break
        out.append(
            {
                "disease": fix["disease"],
                "slug": fix["slug"],
                "columns": fix["columns"],
                "examples": examples,
            }
        )
    return out


def _same(a, b) -> bool:
    """True if two cell values are equal *or* both missing — so a NaN that stays
    NaN does not read as a change."""
    a_null, b_null = pd.isna(a), pd.isna(b)
    if a_null or b_null:
        return bool(a_null and b_null)
    return bool(a == b)


def styler_diff(before: pd.DataFrame, after: pd.DataFrame, max_rows: int = 200):
    """A pandas Styler over `after` with every changed cell highlighted green.

    Aligned on the columns the two frames share (a dropped column simply is not
    shown) and head-capped at `max_rows` — a Styler renders every cell, so the
    full frame is for eyeballing, not for a million rows.

    Needs jinja2 (every pandas Styler does); the keyless core does not depend on
    it, so this opt-in view raises pandas' own clear ImportError if it's absent.
    The inline before/after in the notebook card needs no such dependency.
    """
    common = [c for c in after.columns if c in before.columns]
    a = after[common].head(max_rows)
    b = before[common].head(max_rows)

    def _mark(_data):
        marks = a.copy()
        for col in common:
            bc, ac = b[col].to_numpy(), a[col].to_numpy()
            styles = [
                "background-color: #1e3a32" if not _same(bc[i], ac[i]) else ""
                for i in range(len(ac))
            ]
            marks[col] = styles
        return marks

    return a.style.apply(_mark, axis=None)


def clean(df: pd.DataFrame, policy: str = "auto") -> tuple[pd.DataFrame, CleanSummary]:
    """Deterministically clean a DataFrame. Returns (cleaned_frame, summary).

    The input is never mutated. Each auto-fix is verified — the detector must
    re-run clean or the fix is discarded and the finding moved to review.
    """
    findings = detect_all(df)["findings"]
    working = df.copy()
    applied: list[dict] = []
    needs_review: list[dict] = []

    def rank(f):
        d = f["disease"]
        return _ORDER.index(d) if d in _ORDER else len(_ORDER)

    auto = [f for f in findings if f["grade"] == "AUTO" and f["disease"] in FIXERS]
    for finding in sorted(auto, key=rank):
        disease, cols = finding["disease"], finding["columns"]
        try:
            candidate = FIXERS[disease](working, cols)
        except Exception as exc:  # noqa: BLE001 — a broken fix is reported, not raised
            needs_review.append(_slim(finding, reason=f"fixer error: {exc}"))
            continue
        if detect_one(candidate, disease, cols) is None:  # verified: signal gone
            working = candidate
            applied.append(_slim(finding))
        else:
            needs_review.append(_slim(finding, reason="fix did not clear verification"))

    reviewed = {(f["disease"], tuple(f["columns"])) for f in applied}
    for finding in findings:
        if (finding["disease"], tuple(finding["columns"])) not in reviewed and not any(
            r["disease"] == finding["disease"] and r["columns"] == finding["columns"]
            for r in needs_review
        ):
            needs_review.append(_slim(finding))

    return working, CleanSummary(df, working, applied, needs_review)
