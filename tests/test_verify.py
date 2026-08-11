"""Verify-cell builder tests (spec R9-R10): baseline/revert cells exec'd
against a real frame; verify_cell checked as a string contract only, because
the code it generates imports analyst_agent.detect at kernel runtime —
compiling it is fine, executing it here is not."""

import json

import pandas as pd

from analyst_agent.verify import (
    ROW_DELTA_BOUNDED,
    ROW_DELTA_EXACT,
    baseline_cell,
    revert_cell,
    verify_cell,
)

BASELINE_COLS = ["a", "b", "c"]


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"], "c": [0.5, 1.5, 2.5]})


def _exec_baseline(frame: pd.DataFrame) -> tuple[dict, str]:
    """Run the baseline cell the way the kernel would: exec every statement,
    eval the trailing expression (exec alone would discard its value)."""
    ns = {"df": frame}
    lines = baseline_cell("df").rstrip("\n").splitlines()
    exec(compile("\n".join(lines[:-1]), "<baseline>", "exec"), ns)  # noqa: S102
    value = eval(compile(lines[-1], "<baseline-last>", "eval"), ns)
    return ns, value


# --- baseline / revert: executable without analyst_agent.detect ---


def test_baseline_cell_snapshots_backup_rows_and_hashes():
    frame = _frame()
    ns, value = _exec_baseline(frame)
    assert ns["_clean_backup"] is not frame
    pd.testing.assert_frame_equal(ns["_clean_backup"], frame)
    assert ns["_clean_rows"] == len(frame)
    assert set(ns["_clean_hashes"]) == set(BASELINE_COLS)
    assert all(isinstance(h, int) for h in ns["_clean_hashes"].values())
    # the cell's value is the JSON list of baseline columns the host reads
    assert json.loads(value) == sorted(frame.columns)


def test_revert_cell_restores_the_mutated_frame():
    frame = _frame()
    original = frame.copy()
    ns, _ = _exec_baseline(frame)
    ns["df"]["a"] = [7, 8, 9]
    assert not ns["df"]["a"].equals(original["a"])  # mutation really landed
    exec(compile(revert_cell("df"), "<revert>", "exec"), ns)  # noqa: S102
    pd.testing.assert_frame_equal(ns["df"], original)


def test_baseline_cell_survives_duplicate_column_names():
    frame = pd.concat(
        [pd.DataFrame({"x": [1, 2]}), pd.DataFrame({"x": [3, 4]})], axis=1
    )
    assert list(frame.columns) == ["x", "x"]
    ns = {"df": frame}
    # positional hashing must not raise on duplicate names
    exec(compile(baseline_cell("df"), "<baseline-dup>", "exec"), ns)  # noqa: S102
    assert "x" in ns["_clean_hashes"]


# --- verify_cell: string contract + compile, never exec ---


def _finding(disease=4, slug="sentinel-missing", columns=("a",), stats=None):
    return {
        "disease": disease,
        "slug": slug,
        "columns": list(columns),
        "stats": stats or {},
    }


def _loop_line(code: str) -> str:
    return next(ln for ln in code.splitlines() if ln.startswith("for _c in "))


def test_verify_cell_reruns_detector_and_holds_rows_constant():
    code = verify_cell("df", _finding(), BASELINE_COLS)
    assert "detect_one" in code
    # non-delta disease: rows must be exactly unchanged
    assert "assert len(df) == _clean_rows," in code
    compile(code, "<v>", "exec")


def test_verify_cell_untouched_loop_excludes_only_the_fix_targets():
    code = verify_cell("df", _finding(columns=["a"]), BASELINE_COLS)
    loop = _loop_line(code)
    assert '"b"' in loop and '"c"' in loop  # every other baseline column
    assert '"a"' not in loop  # the target is allowed to change
    # the finding's own columns ride into detect_one json-encoded
    assert json.dumps(["a"]) in code


def test_verify_cell_exact_row_delta_for_duplicate_rows():
    finding = _finding(9, "duplicate-rows", stats={"dup_count": 7})
    code = verify_cell("df", finding, BASELINE_COLS)
    assert "_clean_rows - 7" in code
    assert "len(df) == _clean_rows - 7" in code
    compile(code, "<v9>", "exec")


def test_verify_cell_bounded_row_delta_for_near_duplicate_merges():
    finding = _finding(10, "near-duplicates", stats={"pair_count": 3})
    code = verify_cell("df", finding, BASELINE_COLS)
    assert "_clean_rows - 3 <= len(df) <= _clean_rows" in code
    compile(code, "<v10>", "exec")


def test_verify_cell_exact_row_delta_for_rollup_rows():
    finding = _finding(21, "rollup-rows", stats={"rollup_count": 2})
    code = verify_cell("df", finding, BASELINE_COLS)
    assert "len(df) == _clean_rows - 2" in code
    compile(code, "<v21>", "exec")


def test_row_delta_constants_match_spec_r9():
    assert ROW_DELTA_EXACT == {9: "dup_count", 21: "rollup_count"}
    assert ROW_DELTA_BOUNDED == {10: "pair_count"}
