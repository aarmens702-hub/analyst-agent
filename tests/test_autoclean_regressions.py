"""Wave 1.5: what the last four packets broke in the fixers.

Two kinds of regression, and they pull in opposite directions.

The silent ones first: a magnitude spelling that was left out of the refusal
set by accident, so "1.5MM" became 1.5 and was recorded verified, and a d19
target that is not in the frame at all, which returned the untouched frame and
was likewise recorded verified.

Then the over-refusals P2 bought and never paid back. Each one declines input
crivo can repair without guessing, and a refused repair is a repair lost: the
survival rate cannot tell "correctly declined" from "stopped fixing what it
used to fix well".

Where a token really is two things, the refusal stays and says so - see
`test_number_fix_still_refuses_the_millimetre_million_collision`.
"""

import pandas as pd
import pytest

from crivo.autoclean import FIXERS, clean
from crivo.detect import detect_all, detect_one


def _reviewed(summary, disease, columns):
    return any(
        r["disease"] == disease and r["columns"] == columns
        for r in summary.needs_review
    )


# --- 15. magnitude spellings that were omitted by accident -------------------


def test_number_fix_refuses_the_finance_million_spelling():
    """'1.5MM' is 1,500,000 on every US finance desk. SCALE_SUFFIXES listed
    'mn' and 'bn' but not 'MM', so the fixer stripped it, wrote 1.5, and d01
    could no longer see the column at all: a million-fold error recorded as a
    verified fix."""
    values = ["1.5MM", "2.3MM", "0.8MM", "12.4MM", "7.1MM"] * 6

    cleaned, summary = clean(pd.DataFrame({"revenue": values}))

    assert cleaned["revenue"].tolist() == values, "the scale was stripped"
    assert not any(a["disease"] == 1 for a in summary.applied)
    assert _reviewed(summary, 1, ["revenue"]), summary.needs_review


def test_number_fix_refuses_the_single_letter_trillion():
    """Same hole, one letter wide: 'T' was missing beside 'tn'."""
    values = ["1.5T", "2.3T", "0.8T", "12.4T", "7.1T"] * 6

    cleaned, summary = clean(pd.DataFrame({"gdp": values}))

    assert cleaned["gdp"].tolist() == values, "the scale was stripped"
    assert not any(a["disease"] == 1 for a in summary.applied)


def test_number_fix_still_refuses_the_millimetre_million_collision():
    """The ambiguity kept on purpose, not closed. 'mm' is millimetres and the
    finance spelling for millions, and one column cannot tell them apart, so
    both readings refuse. A wrong magnitude is silent; a refusal is a line in
    needs_review that a person reads."""
    values = ["12 mm", "8 mm", "15 mm", "3 mm"] * 6

    cleaned, summary = clean(pd.DataFrame({"width": values}))

    assert cleaned["width"].tolist() == values
    assert not any(a["disease"] == 1 for a in summary.applied)
    assert _reviewed(summary, 1, ["width"]), summary.needs_review


# --- 9. units that merely begin with a magnitude letter ----------------------


def test_number_fix_still_refuses_a_rate_unit_that_begins_with_a_magnitude():
    """DELIBERATELY INVERTED from the wave 1.5 repair, which asserted "the
    slash proves this is a unit" and parsed '12 m/s' as 12 metres per second.
    The slash proves nothing: '4.5M/yr' is millions per year and wears exactly
    the same shape, so the rule that recovered this one stripped the scale off
    that one and recorded it verified. The two readings cannot be separated
    from the column, so this is the "mm" collision again and it takes the same
    answer - the refusal, and a line in needs_review a person reads.

    The cost is admitted rather than hidden: a genuine speed column goes to
    review. Item 9's recoverable half is the escape a magnitude can never
    wear, which `test_number_fix_repairs_a_unit_a_magnitude_can_never_wear`
    pins."""
    values = ["12 m/s", "3.5 m/s", "7 m/s", "21 m/s"] * 6

    cleaned, summary = clean(pd.DataFrame({"speed": values}))

    assert cleaned["speed"].tolist() == values
    assert not any(a["disease"] == 1 for a in summary.applied)
    assert _reviewed(summary, 1, ["speed"]), summary.needs_review


