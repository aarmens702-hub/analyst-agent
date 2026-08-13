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


def verify_cell(var: str, finding: dict, baseline_columns: list[str]) -> str:
    """Layer-1 verification: signal clear + row invariant + untouched columns."""
    targets = set(finding.get("columns", []))
    untouched = [c for c in baseline_columns if c not in targets]
    return (
        "from analyst_agent.detect import detect_one\n"
        "import pandas as pd\n"
        f"_v = detect_one({var}, {finding['disease']}, "
        f"{json.dumps(finding.get('columns', []))})\n"
        "assert _v is None, f\"signal still fires: {_v['evidence']}\"\n"
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
