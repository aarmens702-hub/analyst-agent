"""Kernel-namespace snapshot cells (P5 R8, upgraded arc W3/H7): survive a
death with the fixes.

`_restart_and_replay` replays only the original load cells, so every verified
fix applied since the load dies with the kernel. These cells run *inside* the
kernel — the same stdout-marker idiom `detect_all` and the case freezer use —
and are deliberately best-effort: one unpicklable or oversized variable costs
that variable, never the whole snapshot. The write is atomic (tmp + rename),
because a half-written state file restored into a fresh kernel would be worse
than none.

W3/H7 upgrades (prime-agent backlog §2, adapted): an AGGREGATE cap so ten big
frames can't write an unbounded state file; a JSON manifest beside the blob
(names, sizes, skips, serializer — auditable without unpickling anything);
dill used transparently when the kernel image has it, pickle otherwise, and
restore mirrors the same fallback. Deliberate divergence: the backlog's
16MiB/var is for REPL-sized state — our kernel's main state IS the dataset,
so the per-variable cap stays 64MiB and the aggregate cap is the new safety.
"""

# one variable above this is skipped with a reason, not a failure
SIZE_CAP_BYTES = 64 * 1024 * 1024
# the whole snapshot stops growing past this; later variables skip with a reason
AGGREGATE_CAP_BYTES = 256 * 1024 * 1024

SNAPSHOT_TEMPLATE = """\
import json as _json
from pathlib import Path as _Path

try:
    import dill as _ser  # broader coverage when the kernel image carries it
    _ser_name = "dill"
except Exception:
    import pickle as _ser
    _ser_name = "pickle"

_saved, _skipped, _blobs, _sizes, _total = [], {{}}, {{}}, {{}}, 0
for _name, _value in list(globals().items()):
    if _name.startswith("_") or _name in ("In", "Out", "get_ipython", "exit", "quit"):
        continue
    if type(_value).__name__ in ("module", "function", "type", "method"):
        continue
    try:
        _blob = _ser.dumps(_value, protocol=4)
    except Exception as _exc:
        _skipped[_name] = f"{{type(_exc).__name__}}: {{_exc}}"[:120]
        continue
    if len(_blob) > {cap}:
        _skipped[_name] = f"over size cap ({{len(_blob):,}} bytes)"
        continue
    if _total + len(_blob) > {aggregate_cap}:
        _skipped[_name] = (
            f"over aggregate cap ({{_total + len(_blob):,}} bytes total)"
        )
        continue
    _blobs[_name] = _blob
    _sizes[_name] = len(_blob)
    _total += len(_blob)
    _saved.append(_name)

_dest = _Path({path!r})
_tmp = _dest.with_suffix(".tmp")
with _tmp.open("wb") as _fh:
    _ser.dump(_blobs, _fh, protocol=4)
_tmp.replace(_dest)
_manifest = {{
    "serializer": _ser_name,
    "saved": _saved,
    "skipped": _skipped,
    "sizes": _sizes,
    "total_bytes": _total,
}}
_mtmp = _dest.with_suffix(".manifest.tmp")
_mtmp.write_text(_json.dumps(_manifest))
_mtmp.replace(_Path(str(_dest) + ".manifest.json"))
print(_json.dumps({{"saved": _saved, "skipped": _skipped}}))
"""

RESTORE_TEMPLATE = """\
import json as _json
from pathlib import Path as _Path

try:
    import dill as _ser  # mirrors the snapshot side's fallback
except Exception:
    import pickle as _ser

_restored, _failed = [], {{}}
with _Path({path!r}).open("rb") as _fh:
    _blobs = _ser.load(_fh)
for _name, _blob in _blobs.items():
    try:
        globals()[_name] = _ser.loads(_blob)
        _restored.append(_name)
    except Exception as _exc:
        _failed[_name] = f"{{type(_exc).__name__}}: {{_exc}}"[:120]
print(_json.dumps({{"restored": _restored, "failed": _failed}}))
"""


def snapshot_cell(
    path: str,
    cap_bytes: int = SIZE_CAP_BYTES,
    aggregate_cap_bytes: int = AGGREGATE_CAP_BYTES,
) -> str:
    return SNAPSHOT_TEMPLATE.format(
        path=path, cap=cap_bytes, aggregate_cap=aggregate_cap_bytes
    )


def restore_cell(path: str) -> str:
    return RESTORE_TEMPLATE.format(path=path)
