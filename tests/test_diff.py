"""Tests for the change renderer (P5 R2).

The gate asks "should this run?" while showing only code. These are the pieces
that let it show consequence instead. The hard part is not computing a diff —
it is rendering one a person can read at a glance, which means marking the part
of a value that actually moved rather than two whole values side by side.
"""

from analyst_agent import diff


def test_word_segments_marks_only_the_part_that_moved() -> None:
    """'12.0 oz' -> '12.0' should point at ' oz', not at the whole value."""
    segments = diff.word_segments("12.0 oz", "12.0")

    assert "".join(text for op, text in segments if op != "insert") == "12.0 oz"
    assert "".join(text for op, text in segments if op != "delete") == "12.0"
    moved = [text for op, text in segments if op == "delete"]
    assert moved == [" oz"], moved


def test_inline_marks_the_change_without_printing_the_value_twice() -> None:
    """A reader should see one line, with the moved part called out."""
    line = diff.inline("American Amber / Red Lager", "American Amber / Red Ale")

    assert "American Amber / Red " in line
    assert "[-Lager-]" in line and "{+Ale+}" in line
    assert line.count("American Amber") == 1, "the common part is not repeated"


def test_column_change_answers_what_will_this_do_to_my_data() -> None:
    """The question a gate poses. Counts first, then a sample of real values,
    then what was left alone — because 'nothing else moved' is half the
    reassurance and today the operator has no way to know it."""
    block = diff.column_change(
        "ibu",
        changed=1005,
        total=2410,
        samples=[("N/A", "NaN"), ("N/A", "NaN"), ("60", "60.0")],
        untouched=["beer_name", "style", "ounces"],
    )

    assert "ibu" in block
    assert "1,005 of 2,410" in block, "counts must be readable, not raw ints"
    assert "N/A" in block and "NaN" in block
    assert "3 columns untouched" in block
    assert block.count("N/A") <= 3, "samples are truncated, never a data dump"
