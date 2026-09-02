"""crivo.load_example() — the README's first block must run offline (arc spec
R1): a deterministic in-code messy frame whose docstring-listed diseases the
engine actually finds, and whose AUTO subset clean() actually fixes."""

import crivo


def test_load_example_is_deterministic_and_its_planted_diseases_are_found():
    frame = crivo.load_example()
    again = crivo.load_example()
    assert frame.equals(again), "same frame every call, forever"
    assert len(frame) == 60

    found = {f["disease"] for f in crivo.diagnose(frame, name="example").findings}
    planted = {1, 4, 6, 7, 9}  # the docstring's promised list
    assert planted <= found, f"missing: {sorted(planted - found)}"

    cleaned, summary = crivo.clean(frame)
    assert len(summary.applied) >= 3, "the AUTO subset must actually fix"
    assert not cleaned.equals(frame)
    assert frame.equals(again), "clean never mutates its input"
    assert "load_example" in crivo.api.__all__
