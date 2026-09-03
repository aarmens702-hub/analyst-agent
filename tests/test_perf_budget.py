"""Arc W3/H3: the perf budget — a regression net, not a benchmark. Measured
2026-09-03 on the dev machine: detect_all over 300k transaction rows in 2.6s
with all 26 signals. The bar is 5x that, so a future detector that silently
turns diagnose() quadratic fails here before it ships, while CI machine
noise never does."""

import time

from crivo.detect import detect_all


def test_detect_all_stays_inside_the_300k_row_budget():
    from bench.bases import transactions

    frame = transactions(7, 300_000)
    started = time.monotonic()
    result = detect_all(frame)
    elapsed = time.monotonic() - started
    assert result["broken"] == {}, "a crashed signal is not a fast signal"
    assert elapsed < 15.0, f"detect_all took {elapsed:.1f}s on 300k rows"
