"""The ground-truth manifest is the bench's frozen interface: injectors record
into it, the scorer reads from it, the external adapter emits it — so the
JSON round-trip has to be lossless before anything parallel starts."""

from bench.truth import Cell, Corruption, GroundTruth


def test_json_round_trip_is_lossless():
    truth = GroundTruth(
        seed=7,
        base="typed_frame",
        n_rows=3,
        n_cols=2,
        frame_sha256="0" * 64,
        corruptions=[
            Corruption(
                disease=1,
                columns=("amount",),
                granularity="cell",
                cells=(Cell(row=0, column="amount", original=12.5, corrupted="12,50"),),
            ),
            Corruption(
                disease=9,
                columns=("amount", "name"),
                granularity="row",
                rows=(3,),
                note="appended duplicate of row 1",
            ),
        ],
    )
    again = GroundTruth.from_json(truth.to_json())
    assert again == truth
    assert again.corruptions[0].cells[0].original == 12.5
    assert again.corruptions[1].rows == (3,)


def test_verify_frame_pins_manifest_to_the_dirty_frame():
    import pandas as pd
    import pytest

    from bench.truth import frame_sha256

    frame = pd.DataFrame({"amount": ["12,50", "3.00"], "name": ["a", "b"]})
    truth = GroundTruth(
        seed=1,
        base="typed_frame",
        n_rows=2,
        n_cols=2,
        frame_sha256=frame_sha256(frame),
    )
    truth.verify_frame(frame)  # matching pair passes silently
    truth.verify_frame(frame.copy())  # hash is content-based, not identity-based
    with pytest.raises(ValueError, match="does not match"):
        truth.verify_frame(frame.assign(name=["a", "c"]))


def test_corruption_rejects_bad_vocabulary():
    import pytest

    # (kwargs override, expected message fragment)
    bad = [
        ({"granularity": "sideways"}, "granularity"),
        ({"disease": 23}, "disease"),
        ({"disease": -1}, "disease"),
    ]
    for override, fragment in bad:
        kwargs = {"disease": 1, "columns": ("a",), "granularity": "cell"} | override
        with pytest.raises(ValueError, match=fragment):
            Corruption(**kwargs)
    # disease 0 (external/unknown) is legal vocabulary
    Corruption(disease=0, columns=("a",), granularity="cell")
