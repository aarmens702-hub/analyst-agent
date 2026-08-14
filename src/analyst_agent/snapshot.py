"""Kernel-namespace snapshot cells (P5 R8): survive a death with the fixes.

`_restart_and_replay` replays only the original load cells, so every verified
fix applied since the load dies with the kernel. These cells run *inside* the
kernel — the same stdout-marker idiom `detect_all` and the case freezer use —
and are deliberately best-effort: one unpicklable or oversized variable costs
that variable, never the whole snapshot. The write is atomic (tmp + rename),
because a half-written state file restored into a fresh kernel would be worse
than none.

Written fresh for this project; the idea of per-variable, size-capped,
best-effort kernel state capture is validated by prime-agent's equivalent.
"""

# one variable above this is skipped with a reason, not a failure
SIZE_CAP_BYTES = 64 * 1024 * 1024

SNAPSHOT_TEMPLATE = """\
import json as _json
import pickle as _pickle
from pathlib import Path as _Path

_saved, _skipped, _blobs = [], {{}}, {{}}
for _name, _value in list(globals().items()):
    if _name.startswith("_") or _name in ("In", "Out", "get_ipython", "exit", "quit"):
        continue
    if type(_value).__name__ in ("module", "function", "type", "method"):
        continue
    try:
        _blob = _pickle.dumps(_value, protocol=_pickle.HIGHEST_PROTOCOL)
    except Exception as _exc:
        _skipped[_name] = f"{{type(_exc).__name__}}: {{_exc}}"[:120]
        continue
    if len(_blob) > {cap}:
        _skipped[_name] = f"over size cap ({{len(_blob):,}} bytes)"
        continue
    _blobs[_name] = _blob
    _saved.append(_name)

_dest = _Path({path!r})
_tmp = _dest.with_suffix(".tmp")
with _tmp.open("wb") as _fh:
    _pickle.dump(_blobs, _fh, protocol=_pickle.HIGHEST_PROTOCOL)
_tmp.replace(_dest)
print(_json.dumps({{"saved": _saved, "skipped": _skipped}}))
"""

RESTORE_TEMPLATE = """\
import json as _json
import pickle as _pickle
from pathlib import Path as _Path

_restored, _failed = [], {{}}
with _Path({path!r}).open("rb") as _fh:
    _blobs = _pickle.load(_fh)
for _name, _blob in _blobs.items():
    try:
        globals()[_name] = _pickle.loads(_blob)
        _restored.append(_name)
    except Exception as _exc:
        _failed[_name] = f"{{type(_exc).__name__}}: {{_exc}}"[:120]
print(_json.dumps({{"restored": _restored, "failed": _failed}}))
"""


def snapshot_cell(path: str, cap_bytes: int = SIZE_CAP_BYTES) -> str:
    return SNAPSHOT_TEMPLATE.format(path=path, cap=cap_bytes)


def restore_cell(path: str) -> str:
    return RESTORE_TEMPLATE.format(path=path)
