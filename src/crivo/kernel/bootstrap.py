"""Bootstrap cell source, executed at kernel start and after every restart (R16).

Everything defined here is underscore-prefixed so the registry probe (which
skips underscored names) never lists its own machinery.
"""

BOOTSTRAP_SOURCE = '''
import matplotlib as _aa_mpl
_aa_mpl.use("Agg")
get_ipython().run_line_magic("matplotlib", "inline")

import pandas as _aa_pd
_aa_pd.set_option("display.max_rows", 30)
_aa_pd.set_option("display.max_columns", 20)
_aa_pd.set_option("display.width", 200)


def _analyst_registry_json():
    """Raw facts about user variables (R12): name, type, shape/len, mem_mb."""
    import json as _json
    import sys as _sys
    import types as _types

    _skip = {"In", "Out", "exit", "quit", "open", "get_ipython"}
    entries, omitted = [], 0
    for _name, _val in list(get_ipython().user_ns.items()):
        if _name.startswith("_") or _name in _skip:
            continue
        if isinstance(_val, _types.ModuleType) or callable(_val):
            continue
        _entry = {"name": _name, "type": type(_val).__name__}
        _remote = getattr(_val, "attrs", {})
        if isinstance(_remote, dict) and isinstance(_remote.get("remote"), dict):
            # R12: a frame load_url stamped must ground in lineage, so the
            # stamp rides the registry up to the host
            _entry["remote"] = _remote["remote"]
        _shape = getattr(_val, "shape", None)
        if _shape is not None:
            _entry["shape"] = list(_shape)
        elif hasattr(_val, "__len__"):
            try:
                _entry["len"] = len(_val)
            except TypeError:
                pass
        try:
            if hasattr(_val, "memory_usage"):
                _mem = _val.memory_usage(deep=True)
                _mem = int(_mem.sum()) if hasattr(_mem, "sum") else int(_mem)
            else:
                _mem = _sys.getsizeof(_val)
            _entry["mem_mb"] = round(_mem / 1048576, 2)
        except Exception:
            pass
        if len(entries) < 50:
            entries.append(_entry)
        else:
            omitted += 1
    return _json.dumps({"entries": entries, "omitted": omitted})
'''