def test_number_fix_still_refuses_a_bitrate_that_begins_with_a_magnitude():
    """The same inversion one letter over. '40 b/s' is bits per second and
    '12B/day' is billions per day, and nothing in the column says which."""
    values = ["40 b/s", "1200 b/s", "9 b/s", "512 b/s"] * 6

    cleaned, summary = clean(pd.DataFrame({"rate": values}))

    assert cleaned["rate"].tolist() == values
    assert not any(a["disease"] == 1 for a in summary.applied)


# --- 10. one slot order, several values, and the 20% floor -------------------


def test_date_fix_refuses_the_order_a_fifth_of_the_column_does_not_support():
    """DELIBERATELY INVERTED from the wave 1.5 repair, which dropped the
    proportional floor to a flat count of two so this 27-value column would
    parse day-first. A count with no relation to column size says the same
    thing about two values in twenty as about two in two hundred: the same
    rule transposed 132 of 198 rows in a month-first column carrying two
    typos, silently, with zero NaT and recorded verified.

    Three in twenty-seven is a typo rate, not a convention, and "one order
    alone parses" does not separate them - a transposed typo makes the other
    order fail to parse too. So the floor stays and the ambiguity is named.
    This is the unclosed half of triage item 10; its other half, the report
    calling a refusal "fix did not clear verification", is a wording defect
    and is pinned below."""
    values = [f"{d:02d}/{m:02d}/2020" for d in (5, 7, 9, 11) for m in (1, 2, 3)]
    values += ["25/06/2020", "19/03/2020", "28/11/2020"]
    values += [f"{d:02d}/{m:02d}/2021" for d in (2, 4, 6) for m in (5, 6, 7, 8)]

    cleaned, summary = clean(pd.DataFrame({"d": values}))

    assert not any(a["disease"] == 2 for a in summary.applied)
    assert cleaned["d"].tolist() == values, "the column was parsed anyway"
    assert _reviewed(summary, 2, ["d"]), summary.needs_review


# --- 22 (sibling). iso-zoned columns with mixed offsets ----------------------


def test_date_fix_parses_an_iso_zoned_column_whose_offsets_differ():
    """_UNSWAPPABLE_FAMILIES sent iso-zoned to a bare pd.to_datetime, which
    RAISES on mixed UTC offsets instead of returning, so the null gate the
    comment promises never ran and the fixer died where it meant to refuse.
    datetime64 carries one offset per column, so the instants are kept in UTC
    and the per-row offset is not."""
    values = [
        "2020-01-01T08:00:00+01:00",
        "2020-01-02T09:00:00+02:00",
        "2020-01-03T10:00:00Z",
    ] * 8

    cleaned, summary = clean(pd.DataFrame({"ts": values}))

    assert 2 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert pd.api.types.is_datetime64_any_dtype(cleaned["ts"])
    assert int(cleaned["ts"].isna().sum()) == 0, "instants were deleted"
    assert cleaned["ts"].iloc[0] == pd.Timestamp("2020-01-01T07:00:00Z")
    assert cleaned["ts"].iloc[1] == pd.Timestamp("2020-01-02T07:00:00Z")


# --- 14. a d19 target that is not in the frame -------------------------------


def test_constant_drop_raises_when_its_target_is_not_in_the_frame():
    """The refusal arm fired on count != 1, so an ABSENT name returned the
    frame untouched and looked exactly like a considered refusal. A refusal
    that cannot be told from a no-op is not a refusal."""
    frame = pd.DataFrame({"amount": [float(i) for i in range(20)], "k": ["x"] * 20})

    with pytest.raises(KeyError) as err:
        FIXERS[19](frame, ["ghost"])

    assert "ghost" in str(err.value)


