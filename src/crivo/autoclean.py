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
# Suffixes the fixer will not strip, because they may multiply the number
# rather than name it. Stripping the 'M' off "1.5M" states 1.5 where the value
# was 1,500,000, and no trace of the scale survives in the numeric column. A
# unit is a name for the number, a scale is part of it.
#
# Every spelling is listed on purpose, the doubled and single-letter ones
# included, because omitting one by accident is exactly how "1.5MM" became 1.5:
#   k       thousands; also kelvin
#   m       millions; also metres, also molar, and thousands in the older
#           accounting convention that spells millions "MM"
#   mm      millions on a US finance desk; also millimetres
#   b       billions; also bytes, also bits
#   t       trillions; also tonnes
#   bn mn tn    magnitudes and nothing else
#   thousand million mil billion trillion    the word spellings, which carry
#           no unit collision at all: nothing is measured in millions
# The ambiguous ones are refused rather than guessed, and crivo picks no side
# on "mm": read as millimetres the strip is right, read as millions it is a
# million-fold error that nothing downstream can see. A wrong magnitude is
# silent; a refusal is a line in needs_review that a person reads. The cost is
# real and is paid knowingly - a genuine millimetres column goes to review.
SCALE_SUFFIXES = frozenset(
    {
        "k",
        "m",
        "mm",
        "b",
        "t",
        "bn",
        "mn",
        "tn",
        "thousand",
        "million",
        "mil",
        "billion",
        "trillion",
    }
)
# The residue shapes a magnitude can still be wearing, read whole rather than
# by its first letter run. Letters, spacing, a full stop and a rate SLASH are
# all in here, because a magnitude wears every one of them: "4.5M/yr" is a run
# rate on any finance desk, and so is "1.5MM/yr". Only a character a magnitude
# is never written with lets the residue out of the gate below - a degree
# sign, a micro sign, a superscript exponent, a per-cent sign - because
# nothing is spelled "1.5M²".
#
# The slash was briefly treated as proof of a unit, on the reading that "12
# m/s" is metres per second. It is, and "4.5M/yr" is millions per year, and
# the two are the same shape: that reading stripped the scale off every rate a
# magnitude wears and recorded it verified. So the slash proves nothing and
# the collision refuses, which is the same answer "mm" gets above and for the
# same reason.
#
# What that costs, stated rather than hidden: a genuine "12 m/s" or "40 b/s"
# column goes to review, and so does "1.5 MM USD" beside "120 mm Hg". Every
# input this gate refuses is one whose first letter run IS a magnitude token,
# so every one of them is ambiguous by construction. The recoverable half of
# that finding is the exponent/degree/micro escape above, which is real and is
# not ambiguous.
MAGNITUDE_SHAPE = re.compile(r"^[a-z\s./]*$")


