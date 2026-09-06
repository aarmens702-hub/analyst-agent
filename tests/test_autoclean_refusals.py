"""The fixers refuse rather than corrupt (review packet: verification bypass).

Every fixer here changes a column past the point where its own detector can
look at it. `_text` and `_present` return None for a non-text column, so once
`_fix_numbers` or `_fix_dates` has coerced the dtype, d01 and d02 find nothing
and `clean` records the fix VERIFIED. Deleting the evidence must never read as
proof, so each fixer leaves the frame untouched when the repair would be lossy
or ambiguous: verification then honestly fails and the finding reaches a human
in `needs_review`.

Each input below is the one the reviewer confirmed corrupting.
"""

import pandas as pd

import crivo as aa
from crivo.autoclean import clean


def _reviewed(summary, disease, columns):
    return any(
        r["disease"] == disease and r["columns"] == columns
        for r in summary.needs_review
    )


# --- 1. numbers-as-strings --------------------------------------------------


def test_number_fix_refuses_when_the_parse_would_delete_or_truncate_values():
    """LEADING_NUMBER is anchored only at the start, so the old fixer turned
    every "approx 12" into NaN and every "12-15" into 12.0, then reported the
    column applied and verified because d01 cannot read a numeric column."""
    frame = pd.DataFrame(
        {
            "amount": ["$1,200"] * 6
            + ["1,500.50"] * 6
            + ["approx 12"] * 4
            + ["12-15"] * 4
        }
    )

    cleaned, summary = clean(frame)

    assert cleaned["amount"].tolist() == frame["amount"].tolist(), "column mangled"
    assert int(cleaned["amount"].notna().sum()) == 20, "non-null values were deleted"
    assert not any(a["disease"] == 1 for a in summary.applied)
    assert _reviewed(summary, 1, ["amount"]), summary.needs_review


def test_number_fix_still_repairs_a_column_it_can_parse_whole():
    """The refusal is scoped to lossy parses: currency and thousands separators
    with nothing trailing them still convert."""
    frame = pd.DataFrame({"amount": ["$1,200", "$3,400.50", "$15", "$980"] * 5})

    cleaned, summary = clean(frame)

    assert pd.api.types.is_numeric_dtype(cleaned["amount"])
    assert cleaned["amount"].iloc[0] == 1200.0
    assert 1 in {a["disease"] for a in summary.applied}


def test_number_fix_keeps_a_recognised_unit_suffix_parsable():
    """A trailing unit is what NUMERIC_WITH_UNIT exists to allow, so "12 kg"
    is a number wearing a unit, not residue, and still converts."""
    frame = pd.DataFrame({"weight": ["12 kg", "1,500 kg", "7 kg", "230 kg"] * 5})

    cleaned, summary = clean(frame)

    assert 1 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert cleaned["weight"].iloc[1] == 1500.0


# --- 2. dates-as-strings ----------------------------------------------------


def test_date_fix_reads_day_first_dates_day_first():
    """pandas infers one format from the first value, so an unguarded coerce
    read "05-01-2020" as May 1 and NaT-ed the twelve values whose day slot
    exceeds 12, all recorded verified."""
    frame = pd.DataFrame(
        {"d": ["05-01-2020", "25-01-2020", "03-02-2020", "17-02-2020"] * 6}
    )

    cleaned, summary = clean(frame)

    assert 2 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert int(cleaned["d"].isna().sum()) == 0, "dates were deleted"
    assert cleaned["d"].iloc[0] == pd.Timestamp("2020-01-05"), "day and month swapped"
    assert cleaned["d"].iloc[1] == pd.Timestamp("2020-01-25")


def test_date_fix_refuses_a_slot_ambiguous_column():
    """Every slot <= 12: D/M and M/D both parse with zero NaT, so there is no
    signal that could tell the two readings apart. Guessing month-first is a
    coin flip recorded as a verified fix."""
    values = ["03/04/2020", "05/06/2020", "07/08/2020", "09/10/2020"] * 6
    frame = pd.DataFrame({"d": values})

    cleaned, summary = clean(frame)

    assert cleaned["d"].tolist() == values, "an ambiguous column was guessed at"
    assert not any(a["disease"] == 2 for a in summary.applied)
    assert _reviewed(summary, 2, ["d"]), summary.needs_review


def test_date_fix_refuses_when_the_parse_would_add_a_nat():
    """A value the format cannot read becomes NaT, which is a deleted date the
    d02 re-check cannot see once the column is datetime64."""
    values = (["2020-01-05", "2020-02-06", "2020-03-07"] * 8) + ["not a date"] * 2
    frame = pd.DataFrame({"d": values})

    cleaned, summary = clean(frame)

    assert cleaned["d"].tolist() == values
    assert not any(a["disease"] == 2 for a in summary.applied)