def test_an_absent_d19_target_is_never_recorded_as_a_verified_fix():
    """Why the raise, end to end. d19 is COLUMN_CHANGING, so detect_one does
    not treat a vanished target as a failure, and an untouched frame carrying
    no other constant column clears the re-check: clean() would record the
    informationless column as fixed. Raising routes it to the fixer-error path
    instead, which is what the loop's revert also keys on."""
    frame = pd.DataFrame({"amount": [float(i) for i in range(20)], "k": ["x"] * 20})

    try:
        candidate = FIXERS[19](frame, ["ghost"])
    except KeyError:
        return  # the fixer-error path: reported, never recorded verified

    assert detect_one(candidate, 19, ["ghost"]) is not None, (
        "an untouched frame cleared the d19 re-check: a verified no-op"
    )


def test_constant_drop_still_removes_the_column_it_was_asked_to_drop():
    """The recovery side: an ordinary d19 target still drops."""
    frame = pd.DataFrame({"amount": [float(i) for i in range(20)], "const": ["k"] * 20})

    cleaned, summary = clean(frame)

    assert list(cleaned.columns) == ["amount"]
    assert 19 in {a["disease"] for a in summary.applied}


# --- 11. a row _d18 never counted as a header row ----------------------------


def test_header_fix_repairs_padding_over_a_row_d18_never_reported():
    """_repeats_header matches case-folded and whitespace-collapsed while _d18
    counts header rows verbatim. Here no row ever equalled the names, so the
    finding is padding and nothing else, and no earlier fixer touched row 0:
    'AMOUNT' is the only cell of its key in its column, so d07 had nothing to
    fold it onto. Refusing the rename declines a repair over a row the finding
    never reported."""
    frame = pd.DataFrame(
        {
            "amount ": ["AMOUNT"] + [str(v) for v in range(16)],
            "region": ["REGION"] + ["east", "west"] * 8,
        }
    )

    cleaned, summary = clean(frame)

    assert 18 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert list(cleaned.columns) == ["amount", "region"], "the padding survived"
    assert cleaned.iloc[0].tolist() == ["AMOUNT", "REGION"], "a row was deleted"


def test_header_fix_still_refuses_a_row_an_earlier_fixer_folded_onto_the_name():
    """The line the recovery must not cross, kept from the P2 packet's own
    case: d07 folded the echo cell onto the column's most frequent spelling of
    the same key, so the cell no longer matches the name verbatim but the row
    IS the header row d18 reported. The give-away is that the spelling occurs
    elsewhere in the column, which is the only way d07 could have written it."""
    frame = pd.DataFrame(
        {
            "  Region ": ["  Region "] + ["REGION"] * 8 + ["east", "west"] * 4,
            "amount": ["amount"] + [str(v) for v in range(16)],
        }
    )

    cleaned, summary = clean(frame)

    assert list(cleaned.columns) == ["  Region ", "amount"], "renamed anyway"
    assert not any(a["disease"] == 18 for a in summary.applied)
    assert cleaned.iloc[0].tolist() == ["REGION", "amount"], "the junk row is here"


# --- 9 (over-correction). a magnitude wearing a rate slash -------------------


def test_number_fix_refuses_a_magnitude_that_wears_a_rate_slash():
    """The item 9 repair let any residue carrying a slash skip the magnitude
    gate entirely, on the reasoning that a rate slash "spells a unit and cannot
    be a magnitude". "4.5M/yr" refutes that in one line: millions per year is
    the ordinary way a finance column writes a run rate, and stripping it
    states 4.5 where the value was 4,500,000 - item 15's exact corruption,
    reopened one character to the right and recorded verified."""
    values = ["4.5M/yr", "3.2M/yr", "1.1M/yr", "8.7M/yr"] * 6

    cleaned, summary = clean(pd.DataFrame({"run_rate": values}))

    assert cleaned["run_rate"].tolist() == values, "the scale was stripped"
    assert not any(a["disease"] == 1 for a in summary.applied)


