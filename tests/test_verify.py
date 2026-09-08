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
    # one list of hex digests per NAME, one entry per COLUMN carrying it, so
    # a name two columns share records two digests instead of one overwriting
    # the other. Not the old commutative sum of row hashes either.
    assert all(
        isinstance(digests, list)
        and digests
        and all(isinstance(h, str) and h for h in digests)
        for digests in ns["_clean_hashes"].values()
    )
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


def _untouched_line(code: str) -> str:
    return next(ln for ln in code.splitlines() if ln.startswith("_untouched = "))


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
    names, and the invariant is behavioral. The frame has to carry every
    baseline column, or the untouched-column guard refuses it first and the
    detector is never reached."""
    import pytest

    code = verify_cell("df", _finding(disease=99), BASELINE_COLS)
    namespace = {"df": pd.DataFrame({"a": ["x", "y"], "b": [1, 2], "c": [3, 4]})}

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
    listed = _untouched_line(code)
    assert '"b"' in listed and '"c"' in listed  # every other baseline column
    assert '"a"' not in listed  # the target is allowed to change
    # the finding's own columns ride into detect_one json-encoded
    assert json.dumps(["a"]) in code


# --- the untouched-column guard, executed against real frames ---


def _guard_frame() -> pd.DataFrame:
    """One repairable target column ('flow', d04 sentinels) and two columns a
    fix aimed at 'flow' has no business touching."""
    return pd.DataFrame(
        {
            "flow": ["N/A", "N/A"] + [str(v) for v in range(8)],
            "site": [f"site-{v}" for v in range(10)],
            "note": [f"note {v}" for v in range(10)],
        }
    )


def _repair(frame: pd.DataFrame) -> pd.DataFrame:
    """The honest fix for the target column: the sentinels become missing."""
    out = frame.copy()
    out["flow"] = out["flow"].replace("N/A", pd.NA)
    return out


def _verify_after(fix) -> None:
    """The kernel's round trip: baseline snapshot, the fix cell's effect on the
    live variable, then the verify cell. Returns when the fix verifies, raises
    whatever the verify cell raises when it does not."""
    namespace = {"df": _guard_frame()}
    exec(compile(baseline_cell("df"), "<g-baseline>", "exec"), namespace)  # noqa: S102
    namespace["df"] = fix(namespace["df"])
    code = verify_cell("df", _finding(columns=("flow",)), ["flow", "note", "site"])
    exec(compile(code, "<g-verify>", "exec"), namespace)  # noqa: S102


def test_the_untouched_guard_passes_a_fix_that_only_repairs_its_target():
    """The control for the three refusals below: an honest repair of the target
    column, and nothing else moved, still verifies."""
    _verify_after(_repair)


def test_a_fix_that_drops_an_unrelated_column_fails_verification():
    """The guard walked the baseline columns and skipped any that were no
    longer in the frame, so deleting a whole column it was not aiming at was
    invisible to it: the detector cleared, the row count held, and the loop
    recorded the fix as verified while a column of data was gone."""
    import pytest

    with pytest.raises(AssertionError, match="were not fix targets"):
        _verify_after(lambda f: _repair(f).drop(columns=["note"]))


def test_a_fix_that_renames_an_unrelated_column_fails_verification():
    """A rename is a drop plus an arrival under the old guard: the baseline name
    was skipped as absent and the new name was never in the baseline list, so
    nothing checked it. Downstream code keyed on the old name silently reads
    nothing."""
    import pytest

    with pytest.raises(AssertionError, match="were not fix targets"):
        _verify_after(lambda f: _repair(f).rename(columns={"note": "notes"}))


def test_a_fix_that_shuffles_an_unrelated_column_fails_verification():
    """The per-column digest was a .sum() of the row hashes, and a sum is
    commutative: permuting a column's rows left the digest identical. So a fix
    that tore one column loose from its rows passed the untouched check while
    corrupting every record in the frame."""
    import pytest

    def shuffle_note(frame: pd.DataFrame) -> pd.DataFrame:
        out = _repair(frame)
        out["note"] = list(out["note"])[::-1]
        return out

    with pytest.raises(AssertionError, match="was not a fix target"):
        _verify_after(shuffle_note)


def test_the_baseline_digest_is_order_dependent():
    """Same values, different order, must digest differently, or the guard
    above has nothing to compare."""
    ns, _ = _exec_baseline(_frame())
    reversed_b = _frame()
    reversed_b["b"] = list(reversed_b["b"])[::-1]
    ns2, _ = _exec_baseline(reversed_b)

    assert ns2["_clean_hashes"]["a"] == ns["_clean_hashes"]["a"]
    assert ns2["_clean_hashes"]["c"] == ns["_clean_hashes"]["c"]
    assert ns2["_clean_hashes"]["b"] != ns["_clean_hashes"]["b"]


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


# --- integration pass: the guard's keying, executed against real frames ------


def _verify_frame(frame: pd.DataFrame, fix, columns) -> None:
    """The kernel round trip over an arbitrary frame: baseline, fix, verify.
    `columns` is the baseline column list the host reads back from the
    baseline cell, so the two sides agree the way the loop makes them agree."""
    namespace = {"df": frame}
    exec(compile(baseline_cell("df"), "<k-baseline>", "exec"), namespace)  # noqa: S102
    namespace["df"] = fix(namespace["df"])
    code = verify_cell("df", _finding(columns=("flow",)), columns)
    exec(compile(code, "<k-verify>", "exec"), namespace)  # noqa: S102


def test_a_non_string_column_name_does_not_break_the_guard():
    """The guard recorded untouched names as str() and then subscripted the
    frame with the recorded string, so one integer-named bystander raised
    KeyError on every finding in the frame: no fix on it could ever verify,
    and the model burned its whole attempt budget on the lookup. Reachable
    through crivo's own reader on any spreadsheet with year headers."""
    frame = _guard_frame().rename(columns={"site": 2020})

    _verify_frame(frame, _repair, ["2020", "flow", "note"])


