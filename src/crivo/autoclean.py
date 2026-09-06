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
import re

import pandas as pd

from crivo.detect import (
    LEADING_NUMBER,
    MISSING_TOKENS,
    NUMERIC_WITH_UNIT,
    UNIT_LETTERS,
    ZERO_WIDTH,
    _date_families,
    _slot_ambiguity,
    _ws_tidy,
    detect_all,
    detect_one,
)

# Why every fixer below refuses instead of doing its best: a fixer that
# changes a column's dtype puts the column out of its own detector's reach
# (_text and _present return None for a non-text column), so detect_one sees
# no finding and clean() records the fix as verified. Absence of a finding is
# not proof of correctness. A fix that would lose or invent a value therefore
# leaves the frame untouched, verification honestly fails, and the finding
# reaches a human in needs_review.


def _real(series) -> "pd.Series":
    """Positional mask of the cells a fixer could actually lose something on.

    A null, a blank and a missing-data token are already absent: coercing one
    to NaN or NaT deletes nothing, and d04's whole job is to do exactly that.
    The loss gates below count only the rest. Counting the others refused a
    clean ISO column over a single stray 'N/A', and refused every external
    column carrying empty strings.

    Built out of numpy arrays rather than chained boolean Series because a
    frame on a non-unique index makes Series alignment ambiguous.
    """
    text = series.astype(str).str.strip()
    present = series.notna().to_numpy()
    blank = (text == "").to_numpy()
    sentinel = text.str.lower().isin(MISSING_TOKENS).to_numpy()
    return present & ~blank & ~sentinel


# A hyphen between two word characters belongs to the unit's name, as in Raha
# beers' '12.0 oz. Alumi-Tek'. Anywhere else it is arithmetic.
INNER_HYPHEN = re.compile(r"(?<=\w)-(?=\w)")
# What a REPAIR may strip off the end of a number, which is not the set a
# DETECTION may tolerate. NUMERIC_WITH_UNIT's suffix class carries the hyphen
# on purpose - a false accept there costs a report - but reused as a repair
# contract it reads the trailing minus of "1200-" as a unit and strips the
# sign off every value in the column.
FIXABLE_RESIDUE = re.compile(r"^[%a-zA-Z°µ²³/.\s]*$")
# Suffixes that multiply the number instead of naming it. Stripping the 'M'
# off "1.5M" states 1.5 where the value was 1,500,000, and no trace of the
# scale survives in the numeric column. A unit is a name for the number, a
# scale is part of it. 'm' is refused with the rest: metres and millions are
# the same token and one column cannot tell them apart, so the honest answer
# is a person, not a guess.
SCALE_SUFFIXES = frozenset({"k", "m", "b", "bn", "mn", "tn"})