def test_date_fix_still_parses_an_unambiguous_iso_column():
    frame = pd.DataFrame({"d": ["2020-01-05", "2020-02-06", "2020-03-07"] * 8})

    cleaned, summary = clean(frame)

    assert 2 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert pd.api.types.is_datetime64_any_dtype(cleaned["d"])
    assert cleaned["d"].iloc[0] == pd.Timestamp("2020-01-05")


# --- 19. constant columns ---------------------------------------------------


def test_constant_drop_refuses_a_duplicated_column_name():
    """`drop(columns=...)` works by name, so on ["x", "x", "y"] it removed the
    informative twin as well; d19 then found no column called "x" at all and
    read that absence as the fix working."""
    frame = pd.concat(
        [
            pd.Series(["k"] * 20, name="x"),
            pd.Series(range(20), name="x"),
            pd.Series(list("ab") * 10, name="y"),
        ],
        axis=1,
    )

    cleaned, summary = clean(frame)

    assert len(cleaned.columns) == 3, f"a column was dropped: {list(cleaned.columns)}"
    assert cleaned.iloc[:, 1].tolist() == list(range(20)), "the informative twin died"
    assert not any(a["disease"] == 19 for a in summary.applied)
    assert _reviewed(summary, 19, ["x"]), summary.needs_review


def test_constant_drop_still_removes_an_unambiguous_constant_column():
    frame = pd.DataFrame({"amount": [float(i) for i in range(20)], "const": ["k"] * 20})

    cleaned, summary = clean(frame)

    assert list(cleaned.columns) == ["amount"]
    assert 19 in {a["disease"] for a in summary.applied}


# --- 6/7. whitespace and case variants --------------------------------------


def test_whitespace_fix_leaves_non_string_cells_at_their_own_type():
    """astype(str) over every non-null cell turned each int, float and bool in
    an object column into text. d06 stringifies for its own check, so it could
    never see it happen."""
    values = ["  a  ", 1, 2.5, True, "b  ", "  c", "two  spaces", 7] * 3
    frame = pd.DataFrame({"m": values})

    cleaned, summary = clean(frame)

    assert 6 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert cleaned["m"].iloc[:8].tolist() == [
        "a",
        1,
        2.5,
        True,
        "b",
        "c",
        "two spaces",
        7,
    ]
    assert [type(v) for v in cleaned["m"].iloc[1:4]] == [int, float, bool]


def test_case_variant_fix_leaves_non_string_cells_at_their_own_type():
    strings = ["IT"] * 6 + ["it", "It"] + ["Sales"] * 6 + ["sales", "SALES"]
    frame = pd.DataFrame({"m": strings + [1, 2.5, True, 7] * 4})

    cleaned, summary = clean(frame)

    assert 7 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert set(cleaned["m"].iloc[:16]) == {"IT", "Sales"}
    assert [type(v) for v in cleaned["m"].iloc[16:20]] == [int, float, bool, int]


# --- 18. header damage ------------------------------------------------------


def test_header_fix_refuses_a_frame_whose_rows_repeat_the_header():
    """The rename makes the junk row stop matching, so the residual d18
    finding carries no column names, the re-check's name comparison misses it,
    and the whole finding is recorded verified with the row still in the
    table. The fixer refuses instead, exactly as its docstring promises."""
    frame = pd.DataFrame(
        {
            "Unnamed: 0": ["Unnamed: 0"] + ["a", "b"] * 8,
            "region": ["region"] + ["e", "w"] * 8,
        }
    )

    cleaned, summary = clean(frame)

    assert list(cleaned.columns) == ["Unnamed: 0", "region"], "renamed anyway"
    assert not any(a["disease"] == 18 for a in summary.applied)
    assert _reviewed(summary, 18, ["Unnamed: 0"]), summary.needs_review
    residual = [f for f in aa.diagnose(cleaned).findings if f["disease"] == 18]
    assert residual and "repeat the header" in residual[0]["evidence"]


def test_header_fix_refuses_when_the_repaired_names_would_match_a_data_row():
    """The same hole from the other side: the row starts matching only after
    the padding is stripped, and d18 goes quiet on names again."""
    frame = pd.DataFrame(
        {
            "amount ": ["amount"] + ["a", "b"] * 8,
            "region": ["region"] + ["e", "w"] * 8,
        }
    )

    cleaned, summary = clean(frame)

    assert list(cleaned.columns) == ["amount ", "region"], "renamed anyway"
    assert not any(a["disease"] == 18 for a in summary.applied)


# --- integration pass: the gaps the attackers found still open ---------------


def test_number_fix_refuses_a_trailing_minus_negative():
    """NUMERIC_WITH_UNIT's suffix class carries the hyphen, so the residue gate
    read the trailing minus of "1200-" as a unit and let the fixer strip it.
    Every value came out positive, the column was numeric, and d01 could no
    longer see that the sign of the whole column had been inverted."""
    values = ["1200-", "340-", "980-", "55-", "7-"] * 6
    frame = pd.DataFrame({"amount": values})

    cleaned, summary = clean(frame)

    assert cleaned["amount"].tolist() == values, "the sign was stripped"
    assert not any(a["disease"] == 1 for a in summary.applied)
    assert _reviewed(summary, 1, ["amount"]), summary.needs_review