def _fix_numbers(frame: pd.DataFrame, cols: list) -> pd.DataFrame:
    """numbers-as-strings: pull the number out of each cell and coerce the
    column to numeric.

    Refuses when the parse would drop a value that was really there, when any
    source string carries characters past the number that are not a unit or
    currency suffix, and when the residue could be read as a magnitude, which
    is when it is a bare word whose first letter run is in SCALE_SUFFIXES.
    LEADING_NUMBER is anchored only at the start, so without those gates
    "approx 12" becomes NaN, the range "12-15" becomes 12.0, the trailing-minus
    negative "1200-" becomes +1200 and "1.5M" becomes 1.5 - all of them
    invisible to d01 once the column is numeric.

    The magnitude gate reads the whole residue, not just its first letter run,
    and what reading it whole buys is one escape: a residue carrying a degree
    sign, a micro sign, a superscript or a per-cent sign is a unit, because no
    magnitude is ever written that way. A rate slash is NOT such a character -
    "4.5M/yr" and "12 m/s" wear the same shape - so the gate holds across it
    and both refuse. Nor does it tell a magnitude followed by a word from a
    two-word unit ("1.5 MM USD" against "120 mm Hg"); both refuse there too.
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
        lowered = residue.str.lower()
        token = lowered.str.extract(UNIT_LETTERS, expand=False)
        scaled = (
            lowered.str.match(MAGNITUDE_SHAPE).to_numpy()
            & token.isin(SCALE_SUFFIXES).to_numpy()
        )
        if bool(scaled[real].any()):
            continue  # the suffix may multiply the number, not name it
        out[c] = parsed
    return out


# family -> the separator its day/month/year slots use
_SLOT_SEPARATORS = {"slash": "/", "dash": "-", "dot": "."}
# families that spell the month out or carry no day/month slots at all, so no
# inferred format can silently swap the two. One strftime string does not
# cover them ("5 Jan 2020" and "05 January 2020" are the same family), so they
# are parsed without a format and held to the same null gate as the rest.
_UNSWAPPABLE_FAMILIES = frozenset({"day-month-name", "month-name-day"})
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
# iso-zoned is unswappable too, but it cannot go through the bare parse: a
# column mixing "+01:00" with "Z" makes pd.to_datetime RAISE rather than
# return, errors="coerce" and all, so the null gate below never ran and the
# fixer died where it meant to either repair or refuse. utc=True is what makes
# the parse return, and it is a real (small) loss stated plainly: datetime64
# holds one offset for a whole column, so the instants survive exactly and the
# per-row offset does not.
_INFER_UTC = "infer-utc"
# How much evidence settles day-first vs month-first. A slot above 12 can only
# be a day, but ONE such value is an outlier, not a convention: on a column of
# month-first dates whose day slot never exceeds 12, a single stray "25/06"
# used to derive %d/%m and transpose every other value in the column, with no
# NaT to show for it. A genuinely day-first column puts ~61% of its days above
# 12, so a fifth of the column is a floor that reads the real thing and
# refuses the outlier.
#
# This was briefly a flat count of two, on the reading that the other slot
# never exceeding 12 is the corroboration that carries the weight and a second
# value agreeing separates a convention from a typo. It does not, and the
# counter-example is the shape the floor exists for: a MONTH-first column
# whose day slot happens never to exceed 12, plus two transposed rows. Both
# readings then leave the second slot clean, exactly one order parses without
# a NaT, and the flat count derived %d/%m and silently transposed 12 of 18,
# 40 of 58 and 132 of 198 rows at n=20/60/200. A count with no relation to
# column size says the same thing about two values in twenty as about two in
# two hundred, and those are not the same evidence.
#
# So the ambiguity is named rather than resolved: below the floor, "a
# convention this column follows" and "a handful of transposed typos" are the
# same picture, and crivo takes the refusal. The cost is real and is the
# unclosed half of the over-refusal this floor was reported for - a column
# where one order alone parses can still go to review, because a typo makes
# the other order fail to parse too, and nothing here separates the two.
_ORDER_SUPPORT = 0.2


def _date_format(values) -> str | None:
    """The one format the column's date family implies, `_INFER` (or
    `_INFER_UTC` for the zoned family) when the family cannot confuse day with
    month, or None when the column has no single readable format and must be
    left alone."""
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
    if family == "iso-zoned":
        return _INFER_UTC
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
    # exceeding it means no order fits, and a handful of dissenters means the
    # column does not agree with itself.
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

    A zoned column is normalised to UTC (`_INFER_UTC`): every instant is
    preserved, the per-row offset is not, and that is the only shape a
    datetime64 column has for a column whose offsets differ.
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
        if fmt == _INFER_UTC:
            parsed = pd.to_datetime(series, errors="coerce", utc=True)
        elif fmt == _INFER:
            parsed = pd.to_datetime(series, errors="coerce")
        else:
            parsed = pd.to_datetime(series, format=fmt, errors="coerce")
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

    Refuses - returns the frame - when a target name is carried by MORE than
    one column: dropping by name on a frame with two columns called "x" takes
    the informative twin with it, and the d19 re-check then finds no column of
    that name at all and reads the loss as proof the fix worked.

    Raises when a target name is carried by NO column. The refusal arm used to
    cover that case too, and returning the frame there is indistinguishable
    from a refusal that was considered: d19 is COLUMN_CHANGING, so detect_one
    does not fail an absent target, the untouched frame clears the re-check,
    and clean() records an informationless column as a verified d19 fix. A
    raise reaches clean()'s fixer-error path and the loop's revert, which is
    the honest answer - the fixer was asked for something it cannot do.
    """
    names = [str(c) for c in frame.columns]
    targets = {str(c) for c in cols}
    missing = sorted(t for t in targets if names.count(t) == 0)
    if missing:
        raise KeyError(f"d19 target column(s) not in the frame: {missing}")
    if any(names.count(t) > 1 for t in targets):
        return frame
    out = frame.iloc[:, [i for i, n in enumerate(names) if n not in targets]]
    dropped = len(frame.columns) - len(out.columns)
    if dropped != len(targets):
        # unreachable while every target is carried exactly once, which the two
        # arms above now guarantee. It raises rather than returning the frame
        # because that is the same silent no-op this fixer was just fixed for.
        raise RuntimeError(f"d19 dropped {dropped} columns for {len(targets)} targets")
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


