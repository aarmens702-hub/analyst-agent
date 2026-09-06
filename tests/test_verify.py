"""Verify-cell builder tests (spec R9-R10): baseline/revert cells exec'd
against a real frame; verify_cell checked as a string contract only, because
the code it generates imports crivo.detect at kernel runtime —
compiling it is fine, executing it here is not."""

import json

import pandas as pd

from crivo import verify
from crivo.detect import detect_one
from crivo.verify import (
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


# --- baseline / revert: executable without crivo.detect ---


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


def test_preview_cell_shows_consequence_without_touching_the_frame():
    """R3: the gate shows the code and asks whether to run it, which makes the
    operator execute pandas in their head. The preview applies the fix to a
    sampled scratch copy and renders what would move — and the live frame is
    untouched by construction, not by revert (AC3). A fix that errors on the
    sample degrades to a one-line reason instead of blocking the gate."""
    import contextlib
    import io

    fix_source = (
        "def fix_amount(df):\n"
        "    out = df.copy()\n"
        "    out['amount'] = out['amount'].str.replace(',', '', regex=False)\n"
        "    return out\n"
        "df = fix_amount(df)\n"
    )
    frame = pd.DataFrame({"amount": ["1,200", "3,400"], "note": ["a", "b"]})
    namespace = {"df": frame}

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(  # noqa: S102
            compile(verify.preview_cell("df", fix_source), "<pv>", "exec"), namespace
        )
    rendered = out.getvalue()

    assert namespace["df"] is frame, "the live variable must not be rebound"
    assert list(frame["amount"]) == ["1,200", "3,400"], "or mutated"
    assert "preview on 2 of 2 rows" in rendered
    assert "amount: 2 of 2 cells change" in rendered
    assert "untouched" in rendered

    broken = "raise RuntimeError('no such column')\n"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(  # noqa: S102
            compile(verify.preview_cell("df", broken), "<pv2>", "exec"), namespace
        )
    assert "preview unavailable: RuntimeError" in out.getvalue()


def test_the_preview_screen_refuses_anything_beyond_dataframes():
    """A preview executes model code BEFORE the human approves it. The
    scratch copy protects the data; it cannot protect the process — a cell
    that SIGKILLs the kernel, opens files, or reaches for dunders would do so
    unapproved. Discovered live: the sigkill recovery test died inside its
    own preview. Anything beyond pure dataframe work degrades the gate to
    code-only, with the reason named."""
    pure = (
        "import pandas as pd\n"
        "def fix(df):\n    return df.copy()\n"
        "df = fix(df)\n"
        "assert len(df) > 0\n"
    )
    assert verify.preview_screen(pure) == ""

    assert "os" in verify.preview_screen("import os\nos.kill(1, 9)\n")
    assert "open" in verify.preview_screen("open('/etc/passwd')\n")
    assert "dunder" in verify.preview_screen("df.__class__.__init__\n")
    assert "parse" in verify.preview_screen("def broken(:\n")


def test_the_preview_screen_catches_the_bypasses_it_can_see():
    """The screen used to read dunders only when they were spelled as
    attributes, and it never looked at pandas, which carries its own doors to
    the filesystem. So a cell reached __builtins__ through a subscript, named
    it through getattr, or called pd.read_pickle (which runs whatever the file
    unpickles) and still previewed, unapproved. These are the cheap spellings,
    and they are now named. The expensive ones stay open by construction,
    which is what the docstring says out loud."""
    # a dunder reached by string subscript is the same reach as an attribute
    assert verify.preview_screen("globals()['__builtins__']['eval']('1+1')\n") != ""
    assert "dunder" in verify.preview_screen("d['__class__']\n")
    # ... and so is one named as a string to a lookup builtin
    assert "getattr" in verify.preview_screen("getattr(df, '__class__')\n")
    assert verify.preview_screen("vars(df)['__dict__']\n") != ""

    # pandas reads and writes files without importing anything the import
    # check would see; read_pickle executes what it loads
    assert "read_pickle" in verify.preview_screen("df = pd.read_pickle('x.pkl')\n")
    assert "read_csv" in verify.preview_screen("df = pd.read_csv('/etc/passwd')\n")
    assert "to_csv" in verify.preview_screen("df.to_csv('/tmp/out.csv')\n")
    assert "to_pickle" in verify.preview_screen("df.to_pickle(path='p')\n")

    # but a writer handed no destination returns a string and touches nothing
    assert verify.preview_screen("s = df.to_csv()\n") == ""
    assert verify.preview_screen("s = df.to_json(index=False)\n") == ""
    # and ordinary dataframe work still previews
    assert verify.preview_screen("df['amount'] = df['amount'].abs()\n") == ""


def test_the_preview_screen_still_previews_ordinary_cleaning_code():
    """The screen matched builtin names against any attribute call, so
    re.compile() (an allowlisted module's main API, and all over crivo's own
    fixers) and df.eval()/pd.eval() (restricted pandas expression ops, not
    Python eval) lost their preview to a reason that misdescribed them. Real
    dunder-shaped names are collateral of the same kind: '__index_level_0__'
    is pandas' own index column, written by pandas."""
    assert verify.preview_screen("import re\npat = re.compile(r'^\\d+$')\n") == ""
    assert verify.preview_screen("df = df[df.eval('amount > 0')]\n") == ""
    assert verify.preview_screen("mask = pd.eval('df.amount > 0')\n") == ""
    assert verify.preview_screen("df = df.drop(columns=['__index_level_0__'])\n") == ""
    assert verify.preview_screen("src = meta['__source__']\n") == ""


def test_the_preview_screen_reads_every_name_in_an_import():
    """'import pandas, os' is one Import node with two names and only the
    first was read, so a forbidden module in second position previewed
    unapproved while the docstring listed imports as a caught pattern."""
    assert "os" in verify.preview_screen("import pandas, os\n")
    assert "subprocess" in verify.preview_screen("import numpy as np, subprocess\n")


def test_the_preview_screen_sees_numpys_doors_as_well_as_pandas():
    """numpy is allowlisted and carries the same two doors the fix closed for
    pandas: np.load runs what it unpickles when allow_pickle is set, and the
    save/loadtxt family reads and writes files. Closing one and not the other
    is an asymmetry the docstring does not lead a reader to expect."""
    assert "np.load" in verify.preview_screen("a = np.load('x.npy', allow_pickle=True)")
    assert "np.savetxt" in verify.preview_screen("np.savetxt('/tmp/out.csv', a)\n")
    assert "np.fromfile" in verify.preview_screen("a = numpy.fromfile('/etc/passwd')\n")
    # a receiver that is not numpy keeps its ordinary meaning
    assert verify.preview_screen("frame = store.load()\n") == ""


def test_the_preview_screen_sees_pandas_constructors_and_to_string_buf():
    """pd.ExcelFile and pd.HDFStore open files without being spelled read_*,
    and to_string touches the filesystem only when it is handed buf=."""
    assert "ExcelFile" in verify.preview_screen("book = pd.ExcelFile('/tmp/x.xlsx')\n")
    assert "HDFStore" in verify.preview_screen("st = pd.HDFStore('/tmp/x.h5')\n")
    assert "to_string" in verify.preview_screen("df.to_string(buf='/tmp/out.txt')\n")
    assert verify.preview_screen("text = df.to_string()\n") == ""


def test_the_preview_screen_docstring_refuses_to_claim_containment():
    """The screen is the last thing standing between model code and the
    kernel at preview time, which makes it tempting to read as a boundary. It
    is not one: a name assembled at runtime walks past every check in it. The
    docstring has to say so, because the next person to lean on this function
    will read that and nothing else."""
    doc = verify.preview_screen.__doc__ or ""
    assert "not a security boundary" in doc
    assert "best-effort" in doc
    assert "sandbox" in doc


def test_a_detector_crash_reads_as_uncheckable_not_as_a_failed_fix():
    """Inside verify, a detector crash was indistinguishable from a fix that
    did not work: the cell just errored, the revert ran, and the skill ledger
    took the hit — two crashes in one run retired a working skill on someone
    else's bug. The cell must name the difference so the loop can decline to
    score it. Could-not-check still fails the cell: unverified is unverified.

    This one executes (unlike the string-contract tests below): the crash
    fires on the detect_one line, before the cell touches any kernel-only
    names, and the invariant is behavioral."""
    import pytest

    code = verify_cell("df", _finding(disease=99), BASELINE_COLS)
    namespace = {"df": pd.DataFrame({"a": ["x", "y"]})}

    with pytest.raises(RuntimeError, match="^uncheckable: ValueError"):
        exec(compile(code, "<v-crash>", "exec"), namespace)  # noqa: S102


def test_a_word_splitting_whitespace_repair_fails_verification():
    """detect.py documents zero-width as delete-not-space, so Bud<ZWSP>weiser
    becomes Budweiser and never 'Bud weiser' — but layer 1 only asserted the
    signal stopped firing, which the word-splitting repair also satisfies. The
    corrupted fix was marked fixed, frozen as its own case, and eligible to
    become a skill that runs unattended on AUTO findings. d06 verification is
    now anchored to the reference repair (_ws_tidy of the original), which the
    split disagrees with and the honest repair matches."""
    import pytest

    original = pd.Series(["Bud\u200bweiser"] * 8 + ["Coors Light"] * 12, name="beer")
    finding = _finding(disease=6, slug="whitespace-damage", columns=("beer",))
    code = verify_cell("df", finding, ["beer"])
    namespace = {
        "df": pd.DataFrame({"beer": original.str.replace("\u200b", " ")}),
        "_clean_backup": pd.DataFrame({"beer": original}),
        "_clean_rows": 20,
        "_clean_hashes": {},
    }

    with pytest.raises(AssertionError, match="reference"):
        exec(compile(code, "<v-zw>", "exec"), namespace)  # noqa: S102

    namespace["df"] = pd.DataFrame({"beer": original.str.replace("\u200b", "")})
    exec(compile(code, "<v-zw-ok>", "exec"), namespace)  # noqa: S102


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


def test_a_frozen_case_still_trips_the_detector_it_was_carved_from() -> None:
    """Admission asserts the case still exhibits the disease, or passing proves
    nothing. But the case concentrates sick rows (150 sick + 50 healthy), which
    moves every fraction-based threshold — and disease 22's density guard reads
    a dense integer range as a row counter rather than a code. Concentration
    must not flip either verdict."""
    zips = pd.DataFrame(
        {"zip_code": ([35233, 90210, 60614, 98101] * 30) + ([2115, 2116] * 20)}
    )
    assert detect_one(zips, 22, ["zip_code"]) is not None, "the column itself"

    short = zips["zip_code"].astype(str).str.len() < 5
    case = pd.concat(
        [
            zips[short].head(verify.CASE_SICK_ROWS),
            zips[~short].head(verify.CASE_HEALTHY_ROWS),
        ]
    ).sort_index()
    assert detect_one(case, 22, ["zip_code"]) is not None, (
        "the frozen case must still trip it, or admission refuses good skills"
    )

    sentinel = pd.DataFrame({"flow": ["N/A"] * 3 + [str(v) for v in range(200)]})
    sick = sentinel["flow"] == "N/A"
    thin = pd.concat(
        [sentinel[sick], sentinel[~sick].head(verify.CASE_HEALTHY_ROWS)]
    ).sort_index()
    assert detect_one(thin, 4, ["flow"]) is not None, (
        "a disease with only a handful of sick rows must survive the carve too"
    )