def test_number_fix_refuses_a_magnitude_suffix():
    """A unit names the number, a scale is part of it. Stripping the 'M' off
    "1.5M" states 1.5 where the value was 1,500,000 and leaves no trace of the
    factor anywhere in the numeric column."""
    values = ["1.5M", "2.3M", "0.8M", "12.4M", "7.1M"] * 6
    frame = pd.DataFrame({"revenue": values})

    cleaned, summary = clean(frame)

    assert cleaned["revenue"].tolist() == values, "the scale was stripped"
    assert not any(a["disease"] == 1 for a in summary.applied)
    assert _reviewed(summary, 1, ["revenue"]), summary.needs_review


def test_number_fix_still_repairs_a_column_whose_gaps_are_blanks():
    """The other side of the loss gate, and the line it must not cross. An
    empty cell is already absent: coercing it to NaN deletes nothing, so
    counting it as a deleted value refused every real column carrying blanks
    (Raha beers' abv, 62 of them) for no safety gained."""
    values = ["$1,200", "", "$3,400.50", "N/A", "$980"] * 6
    frame = pd.DataFrame({"amount": values})

    cleaned, summary = clean(frame)

    assert 1 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert cleaned["amount"].iloc[0] == 1200.0
    assert cleaned["amount"].isna().tolist()[:5] == [False, True, False, True, False]


def test_date_fix_refuses_a_time_of_day_column():
    """A time carries no date and pd.to_datetime supplies one from the clock,
    so every value came back stamped with the day clean() happened to run.
    Nothing is deleted, so the loss gate stays quiet, and the column is
    datetime64 afterwards, so d02 is blind to it. Worse than lossy: the same
    input gives a different frame tomorrow."""
    values = ["08:00:00", "09:15:00", "13:45:00", "22:05:00"] * 8
    frame = pd.DataFrame({"t": values})

    cleaned, summary = clean(frame)

    assert cleaned["t"].tolist() == values, "a date was invented"
    assert not any(a["disease"] == 2 for a in summary.applied)
    assert _reviewed(summary, 2, ["t"]), summary.needs_review


def test_date_fix_refuses_an_order_decided_by_a_single_outlier():
    """`.max() > 12` is one cell. On 24 month-first dates whose day slot never
    exceeds 12, one stray "25/06/2020" derived %d/%m/%Y, transposed every
    other value, produced zero NaT and reported applied with needs_review
    empty: one visible loss traded for 24 invisible reversals."""
    values = [f"{m:02d}/{d:02d}/2020" for m in range(1, 13) for d in (3, 9)]
    values.append("25/06/2020")
    frame = pd.DataFrame({"d": values})

    cleaned, summary = clean(frame)

    assert cleaned["d"].tolist() == values, "day and month were swapped"
    assert not any(a["disease"] == 2 for a in summary.applied)
    assert _reviewed(summary, 2, ["d"]), summary.needs_review


def test_date_fix_still_parses_a_column_whose_only_gap_is_a_missing_token():
    """The over-refusal the loss gate bought: d02 grades AUTO from 90%
    coverage down, and a single 'N/A' in an otherwise clean ISO column made
    the whole band unfixable. A missing token is already absent; NaT loses
    nothing a person could have read."""
    values = [f"2020-01-{d:02d}" for d in range(1, 26)] + ["N/A"]
    frame = pd.DataFrame({"d": values})

    cleaned, summary = clean(frame)

    assert 2 in {a["disease"] for a in summary.applied}, summary.needs_review
    assert cleaned["d"].iloc[0] == pd.Timestamp("2020-01-01")
    assert int(cleaned["d"].isna().sum()) == 1


def test_header_fix_refuses_an_echo_row_an_earlier_fixer_rewrote():
    """_ORDER runs d18 last, so by the time the header fixer sees the frame
    the echo row has been through d06 and d07: here d07 folds the echo cell
    onto the column's more frequent spelling of the same word, so it matches
    neither the damaged name nor the repaired one. The verbatim check missed
    it, the rename went ahead, d18 then had nothing left to report, and the
    whole finding ('stray whitespace; 1 data rows repeat the header') was
    recorded verified with the junk row still in the table."""
    frame = pd.DataFrame(
        {
            "  Region ": ["  Region "] + ["REGION"] * 8 + ["east", "west"] * 4,
            "amount": ["amount"] + [str(v) for v in range(16)],
        }
    )

    cleaned, summary = clean(frame)

    assert list(cleaned.columns) == ["  Region ", "amount"], "renamed anyway"
    assert not any(a["disease"] == 18 for a in summary.applied)
    assert _reviewed(summary, 18, ["  Region "]), summary.needs_review
    assert cleaned.iloc[0].tolist() == ["REGION", "amount"], "the junk row is here"
