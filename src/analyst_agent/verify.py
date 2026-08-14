"""Builders for the kernel cells that make a fix *verified* (P1 R9-R10) and a
skill *admissible* (P2 R7-R8).

All functions return code strings executed in the kernel by the CLEAN flow:
baseline snapshot, per-fix verification (layer 1: detector re-run + row
invariant + untouched-column hashes), revert, and — once a fix has earned a
skill proposal — freezing the case it came from and re-running the generalised
fix against it. Layer 2 (the model's own asserts) lives inside the fix cell.

Everything here runs in the sandbox, never on the host. Model-authored code
meeting real data belongs behind the same wall as every other cell.
"""

import json

# diseases whose fixes legitimately change the row count (spec R9)
ROW_DELTA_EXACT = {9: "dup_count", 21: "rollup_count"}  # stats key with the delta
ROW_DELTA_BOUNDED = {10: "pair_count"}  # merges ≤ candidate pairs

CASE_SICK_ROWS = 150  # rows the fix actually changed
CASE_HEALTHY_ROWS = 50  # ... plus untouched ones, so a fix must not harm them

BASELINE_TEMPLATE = """\
import json as _json
import pandas as pd
_clean_backup = {var}.copy()
_clean_rows = len({var})
_clean_hashes = {{}}
for _i in range(len({var}.columns)):
    _clean_hashes[str({var}.columns[_i])] = int(
        pd.util.hash_pandas_object({var}.iloc[:, _i], index=False).sum()
    )
_json.dumps(sorted(_clean_hashes))
"""


def baseline_cell(var: str) -> str:
    """Snapshot rows, per-column hashes, and a revert copy. The cell's value
    is a JSON list of baseline column names (the host needs it for R9)."""
    return BASELINE_TEMPLATE.format(var=var)


def revert_cell(var: str) -> str:
    return f'{var} = _clean_backup.copy()\n"reverted"'


def _row_invariant(var: str, finding: dict) -> str:
    disease = finding["disease"]
    stats = finding.get("stats", {})
    if disease in ROW_DELTA_EXACT:
        delta = int(stats.get(ROW_DELTA_EXACT[disease], 0))
        return (
            f"assert len({var}) == _clean_rows - {delta}, "
            f'f"expected exactly {delta} rows removed, '
            f'got {{_clean_rows - len({var})}}"'
        )
    if disease in ROW_DELTA_BOUNDED:
        bound = int(stats.get(ROW_DELTA_BOUNDED[disease], 0))
        return (
            f"assert _clean_rows - {bound} <= len({var}) <= _clean_rows, "
            f'"row count outside the allowed merge range"'
        )
    return (
        f"assert len({var}) == _clean_rows, "
        f'f"row count changed ({{_clean_rows}} -> {{len({var})}}) '
        f'but this fix must not add or drop rows"'
    )


PREVIEW_ROWS = 200  # the ruling from open-findings D: sampled, never the frame

PREVIEW_TEMPLATE = """\
import pandas as pd
from analyst_agent import diff as _diff

_pv_names = {names}
_pv_before = {{
    _v: globals()[_v].head({rows}).copy()
    for _v in _pv_names
    if isinstance(globals().get(_v), pd.DataFrame)
}}
_pv_ns = {{"pd": pd}}
_pv_ns.update({{_v: _f.copy() for _v, _f in _pv_before.items()}})
try:
    exec(compile({fix_source!r}, "<preview>", "exec"), _pv_ns)
except Exception as _exc:
    print(f"(preview unavailable: {{type(_exc).__name__}}: {{_exc}})")
else:
    _lines = []
    for _v, _b in _pv_before.items():
        _after = _pv_ns.get(_v)
        if not isinstance(_after, pd.DataFrame):
            _lines.append(f"{{_v}}: no longer a DataFrame after this cell")
            continue
        _lines.append(
            f"{{_v}}: preview on {{len(_b):,}} of {{len(globals()[_v]):,}} rows"
        )
        if len(_after) != len(_b):
            _lines.append(f"  rows: {{len(_b):,}} -> {{len(_after):,}}")
            continue
        _a2 = _after.reset_index(drop=True)
        _b2 = _b.reset_index(drop=True)
        _untouched = []
        _changed_any = False
        for _c in _b2.columns:
            if _c not in _a2.columns:
                _lines.append(f"  column dropped: {{_c!r}}")
                _changed_any = True
                continue
            _mask = _b2[_c].astype(str) != _a2[_c].astype(str)
            _n = int(_mask.sum())
            if _n == 0:
                _untouched.append(_c)
                continue
            _changed_any = True
            _pairs = list(
                zip(
                    _b2[_c][_mask].head(3).astype(str),
                    _a2[_c][_mask].head(3).astype(str),
                )
            )
            _lines.append(_diff.column_change(_c, _n, len(_b2), _pairs))
            if str(_b2[_c].dtype) != str(_a2[_c].dtype):
                _lines.append(f"  dtype: {{_b2[_c].dtype}} -> {{_a2[_c].dtype}}")
        if _untouched:
            _lines.append(f"  {{len(_untouched)}} column(s) untouched")
        if not _changed_any:
            _lines.append("  no cells change in the sampled rows")
    print("\\n".join(_lines))
"""


