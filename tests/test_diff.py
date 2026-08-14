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


def test_counts_are_right_for_a_one_shot_iterable() -> None:
    """zip(before_col, after_col) is the natural caller shape, and it can only
    be walked once. Re-iterating it to count the remainder read an exhausted
    iterator: a generator printed one sample and no '… N more' at all, so a
    preview of 5,000 changes claimed to have shown everything."""
    pairs = (("N/A", "NaN") for _ in range(5000))
    block = diff.column_change("ibu", changed=5000, total=5000, samples=pairs)

    assert "4,99" in block, f"the remainder must be reported:\n{block}"
    assert block.count("N/A") <= diff.SAMPLE_LIMIT


def test_a_long_repeated_value_still_marks_only_what_moved() -> None:
    """difflib's autojunk treats any token appearing in >1% of a sequence as
    junk once the sequence passes 200 elements — so packed and delimited cells,
    exactly what a gate preview must render, degraded to a whole-value replace:
    the very output this module says it exists to prevent."""
    before = ", ".join(["Vancouver BC"] * 80)
    after = before.replace("Vancouver BC", "Vancouver B.C.", 1)

    marked = sum(len(t) for op, t in diff.word_segments(before, after) if op != "equal")
    assert marked < 20, f"{marked} characters marked as moved; expected a handful"


def test_a_change_past_the_clip_boundary_still_shows_markers() -> None:
    """_clip truncated both values to 60 chars BEFORE inline() diffed them, so
    any change past character 59 rendered as one truncated line in which
    nothing appears to change — no delete, no insert, no hint. A gate preview
    of a notes field or a packed address whose edit sits in the tail told the
    operator the fix does nothing. Diff first, then bound the rendering."""
    before = "x" * 70 + " oz"
    after = "x" * 70

    block = diff.column_change("ounces", 2410, 2410, [(before, after)])

    assert "[- oz-]" in block, block
    assert max(len(line) for line in block.splitlines()) < 110, block


def test_a_preview_stays_a_preview() -> None:
    """CLAUDE.md: 'Never put raw dataset rows in a prompt — schema, stats, and
    truncated samples only.' Only the sample COUNT was bounded, so a free-text
    column dumped whole cells into the render. A newline in a value was worse:
    it split the sample across lines and pushed the trailing summary lines into
    the wrong place."""
    long_block = diff.column_change("notes", 2, 2, [("x" * 4000, "y" * 4000)])
    assert len(long_block) < 400, f"{len(long_block)} chars for one sample"
    assert "…" in long_block

    multiline = diff.column_change(
        "notes", 2, 2, [("a\nb", "a\nc")], untouched=["id", "name"]
    )
    assert multiline.splitlines()[-1].strip().startswith("2 columns untouched")