def test_number_fix_refuses_the_millimetre_million_collision_under_a_slash_too():
    """The "mm" ambiguity does not stop being ambiguous because a slash
    follows it. "1.5 mm/yr" is a millimetres-per-year erosion rate and
    "1.5 MM/yr" is millions per year, and they are the same shape, so the
    refusal has to cover both rather than quietly resolve them to the unit
    reading."""
    values = ["1.5 mm/yr", "2.5 mm/yr", "0.8 mm/yr", "3.1 mm/yr"] * 6

    cleaned, summary = clean(pd.DataFrame({"erosion": values}))

    assert cleaned["erosion"].tolist() == values, "the scale was stripped"
    assert not any(a["disease"] == 1 for a in summary.applied)


def test_number_fix_repairs_a_unit_a_magnitude_can_never_wear():
    """The half of item 9 that IS recoverable, and the reason the gate reads
    more than the first letter run. No magnitude is ever written "1.5M²", so a
    residue carrying a superscript, a degree sign or a per-cent sign is a unit
    however it begins."""
    values = ["12 m²", "3.5 m²", "7 m²", "21 m²"] * 6

    cleaned, summary = clean(pd.DataFrame({"area": values}))

    assert 1 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert cleaned["area"].iloc[0] == 12.0


# --- 10 (over-correction). two outliers against a month-first column ---------


def test_date_fix_refuses_two_day_first_outliers_in_a_month_first_column():
    """_ORDER_SUPPORT went from a fifth of the column to a flat count of two,
    with no relation to column size, so a month-first column whose day slot
    never exceeds 12 plus two stray day-first rows derived %d/%m/%Y and
    transposed every other row silently, with zero NaT to show for it. Two
    typos are not a convention. The column below is the same shape at 200 rows
    that the packet's own test used at 27."""
    values = [f"{m:02d}/{d:02d}/2020" for m in range(1, 13) for d in range(1, 12)]
    values = values[:198] + ["06/25/2020", "07/26/2020"]

    cleaned, summary = clean(pd.DataFrame({"d": values}))

    assert not any(a["disease"] == 2 for a in summary.applied), (
        "a month-first column was transposed on two outliers"
    )
    assert cleaned["d"].tolist() == values


# --- 11 (over-refusal survived). a banner row that merely repeats ------------


def test_header_fix_repairs_padding_over_a_repeated_banner_row():
    """The item 11 repair still refuses when the row's spelling merely occurs
    more than once in its own column, on the theory that only d07 could have
    written it there. A repeated all-caps banner is exactly the spelling d07
    leaves alone, and the d18 finding on this frame reports header_rows 0: no
    row ever equalled the names, so there is no header row for the rename to
    hide from the re-check."""
    frame = pd.DataFrame(
        {
            "amount ": ["AMOUNT"] * 6 + [str(v) for v in range(12)],
            "region": ["REGION"] * 6 + ["east", "west"] * 6,
        }
    )
    finding = next(f for f in detect_all(frame)["findings"] if f["disease"] == 18)
    assert finding["stats"]["header_rows"] == 0, "the premise of this test"

    cleaned, summary = clean(frame)

    assert 18 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert list(cleaned.columns) == ["amount", "region"], "the padding survived"


def test_number_fix_refuses_the_magnitudes_spelled_as_words():
    """The item 15 comment says every spelling is listed on purpose "because
    omitting one by accident is exactly how 1.5MM became 1.5". The word
    spellings were never listed, so "1.5 million" still became 1.5 by exactly
    that accident, under a comment asserting the hole was closed. These carry
    no unit collision at all - nothing is measured in millions - so the
    refusal costs nothing that "mm" costs."""
    for spelling in ("million", "mil", "billion", "thousand", "trillion"):
        values = [f"{n}.5 {spelling}" for n in range(1, 5)] * 6

        cleaned, summary = clean(pd.DataFrame({"revenue": values}))

        assert cleaned["revenue"].tolist() == values, f"{spelling} was stripped"
        assert not any(a["disease"] == 1 for a in summary.applied), spelling