# the only modules a cell may touch and still be previewed: previews run
# BEFORE the human approves, so anything reaching beyond dataframes (files,
# processes, sockets) must wait for the gate
PREVIEW_ALLOWED_IMPORTS = frozenset(
    {
        "pandas",
        "numpy",
        "re",
        "json",
        "math",
        "statistics",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "unicodedata",
    }
)
_PREVIEW_FORBIDDEN_CALLS = frozenset(
    {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint"}
)


def preview_screen(source: str) -> str:
    """Why this cell must not be previewed, or "" when it is safe to.

    The scratch copy protects the data; it cannot protect the process. A cell
    that imports os, opens files, or reaches for dunders runs only after the
    human says so — the preview degrades to code-only with this reason."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"cell does not parse: {exc.msg}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            module = (
                node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
            )
            root = (module or "").split(".")[0]
            if root not in PREVIEW_ALLOWED_IMPORTS:
                return f"cell imports {root!r}, which reaches beyond dataframes"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _PREVIEW_FORBIDDEN_CALLS
        ):
            return f"cell calls {node.func.id}()"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return "cell reaches for dunder attributes"
    return ""


def preview_cell(names, fix_source: str, rows: int = PREVIEW_ROWS) -> str:
    """R3: what approving this cell would do, computed on sampled scratch
    copies inside the kernel. The live variables are untouched by construction
    — the cell executes in a namespace where each name is bound to a copy —
    so AC3 holds without a revert. A cell that errors on the samples
    (including its own asserts) degrades to a one-line reason. `names` may be
    one variable or several: QUERY cells touch whatever they like."""
    if isinstance(names, str):
        names = [names]
    return PREVIEW_TEMPLATE.format(
        names=repr(list(names)), rows=rows, fix_source=fix_source
    )


def verify_cell(var: str, finding: dict, baseline_columns: list[str]) -> str:
    """Layer-1 verification: signal clear + row invariant + untouched columns.

    For whitespace damage (d06) the signal going quiet is not enough: a repair
    that turns zero-width characters into spaces also silences the detector,
    while corrupting every affected word — 'Bud<ZWSP>weiser' must become
    'Budweiser', never 'Bud weiser'. That disease is therefore anchored to the
    reference repair: the fixed column must equal _ws_tidy of the original,
    which deletes zero-widths by construction."""
    targets = set(finding.get("columns", []))
    untouched = [c for c in baseline_columns if c not in targets]
    reference = ""
    if int(finding.get("disease", 0)) == 6:
        reference = (
            "from analyst_agent.detect import _ws_tidy\n"
            f"for _c in {json.dumps(finding.get('columns', []))}:\n"
            f"    if _c in {var}.columns and _c in _clean_backup.columns:\n"
            "        _m = _clean_backup[_c].notna()\n"
            "        _want = _ws_tidy(_clean_backup[_c][_m].astype(str))\n"
            f"        _got = {var}[_c][_m].astype(str)\n"
            "        assert _got.equals(_want), (\n"
            "            f'column {_c!r} does not match the whitespace "
            "reference repair'\n"
            "        )\n"
        )
    return (
        "from analyst_agent.detect import detect_one\n"
        "import pandas as pd\n"
        "try:\n"
        f"    _v = detect_one({var}, {finding['disease']}, "
        f"{json.dumps(finding.get('columns', []))})\n"
        "except Exception as _exc:\n"
        "    # a crash is 'could not check', not 'the fix failed' — the loop\n"
        "    # must not score the skill for the detector's bug. The cell still\n"
        "    # errors either way: unverified is unverified.\n"
        "    raise RuntimeError(\n"
        "        f'uncheckable: {type(_exc).__name__}: {_exc}'\n"
        "    ) from _exc\n"
        "assert _v is None, f\"signal still fires: {_v['evidence']}\"\n"
        f"{reference}"
        f"{_row_invariant(var, finding)}\n"
        f"for _c in {json.dumps(untouched)}:\n"
        f"    if _c in {var}.columns:\n"
        f"        _h = int(pd.util.hash_pandas_object("
        f"{var}[_c], index=False).sum())\n"
        "        assert _h == _clean_hashes[_c], "
        'f"column {_c!r} changed but was not a fix target"\n'
        '"verified"'
    )


CASE_TEMPLATE = """\
import json as _json
import pandas as pd
from pathlib import Path
_cols = [c for c in {columns} if c in {var}.columns and c in _clean_backup.columns]
if len(_clean_backup) != len({var}) or not _cols:
    # the fix changed the row count, so rows cannot be paired: treat all as sick
    _changed = pd.Series(True, index=_clean_backup.index)
else:
    _before = _clean_backup[_cols].astype(str).reset_index(drop=True)
    _after = {var}[_cols].astype(str).reset_index(drop=True)
    _changed = (_before != _after).any(axis=1)
    _changed.index = _clean_backup.index
_sick = _clean_backup[_changed].head({sick})
_healthy = _clean_backup[~_changed].head({healthy})
_case = pd.concat([_sick, _healthy]).sort_index()
Path({path}).parent.mkdir(parents=True, exist_ok=True)
_case.to_parquet({path})
_json.dumps({{
    "rows": int(len(_case)),
    "sick": int(len(_sick)),
    "healthy": [str(i) for i in _healthy.index],
}})
"""


def case_cell(var: str, finding: dict, path: str) -> str:
    """Freeze the case a fix was born from (R7).

    Sick rows are the ones the fix actually changed — found by diffing the
    pre-fix backup against the live frame, which needs no cooperation from the
    detector. Healthy rows ride along so admission can prove the generalised
    fix leaves them alone.
    """
    return CASE_TEMPLATE.format(
        var=var,
        columns=json.dumps(finding.get("columns", [])),
        path=json.dumps(path),
        sick=CASE_SICK_ROWS,
        healthy=CASE_HEALTHY_ROWS,
    )


ADMISSION_TEMPLATE = """\
import json as _json
import pandas as pd
from analyst_agent.detect import detect_one

{fix_source}

_case = pd.read_parquet({path})
_cols = {columns}
_before = detect_one(_case, {disease}, _cols)
assert _before is not None, (
    "the frozen case no longer trips the detector, so passing proves nothing"
)
_out = fix(_case.copy(), list(_cols))
assert isinstance(_out, pd.DataFrame), "fix(df, columns) must return a DataFrame"
_after = detect_one(_out, {disease}, _cols)
assert _after is None, (
    "the generalised fix leaves the signal firing: " + str(_after["evidence"])
)
for _c in [c for c in _case.columns if c not in _cols]:
    assert _out[_c].astype(str).tolist() == _case[_c].astype(str).tolist(), (
        "column " + repr(_c) + " changed but was not a fix target"
    )
_healthy = [i for i in {healthy} if i in _out.index.astype(str).tolist()]
if _healthy:
    _mask = _out.index.astype(str).isin(_healthy)
    _lhs = _out.loc[_mask, _cols].astype(str).values.tolist()
    _rhs = _case.loc[_mask, _cols].astype(str).values.tolist()
    assert _lhs == _rhs, (
        "the fix altered rows that were never broken — it heals the sick by "
        "harming the healthy"
    )
_json.dumps({{"admitted": True, "rows": int(len(_case))}})
"""


def admission_cell(path: str, fix_source: str, finding: dict, healthy) -> str:
    """Admission gate 1: the generalised fix, re-run against the real case (R7).

    Three claims, each of which a plausible-looking generalisation can fail: the
    case still exhibits the disease (so passing means something), the fix clears
    it, and nothing else moves.
    """
    return ADMISSION_TEMPLATE.format(
        fix_source=fix_source,
        path=json.dumps(path),
        columns=json.dumps(finding.get("columns", [])),
        disease=finding["disease"],
        healthy=json.dumps([str(i) for i in healthy]),
    )


def skill_apply_cell(var: str, fix_source: str, columns: list) -> str:
    """Apply a retrieved skill to the live variable. No asserts of its own: the
    skill's fix is trusted no further than the P1 verify cell that follows."""
    return f'{fix_source}\n{var} = fix({var}, {json.dumps(columns)})\n"applied"'


SKILL_TEST_TEMPLATE = """\
import json as _json

{fix_source}

_ns = {{"fix": fix}}
exec(compile({test_source}, "test_fix.py", "exec"), _ns)
_tests = sorted(k for k in _ns if k.startswith("test_") and callable(_ns[k]))
assert _tests, "the shipped test defines no test_ functions"
for _name in _tests:
    _ns[_name]()
_json.dumps({{"tests": len(_tests)}})
"""


def skill_test_cell(fix_source: str, test_source: str) -> str:
    """Admission gate 2: run the test that ships with the skill (R8).

    `fix` is injected into the test's namespace rather than imported, so the
    test never depends on where the skill folder happens to sit on disk.
    """
    return SKILL_TEST_TEMPLATE.format(
        fix_source=fix_source, test_source=json.dumps(test_source)
    )


FAMILY_LOAD_TEMPLATE = """\
import csv as _csv
import glob as _glob
import json as _json
from pathlib import Path as _Path

import pandas as pd

{name} = {{}}
_meta = []
for _p in sorted(_glob.glob({pattern})):
    with open(_p, encoding="utf-8-sig", errors="replace") as _fh:
        _head = _fh.read(8192)
    try:
        _sep = _csv.Sniffer().sniff(_head, delimiters=",;\\t|").delimiter
    except _csv.Error:
        _sep = ","
    _df = pd.read_csv(_p, sep=_sep, encoding="utf-8-sig", low_memory=False)
    _key = _Path(_p).stem
    {name}[_key] = _df
    _meta.append({{
        "slice": _key,
        "path": _p,
        "rows": int(len(_df)),
        "columns": [str(c) for c in _df.columns],
        "sep": _sep,
    }})
print(_json.dumps(_meta))
"""


def family_load_cell(name: str, pattern: str) -> str:
    """Load a glob of same-family files into one dict variable (P2.5 R1).

    The delimiter is sniffed per file rather than assumed: Vancouver's "csv"
    exports are semicolon-delimited with a BOM, and guessing wrong turns thirty
    columns into one — a failure that looks like clean data.
    """
    return FAMILY_LOAD_TEMPLATE.format(name=name, pattern=json.dumps(pattern))


FAMILY_BASELINE_TEMPLATE = """\
import json as _json
_family_backup = {{k: v.copy() for k, v in {name}.items()}}
_family_rows = {{k: int(len(v)) for k, v in {name}.items()}}
_family_nonnull = {{}}
for _k, _v in {name}.items():
    _family_nonnull[_k] = {{str(c): int(_v[c].notna().sum()) for c in _v.columns}}
_json.dumps(sorted(_family_rows))
"""


def family_baseline_cell(name: str) -> str:
    return FAMILY_BASELINE_TEMPLATE.format(name=name)


def family_revert_cell(name: str) -> str:
    return f"{name} = {{k: v.copy() for k, v in _family_backup.items()}}\n'reverted'"


FAMILY_VERIFY_TEMPLATE = """\
import json as _json

_keys = sorted({name})
assert _keys == sorted(_family_rows), "harmonizing must not add or drop slices"
_shapes = {{k: [str(c) for c in {name}[k].columns] for k in _keys}}
_canonical = _shapes[_keys[0]]
for _k in _keys:
    assert _shapes[_k] == _canonical, (
        "slice " + repr(_k) + " still has a different column list: "
        + str(sorted(set(_shapes[_k]) ^ set(_canonical))[:6])
    )
for _k in _keys:
    assert int(len({name}[_k])) == _family_rows[_k], (
        "slice " + repr(_k) + " changed row count; harmonizing renames columns, "
        "it does not filter rows"
    )
_lost = []
for _k in _keys:
    _after = {{str(c): int({name}[_k][c].notna().sum()) for c in {name}[_k].columns}}
    _before_total = sum(_family_nonnull[_k].values())
    if sum(_after.values()) < _before_total:
        _lost.append((_k, _before_total - sum(_after.values())))
assert not _lost, (
    "harmonizing dropped populated cells: " + str(_lost[:4])
    + " — a rename that loses values is a data loss, not a mapping"
)
_json.dumps({{"slices": len(_keys), "columns": len(_canonical)}})
"""


def family_verify_cell(name: str) -> str:
    """One canonical schema, no rows moved, no populated cell lost (P2.5 R5)."""
    return FAMILY_VERIFY_TEMPLATE.format(name=name)