def _fix_numbers(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    """numbers-as-strings: pull the number out of each cell and coerce the
    column to numeric.

    Refuses when the parse would drop a value that was really there, when any
    source string carries characters past the number that are not a unit or
    currency suffix, and when the suffix is a magnitude token. LEADING_NUMBER
    is anchored only at the start, so without those gates "approx 12" becomes
    NaN, the range "12-15" becomes 12.0, the trailing-minus negative "1200-"
    becomes +1200 and "1.5M" becomes 1.5 - all of them invisible to d01 once
    the column is numeric.
    """
    out = frame.copy()
    for c in cols:
        series = out[c]
        text = series.astype(str)
        pulled = text.str.extract(LEADING_NUMBER)
        parsed = pd.to_numeric(
            (pulled[0].fillna("") + pulled[1].fillna("")).str.replace(
                ",", "", regex=False
            ),
            errors="coerce",
        )
        real = _real(series)
        if bool((real & parsed.isna().to_numpy()).any()):
            continue  # the parse would delete values
        if not bool(text.str.match(NUMERIC_WITH_UNIT).to_numpy()[real].all()):
            continue  # residue past the number that is not a unit or symbol
        residue = text.str.replace(LEADING_NUMBER, "", regex=True).str.replace(
            INNER_HYPHEN, "", regex=True
        )
        if not bool(residue.str.match(FIXABLE_RESIDUE).to_numpy()[real].all()):
            continue  # a sign or other arithmetic the extraction did not consume
        token = residue.str.lower().str.extract(UNIT_LETTERS, expand=False)
        if bool(token[real].isin(SCALE_SUFFIXES).any()):
            continue  # the suffix multiplies the number, it does not name it
        out[c] = parsed
    return out


# family -> the separator its day/month/year slots use
_SLOT_SEPARATORS = {"slash": "/", "dash": "-", "dot": "."}
# families that spell the month out or carry no day/month slots at all, so no
# inferred format can silently swap the two. One strftime string does not
# cover them ("5 Jan 2020" and "05 January 2020" are the same family), so they
# are parsed without a format and held to the same null gate as the rest.
_UNSWAPPABLE_FAMILIES = frozenset({"iso-zoned", "day-month-name", "month-name-day"})
# A time of day carries no date, and pd.to_datetime supplies the missing one
# from the clock: "08:00:00" becomes today at 08:00. Nothing is deleted, so
# the loss gate stays quiet, and the column is datetime64 afterwards, so d02
# cannot see it again. The invented date also changes from run to run, which
# breaks the reproducibility the whole tool rests on. Inventing is worse than
# losing, so the family is refused outright. 'epoch' is here for the same
# reason it is in _date_families' own guard - ten digits alone are an id, not
# an instant - though d02 cannot reach it: _date_families deletes a lone
# epoch match and d02 skips any column with more than one family.
_UNPARSEABLE_FAMILIES = frozenset({"time", "epoch"})
_INFER = "infer"  # _date_format's "no format needed, and none can be built"
# How much evidence settles day-first vs month-first. A slot above 12 can only
# be a day, but ONE such value is an outlier, not a convention: on a column of
# month-first dates whose day slot never exceeds 12, a single stray "25/06"
# used to derive %d/%m and transpose every other value in the column, with no
# NaT to show for it. A genuinely day-first column puts ~61% of its days above
# 12, so a fifth of the column is a floor that reads the real thing and
# refuses the outlier.
_ORDER_SUPPORT = 0.2


def _date_format(values) -> str | None:
    """The one format the column's date family implies, `_INFER` when the
    family cannot confuse day with month, or None when the column has no
    single readable format and must be left alone."""
    families = [f for f, share in _date_families(values).items() if share >= 0.05]
    if len(families) != 1:
        return None
    family = families[0]
    if family in _UNPARSEABLE_FAMILIES:
        return None
    if family == "iso":
        return "ISO8601"
    if family == "compact":
        return "%Y%m%d"
    if family in _UNSWAPPABLE_FAMILIES:
        return _INFER
    if family not in _SLOT_SEPARATORS:
        return None
    sep = _SLOT_SEPARATORS[family]
    esc = re.escape(sep)
    slots = values.str.extract(rf"^(\d{{1,2}}){esc}(\d{{1,2}}){esc}(\d{{2,4}})$")
    left = pd.to_numeric(slots[0], errors="coerce")
    right = pd.to_numeric(slots[1], errors="coerce")
    widths = slots[2].dropna().str.len().unique()
    if len(widths) != 1:
        return None  # two-digit and four-digit years in one column
    year = "%Y" if int(widths[0]) == 4 else "%y"
    # Slots above 12 settle the order, but only with corroboration: the
    # deciding side needs a real share of the column and the other side needs
    # none at all. Neither side exceeding 12 is _slot_ambiguity's case, both
    # exceeding it means no order fits, and a lone dissenter means the column
    # does not agree with itself.
    slotted = int(left.notna().sum())
    floor = max(2, int(slotted * _ORDER_SUPPORT))
    left_high, right_high = int((left > 12).sum()), int((right > 12).sum())
    if left_high >= floor and right_high == 0:
        return f"%d{sep}%m{sep}{year}"
    if right_high >= floor and left_high == 0:
        return f"%m{sep}%d{sep}{year}"
    return None


def _fix_dates(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    """dates-as-strings: parse the column to datetime64 under an explicit
    format derived from its detected family.

    Refuses when `_slot_ambiguity` says day and month cannot be told apart,
    when no single format covers the column, and when the parse would produce
    a NaT where the input really had a date. A coerced NaT is a deleted date
    and a swapped slot is a wrong one, and d02 sees neither once the column is
    datetime64: pandas picks one format from the first value, so an unguarded
    coerce read "05-01-2020" as May 1 and turned half a day-first column
    into NaT while clean() recorded it verified.
    """
    out = frame.copy()
    for c in cols:
        series = out[c]
        text = series.dropna().astype(str).str.strip()
        text = text[text != ""]
        if len(text) == 0 or _slot_ambiguity(text):
            continue
        fmt = _date_format(text)
        if fmt is None:
            continue
        parsed = (
            pd.to_datetime(series, errors="coerce")
            if fmt == _INFER
            else pd.to_datetime(series, format=fmt, errors="coerce")
        )
        if bool((_real(series) & parsed.isna().to_numpy()).any()):
            continue  # the parse would delete values
        out[c] = parsed
    return out


def _fix_sentinels(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = frame.copy()
    for c in cols:
        low = out[c].astype(str).str.strip().str.lower()
        out.loc[low.isin(MISSING_TOKENS), c] = None
    return out


def _strings(series) -> pd.Series:
    """Mask of the cells that really are strings. An object column holds
    whatever the reader put there, and astype(str) over all of it would turn
    every int, float and bool into text. d06 and d07 stringify for their own
    checks, so neither would ever see that happen."""
    return series.map(lambda v: isinstance(v, str))


def _fix_whitespace(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = frame.copy()
    for c in cols:
        mask = _strings(out[c])
        if not bool(mask.any()):
            continue
        out.loc[mask, c] = _ws_tidy(out[c][mask])
    return out


def _fix_case_variants(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    out = frame.copy()
    for c in cols:
        mask = _strings(out[c])
        if not bool(mask.any()):
            continue
        tidy = out[c][mask].str.replace(r"\s+", " ", regex=True).str.strip()
        key = tidy.str.lower()
        # each normalised key -> its most frequent real spelling, so 'IT' is
        # preserved over 'it' rather than lowercased
        canon = {k: tidy[key == k].value_counts().index[0] for k in key.unique()}
        out.loc[mask, c] = key.map(canon).to_numpy()
    return out


def _drop_constant(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    """Drop the constant columns a d19 finding named, positionally.

    Refuses when a target name is not carried by exactly one column: dropping
    by name on a frame with two columns called "x" takes the informative twin
    with it, and the d19 re-check then finds no column of that name at all and
    reads the loss as proof the fix worked.
    """
    names = [str(c) for c in frame.columns]
    targets = {str(c) for c in cols}
    if any(names.count(t) != 1 for t in targets):
        return frame
    out = frame.iloc[:, [i for i, n in enumerate(names) if n not in targets]]
    if len(frame.columns) - len(out.columns) != len(targets):
        return frame
    return out


_TRUTHY = {"y", "yes", "true", "t", "1"}
_FALSY = {"n", "no", "false", "f", "0"}


def _fix_booleans(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    """boolean-chaos: one truth, many spellings — canonicalise to a real
    nullable boolean dtype. Unmappable strays stay untouched by leaving the
    whole column alone (a partial mapping would trade one chaos for two)."""
    out = frame.copy()
    for c in cols:
        series = out[c]
        folded = series.astype(str).str.strip().str.lower()
        known = folded.isin(_TRUTHY | _FALSY) | series.isna()
        if not bool(known.all()):
            continue
        mapped = folded.map(lambda v: v in _TRUTHY)
        mapped[series.isna()] = pd.NA
        out[c] = mapped.astype("boolean")
    return out


def _header_key(value) -> str:
    """One spelling for a header echo, whatever an earlier fixer did to it."""
    return " ".join(str(value).split()).casefold()


def _repeats_header(frame: pd.DataFrame, names: list) -> bool:
    """True when a data row spells out `names`. Mirrors _d18's own scan, same
    200-row window, so the fixer refuses the frames the detector calls
    header-damaged for that reason.

    Matched on a normalised key, not verbatim like _d18, because by the time
    this runs the row has been through four other fixers: _ORDER puts d18
    last, so d06 has already tidied the echo's whitespace and d07 has already
    recased it. Verbatim, such a row matches neither the damaged names nor the
    repaired ones, the rename goes ahead, d18 re-runs clean, and the finding
    is recorded verified with the junk row still in the table.
    """
    wanted = [_header_key(n) for n in names]
    scan = frame.head(200)
    return any(
        [_header_key(v) for v in scan.iloc[pos].tolist()] == wanted
        for pos in range(len(scan))
    )


def _fix_headers(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    """Repair damaged column NAMES: strip BOM/zero-width residue, collapse
    padding, replace "Unnamed: N" placeholders, dedupe collisions. Renames
    only, never drops, and untargeted healthy names always keep their claim.

    Header-repeat data ROWS are out of reach (row deletion is a judgement
    call), so a frame carrying one is refused outright and d18 goes to review
    with all of its evidence. Renaming instead would be worse than
    half-fixing: the row stops matching the repaired names, so d18's residual
    finding carries no column names (header_rows contributes none), the
    re-check's name comparison never matches it, and clean() records the whole
    finding verified while the junk row is still in the table. The proposed
    names are checked too, for the frame whose row starts matching only after
    the repair.
    """
    targeted = {str(c) for c in cols}
    positions = [pos for pos, name in enumerate(frame.columns) if str(name) in targeted]
    taken = {
        str(name) for pos, name in enumerate(frame.columns) if pos not in positions
    }
    old_names = [str(name) for name in frame.columns]
    new_names = list(old_names)
    for pos in positions:
        text = new_names[pos]
        for z in ZERO_WIDTH:
            text = text.replace(z, "")
        text = " ".join(text.split())
        if not text or text.startswith("Unnamed:"):
            text = f"column_{pos}"
        candidate, k = text, 1
        while candidate in taken:
            k += 1
            candidate = f"{text}_{k}"
        taken.add(candidate)
        new_names[pos] = candidate
    if _repeats_header(frame, old_names) or _repeats_header(frame, new_names):
        return frame
    out = frame.copy()
    out.columns = new_names
    return out


# disease -> deterministic fixer. Row-deleting diseases (9 dup-rows, 10
# near-dup) are deliberately absent: dropping a row is destructive and a
# judgement call, so it is reported, never auto-applied.
FIXERS = {
    1: _fix_numbers,
    2: _fix_dates,
    4: _fix_sentinels,
    6: _fix_whitespace,
    7: _fix_case_variants,
    18: _fix_headers,
    19: _drop_constant,
    23: _fix_booleans,
}
# apply order: clear sentinels and whitespace before coercing types; structural
# changes last (drops, then renames), so a value fix never runs against an
# already-mutated shape and a rename never orphans a later fixer's column list
_ORDER = [4, 6, 7, 1, 2, 23, 19, 18]


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
        from crivo import notebook as _notebook

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
        if fix["disease"] == 18:
            # header repair renames columns, so the old name is absent from
            # `after` — without this branch the d19 "removed" path below
            # would misreport every rename as a drop. Same column count:
            # positions are stable and the mapping is the positional diff.
            # Different count (a d19 drop ran in the same clean): pair the
            # vanished old names with the appeared new names in order —
            # renamed columns keep their relative order, dropped ones
            # appear on neither side of the pairing.
            before_names = [str(c) for c in before.columns]
            after_names = [str(c) for c in after.columns]
            if len(before_names) == len(after_names):
                pairs = [
                    (old, new)
                    for old, new in zip(before_names, after_names)
                    if old != new
                ]
            else:
                # names dropped by a d19 fix in the same clean are gone, not
                # renamed — exclude them or the pairing walks onto them
                dropped = {
                    str(c)
                    for other in applied
                    if other["disease"] == 19
                    for c in other["columns"]
                }
                after_set, before_set = set(after_names), set(before_names)
                olds = [
                    n for n in before_names if n not in after_set and n not in dropped
                ]
                news = [n for n in after_names if n not in before_set]
                pairs = list(zip(olds, news))
            examples = [
                {"column": old, "renamed": True, "new": new} for old, new in pairs
            ][:per_fix]
            out.append(
                {
                    "disease": fix["disease"],
                    "slug": fix["slug"],
                    "columns": fix["columns"],
                    "examples": examples,
                }
            )
            continue
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
