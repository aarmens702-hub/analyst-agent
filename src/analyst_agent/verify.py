"""Builders for the kernel cells that make a fix *verified* (P1 R9-R10).

All functions return code strings executed in the kernel by the CLEAN flow:
baseline snapshot, per-fix verification (layer 1: detector re-run + row
invariant + untouched-column hashes), and revert. Layer 2 (the model's own
asserts) lives inside the fix cell itself.
"""

import json

# diseases whose fixes legitimately change the row count (spec R9)
ROW_DELTA_EXACT = {9: "dup_count", 21: "rollup_count"}  # stats key with the delta
ROW_DELTA_BOUNDED = {10: "pair_count"}  # merges ≤ candidate pairs

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
