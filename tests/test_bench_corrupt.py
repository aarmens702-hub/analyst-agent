"""corrupt.py injectors must record exactly what they plant: the ground
truth is the only thing a scorer can trust downstream, so a mismatch between
recorded cells and the actual pristine-to-dirty diff would make every number
built on top of it a lie. Diseases 1 and 4 are the representative pair — one
rewrites an entire column's text while damaging only a rate-selected subset
(untouched cells still change repr, just not meaning), the other only ever
touches the cells it names — so together they cover both recording shapes."""

import numpy as np
import pytest

from bench.bases import typed_frame
from bench.corrupt import INJECTORS, corrupt
from bench.truth import GroundTruth


def _differs(original, corrupted) -> bool:
    """True unless `corrupted` is exactly `original`, or (disease 1's case)
    exactly the plain 2dp string form of a float original — stringifying a
    float column isn't itself the damage, only the rate-selected subset is."""
    if corrupted == original:
        return False
    if isinstance(original, float):
        return str(corrupted) != f"{original:.2f}"
    return str(corrupted) != str(original)


def test_recorded_cells_match_the_pristine_to_dirty_diff():
    for disease_id in (1, 4):
        frame = typed_frame(7, 30, {"amount": "numeric", "label": "id"})
        truth = GroundTruth(
            seed=7, base="typed_frame", n_rows=30, n_cols=2, frame_sha256=""
        )
        rng = np.random.default_rng(7)
        dirty = INJECTORS[disease_id](frame, truth, rng, rate=0.2)

        [corruption] = truth.corruptions
        col = corruption.columns[0]
        recorded = {(c.row, c.column) for c in corruption.cells}
        actual = {
            (row, col)
            for row in range(len(frame))
            if _differs(frame[col].iat[row], dirty[col].iat[row])
        }

        assert recorded == actual, f"disease {disease_id}"
        assert len(recorded) >= 1


def _rich_frame(n=40):
    """One frame where columns=None auto-pick succeeds for every disease:
    a float+int numeric pair, a datetime, an ascii category, an accented
    text column, a unique id, a lat/lon pair, and a start/end pair."""
    return typed_frame(
        3,
        n,
        {
            "amount": "numeric",
            "count": "int",
            "posted_at": "datetime",
            "city": "category",
            "bio": "text",
            "key": "id",
            "lat": "lat",
            "lon": "lon",
            "start": "start",
            "end": "end",
        },
    )


EXPECTED_DISEASES = tuple(sorted(set(range(1, 23)) - {20}))


@pytest.mark.parametrize("disease_id", EXPECTED_DISEASES)
def test_every_injector_plants_at_least_one_corruption_deterministically(disease_id):
    frame = _rich_frame()
    snapshot = frame.copy()
    fn = INJECTORS[disease_id]

    def run():
        truth = GroundTruth(
            seed=9,
            base="typed_frame",
            n_rows=len(frame),
            n_cols=len(frame.columns),
            frame_sha256="",
        )
        dirty = fn(frame, truth, np.random.default_rng(9))
        return dirty, truth

    dirty_a, truth_a = run()
    dirty_b, truth_b = run()

    assert frame.equals(snapshot), f"disease {disease_id} mutated its input frame"
    assert len(truth_a.corruptions) >= 1
    assert dirty_a.equals(dirty_b), f"disease {disease_id} is not deterministic"
    assert truth_a.corruptions == truth_b.corruptions


@pytest.mark.parametrize("disease_id", (9, 10, 21))
def test_row_appending_diseases_only_touch_appended_positions(disease_id):
    frame = _rich_frame()
    n = len(frame)
    truth = GroundTruth(
        seed=5, base="typed_frame", n_rows=n, n_cols=len(frame.columns), frame_sha256=""
    )
    dirty = INJECTORS[disease_id](frame, truth, np.random.default_rng(5))

    [corruption] = truth.corruptions
    assert corruption.granularity == "row"
    assert corruption.rows
    assert all(r >= n for r in corruption.rows)
    assert dirty.iloc[:n].equals(frame)


def _truth(n, n_cols):
    return GroundTruth(
        seed=1, base="typed_frame", n_rows=n, n_cols=n_cols, frame_sha256=""
    )


def test_applicability_errors_are_never_silently_skipped():
    ascii_frame = typed_frame(1, 10, {"city": "category", "amount": "numeric"})
    with pytest.raises(ValueError, match="applicable"):
        INJECTORS[8](ascii_frame, _truth(10, 2), np.random.default_rng(1))

    no_pair_frame = typed_frame(1, 10, {"posted_at": "datetime", "amount": "numeric"})
    with pytest.raises(ValueError, match="pair"):
        INJECTORS[12](no_pair_frame, _truth(10, 2), np.random.default_rng(1))


def test_corrupt_end_to_end_manifest_matches_and_round_trips():
    frame = _rich_frame()
    dirty, truth = corrupt(frame, diseases=[1, 6, 9], seed=11, base="typed_frame")

    truth.verify_frame(dirty)  # does not raise
    again = GroundTruth.from_json(truth.to_json())
    assert again == truth
    assert truth.n_rows == len(frame)
    assert truth.n_cols == len(frame.columns)
    assert len(truth.corruptions) == 3


def test_d7_targets_repeated_vocab_and_plants_case_only_variants():
    """Bench triage 2026-09-02: d7 auto-picked the unique txn_id column, where
    a recased one-occurrence value creates no fold-collision — the disease
    (one entity under several spellings) requires REPEATED values, so the
    plant was undetectable by construction. And its trailing-space fallback
    is d6's disease, not d7's: every planted variant must differ from its
    original by case alone."""
    import numpy as np

    from bench.bases import transactions
    from bench.corrupt import INJECTORS
    from bench.truth import GroundTruth

    frame = transactions(107, 250)
    truth = GroundTruth(seed=107, base="t", n_rows=250, n_cols=9, frame_sha256="")
    INJECTORS[7](frame, truth, np.random.default_rng(107))
    (corruption,) = truth.corruptions
    (col,) = corruption.columns
    assert frame[col].nunique() / len(frame) <= 0.5, (
        f"d7 must target a repeated-vocab column, picked {col!r}"
    )
    for cell in corruption.cells:
        assert str(cell.corrupted) != str(cell.original)
        assert str(cell.corrupted).casefold() == str(cell.original).casefold(), (
            "variants must differ by case alone — whitespace is d6's disease"
        )
