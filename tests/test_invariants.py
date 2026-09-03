"""Arc W3/H1: property invariants — the contracts that hold on ANY frame,
asserted across the input space instead of on hand-picked examples. These are
the promises the README makes implicitly; hypothesis's job is to find the
frame that breaks them before a user does."""

import hypothesis.strategies as st
import pandas as pd
from hypothesis import HealthCheck, given, settings

from crivo.detect import detect_all

_CELLS = st.one_of(
    st.none(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.floats(allow_nan=True, allow_infinity=True, width=32),
    st.text(max_size=12),
    st.sampled_from(["N/A", "-999", "", "  padded ", "café", "1,234.50", "Y", "no"]),
    st.datetimes(
        min_value=pd.Timestamp("1970-01-01").to_pydatetime(),
        max_value=pd.Timestamp("2120-01-01").to_pydatetime(),
    ),
)


@st.composite
def frames(draw):
    n_cols = draw(st.integers(min_value=0, max_value=6))
    n_rows = draw(st.integers(min_value=0, max_value=25))
    names = draw(
        st.lists(
            st.text(min_size=0, max_size=10),
            min_size=n_cols,
            max_size=n_cols,
            unique=True,
        )
    )
    data = {
        name: draw(st.lists(_CELLS, min_size=n_rows, max_size=n_rows)) for name in names
    }
    return pd.DataFrame(data)


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(frames())
def test_detect_all_never_raises_and_never_mutates(frame):
    before = frame.copy(deep=True)
    result = detect_all(frame)
    assert set(result) == {"findings", "clear", "broken"}
    assert result["broken"] == {}, "a signal crashed on a generated frame"
    pd.testing.assert_frame_equal(frame, before)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(frames())
def test_clean_is_pure_idempotent_and_keeps_the_verify_promise(frame):
    from crivo.autoclean import clean
    from crivo.detect import detect_one

    before = frame.copy(deep=True)
    cleaned, summary = clean(frame)
    pd.testing.assert_frame_equal(frame, before)  # never mutates its input

    # verify-or-revert, restated post-hoc: every applied fix's signal is
    # still gone on the FINAL frame — later fixes never resurrect a disease
    for fix in summary.applied:
        cols = [c for c in fix["columns"] if c in cleaned.columns]
        if fix["disease"] != 19 and cols:
            assert detect_one(cleaned, fix["disease"], cols) is None, fix

    again, second = clean(cleaned)
    assert not second.applied, f"second pass re-applied: {second.applied}"
    pd.testing.assert_frame_equal(again, cleaned)