def _echoes_name(frame: pd.DataFrame, pos: int, cell: str, name: str) -> bool:
    """True when `cell` is a spelling of `name` that _d18 counted as a header
    row, or one an earlier fixer could have written over the row _d18 counted.

    _d18 compares verbatim, and _ORDER runs d18 last, so by the time the header
    fixer looks, the echo row has been through d06 and d07. There are exactly
    two ways they can have rewritten it, and both are checkable here:

    - d06 tidied its whitespace, which gives `_ws_tidy(name)` and nothing else.
    - d07 folded it onto the column's most frequent spelling of the same key,
      which by definition is a spelling that occurs elsewhere in the column.

    A cell that is neither is the cell _d18 itself read, verbatim, and did not
    count as a header row. Refusing the rename over it declines a repair for a
    row the finding never reported, which is what the case-folded match did.
    """
    if cell == name:
        return True  # what _d18 counts
    if _header_key(cell) != _header_key(name):
        return False
    if cell == _ws_tidy(pd.Series([name], dtype=object)).iloc[0]:
        return True  # d06 could have written this
    column = frame.iloc[:, pos].astype(str)
    return int((column == cell).sum()) > 1  # d07 folded a group onto this cell


def _repeats_header(frame: pd.DataFrame, names: list, widened: bool = True) -> bool:
    """True when a data row spells out `names`. Mirrors _d18's own scan, same
    200-row window, so the fixer refuses the frames the detector calls
    header-damaged for that reason. Renaming past a real echo row makes it stop
    matching, so d18's residual finding carries no column names, the re-check's
    name comparison never matches it, and clean() records the finding verified
    with the junk row still in the table.

    `widened` picks which comparison. Verbatim is _d18's own and is the whole
    of the guard when the caller knows the finding counted NO header rows:
    a row _d18 never counted cannot be hidden from the re-check by a rename,
    because the re-check runs the same verbatim scan and cannot see it either
    way. The widened comparison (`_echoes_name`) is for the caller that does
    not know, and for the finding that did count one, where d06 and d07 have
    since rewritten the echo row out of verbatim reach.
    """
    scan = frame.head(200)
    for pos in range(len(scan)):
        row = [str(v) for v in scan.iloc[pos].tolist()]
        pairs = list(zip(row, names, strict=True))
        if widened:
            if all(
                _echoes_name(frame, i, cell, name)
                for i, (cell, name) in enumerate(pairs)
            ):
                return True
        elif all(cell == name for cell, name in pairs):
            return True
    return False


def _fix_headers(
    frame: pd.DataFrame, cols: list, header_rows: int | None = None
) -> pd.DataFrame:
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

    "Carrying one" is `_repeats_header`'s definition, and `header_rows` - the
    count from the finding's own stats, when the caller has it - decides which
    one. At 0 the detector counted no header row on this frame, so there is
    nothing a rename could hide from the re-check and the comparison is
    _d18's own verbatim one: the padding repair goes ahead over a banner row
    that merely reads like the header, which is the case a case-folded match
    and then a "the spelling repeats in its column" match each refused.

    Above 0, and whenever the caller does not pass it at all, the widened
    comparison is used instead, because d06 and d07 run before this fixer and
    may have rewritten the counted echo row out of verbatim reach. The
    unknown case takes the refusing side on purpose: loop.py's generated
    `FIXERS[18](df, cols)` carries no stats, so the agent lane keeps the wider
    guard and the narrower recovery is clean()'s alone.
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
    widened = header_rows is None or header_rows > 0
    if _repeats_header(frame, old_names, widened) or _repeats_header(
        frame, new_names, widened
    ):
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
        # d18 alone reads a second argument: the header-row count the detector
        # actually recorded. Without it the header fixer has to guess whether a
        # row that reads like the header is the one the finding reported, and
        # guessing wide refused repairs over rows d18 never counted. The
        # finding is right here, so it is passed rather than guessed at.
        extra = (
            {"header_rows": int(finding.get("stats", {}).get("header_rows", 0))}
            if disease == 18
            else {}
        )
        try:
            candidate = FIXERS[disease](working, cols, **extra)
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