def test_a_lossless_whole_frame_reorder_still_verifies():
    """The digest read values in frame order, so sorting or sampling the whole
    frame, every column moving together and nothing lost, failed the untouched
    check. Pairing each value with its index label and folding the pairs in
    sorted order keeps the tear-loose refusal below while letting the lossless
    move through."""
    _verify_after(lambda f: _repair(f).sort_values("site", ascending=False))


def test_a_reorder_that_also_resets_the_index_still_verifies():
    """Pairing each value with its index label put the index inside the guard,
    and `sort_values(...).reset_index(drop=True)` is one idiom, not two: the
    frame moves as a unit and every label is rewritten. The pairs then all
    change and the untouched guard refused a fix that lost nothing, which is
    over-refusal - a repair the model wrote correctly, reverted and counted
    against it."""
    _verify_after(
        lambda f: _repair(f).sort_values("site", ascending=False).reset_index(drop=True)
    )


def test_an_index_reset_does_not_excuse_a_column_torn_loose():
    """The other half, and the one that must not be traded away for the test
    above: a fix that rewrites the index AND tears one untouched column loose
    from its rows has to keep failing. Corruption is silent and a refusal is
    not."""
    import pytest

    def reorder_then_tear(frame: pd.DataFrame) -> pd.DataFrame:
        out = _repair(frame).sort_values("site", ascending=False)
        out = out.reset_index(drop=True)
        out["note"] = list(out["note"])[::-1]
        return out

    with pytest.raises(AssertionError, match="was not a fix target"):
        _verify_after(reorder_then_tear)


def test_the_whole_untouched_block_sliding_together_is_refused():
    """DELIBERATELY INVERTED. This was pinned as the documented price of the
    reorder exemption, on the reading that "once the index labels are gone"
    nothing can say whether the target column rode along. The labels are not
    gone here: this fix never touches the index, and the exemption's real
    precondition was only that the live index is a positional range - which
    every default-index frame satisfies, so the residual applied to almost
    every frame crivo reads rather than to reordered ones.

    Something CAN say whether the target rode along: the target column itself.
    A genuine reorder differs from the permuted baseline only on the cells the
    repair changed; a slid block leaves the target sitting still and disagrees
    with the permutation nearly everywhere. `rode_along` asks it, so this now
    refuses, and the guard is back to what the P2 index-pairing bought.

    The test above (`..._reorder_that_also_resets_the_index_still_verifies`)
    pins the recovery this must not cost."""
    import pytest

    def slide_the_block(frame: pd.DataFrame) -> pd.DataFrame:
        out = _repair(frame)
        rotated = list(range(1, len(out))) + [0]
        out[["site", "note"]] = out[["site", "note"]].iloc[rotated].to_numpy()
        return out

    with pytest.raises(AssertionError, match="was not a fix target"):
        _verify_after(slide_the_block)


def test_an_index_rewrite_with_one_untouched_column_is_refused():
    """The documented residual, pinned so it is not loosened by accident. With
    a single untouched column there is nothing left to pair it against: once
    the index labels are gone, "the frame moved as a unit" and "this column
    was permuted against the rest of the frame" are the same picture. The
    ambiguous case takes the refusal."""
    import pytest

    frame = _guard_frame()[["flow", "note"]]

    def reorder(f: pd.DataFrame) -> pd.DataFrame:
        return _repair(f).sort_values("note", ascending=False).reset_index(drop=True)

    with pytest.raises(AssertionError, match="was not a fix target"):
        _verify_frame(frame, reorder, ["flow", "note"])


def test_a_fix_that_wipes_a_name_shadowed_column_fails_verification():
    """The baseline kept ONE digest per NAME, and it keyed by str(name), so of
    two columns whose names str() alike only the survivor of the dict write
    was addressed by anything: the integer-named one could be emptied and the
    cell still reported VERIFIED. crivo manufactures this shape itself:
    _fix_headers str()s every name during a d18 repair, and the loop then
    re-snapshots the baseline over the collided names."""
    import pytest

    frame = _guard_frame().rename(columns={"site": 1, "note": "1"})
    assert list(frame.columns) == ["flow", 1, "1"]

    def wipe_the_shadowed(f: pd.DataFrame) -> pd.DataFrame:
        out = _repair(f)
        out.iloc[:, 1] = "WIPED"  # the integer-named column
        return out

    with pytest.raises(AssertionError, match="was not a fix target"):
        _verify_frame(frame, wipe_the_shadowed, ["1", "flow"])


def test_a_frame_with_duplicate_column_names_can_still_verify_an_honest_fix():
    """The other side of it: keying by name also meant the digest was computed
    over a two-column DataFrame at verify time and a single column at baseline
    time, so a frame carrying a duplicate name could never verify anything at
    all, however honest the fix."""
    frame = _guard_frame().rename(columns={"site": "note"})

    _verify_frame(frame, _repair, ["flow", "note"])


def test_a_constant_untouched_column_does_not_excuse_a_torn_neighbour():
    """The reorder exemption fires when EVERY untouched name is torn, and the
    packet treated that as the safety property: tearing a proper subset leaves
    the others' digests intact, so not every name is torn. A CONSTANT column
    refutes it. Its (index, value) digest is torn by the index rewrite like
    every other, and it adds nothing that distinguishes one row from another,
    so the whole-row multiset is blind to a neighbour torn loose beside it.

    crivo has an entire disease for constant columns (d19), so this is not an
    exotic frame in crivo's own model of dirty data. Every record's city ends
    up attached to the wrong price."""
    import pytest

    frame = pd.DataFrame(
        {
            "flow": ["N/A", "N/A"] + [str(v) for v in range(8)],
            "city": [f"city-{v}" for v in range(10)],
            "country": ["US"] * 10,
        },
        index=[f"r{v}" for v in range(10)],
    )

    def tear_past_the_constant(f: pd.DataFrame) -> pd.DataFrame:
        out = _repair(f)
        out["city"] = list(out["city"])[::-1]
        return out.reset_index(drop=True)

    with pytest.raises(AssertionError, match="was not a fix target"):
        _verify_frame(frame, tear_past_the_constant, ["flow", "city", "country"])


def test_a_target_a_header_repair_renamed_is_not_treated_as_untouched():
    """The other half of the cross-packet item 16 fix, and without it the
    detect half is inert. detect_one now resolves a frozen target onto the
    column an already-verified d18 repair renamed it to, but verify_cell still
    built its untouched list from the FROZEN name. The loop re-snapshots the
    baseline after every verified fix, so the baseline carries the NEW name,
    the frozen name matches nothing, and the renamed column lands in
    `untouched` - the one column the fix is licensed to change. The detector
    cleared and the untouched guard then tore the honest repair up.

    Resolved the same way detect_one resolves it, so the two halves cannot
    drift apart."""
    frame = pd.DataFrame(
        {
            "active": ["Y", "N", "yes", "no", "TRUE", "FALSE", "1", "0"] * 5,
            "n": range(40),
        }
    )
    finding = _finding(23, "boolean-chaos", columns=("  active ",))

    namespace = {"df": frame.copy()}
    exec(compile(baseline_cell("df"), "<r-baseline>", "exec"), namespace)  # noqa: S102
    namespace["df"] = namespace["df"].assign(
        active=namespace["df"]["active"].str.lower().isin(["y", "yes", "true", "1"])
    )
    code = verify_cell("df", finding, ["active", "n"])

    exec(compile(code, "<r-verify>", "exec"), namespace)  # noqa: S102
