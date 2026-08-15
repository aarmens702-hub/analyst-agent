"""`analyst-agent diagnose <file>` — what is wrong with this data, for free.

Every other surface in this project needs an API key, a running kernel, and a
decision to let an agent touch your data. This one needs a file. It runs the
same 21 single-file signals the CLEAN mode runs, in-process, with no model
involved anywhere — so a stranger can point it at their own CSV and see whether
the diagnosis is any good before trusting anything else.

It reports what it found, what it deliberately will not fix, what it checked
and found clean, and what failed to run. That last pair is the part other
profilers skip: absence is only a claim if something says it was checked.
"""

import json
from pathlib import Path

import pandas as pd

from analyst_agent.detect import SINGLE_FRAME, detect_all

GRADE_NOTE = {
    "AUTO": "safe to fix automatically",
    "GATE": "fix with a human check",
    "HUMAN": "needs a judgement call",
}


def load(path) -> pd.DataFrame:
    """Read a file the way the agent would.

    keep_default_na=False on purpose: pandas turns "N/A" into NaN by default,
    which silently repairs one of the diseases before it can be reported.
    Delimiter is sniffed, because a .csv is not always comma-separated — the
    Vancouver exports are semicolons, and guessing wrong turns thirty columns
    into one. Encoding falls back utf-8 -> cp1252, because a real Windows
    export (HM Treasury's spend files carry a literal £, byte 0xA3) otherwise
    dies before a single detector runs — but the fallback is stamped on the
    frame so render() can say it out loud: a silently switched encoding is a
    claim the report never made.
    """
    path = Path(path)
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    sample = path.read_bytes()[:8192]
    try:
        # a multibyte char cut at the 8KB boundary is not evidence against
        # utf-8; a failure anywhere earlier is
        head, encoding = sample.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError as exc:
        if exc.start >= len(sample) - 3:
            head, encoding = sample[: exc.start].decode("utf-8-sig"), "utf-8-sig"
        else:
            head, encoding = sample.decode("cp1252", errors="replace"), "cp1252"
    counts = {sep: head.count(sep) for sep in (",", ";", "\t", "|")}
    sep = max(counts, key=counts.get) if any(counts.values()) else ","
    frame = pd.read_csv(
        path, sep=sep, encoding=encoding, keep_default_na=False, low_memory=False
    )
    frame.attrs["encoding"] = encoding
    return frame


def report(path, as_json: bool = False) -> str:
    """The findings for one file, rendered for a person or for a machine."""
    frame = load(path)
    result = detect_all(frame, Path(path).name)
    if as_json:
        return json.dumps(
            {"file": str(path), "rows": len(frame), "columns": len(frame.columns)}
            | result,
            indent=2,
        )
    return render(path, frame, result)


def render(path, frame: pd.DataFrame, result: dict) -> str:
    findings = result["findings"]
    fixable = [f for f in findings if not f["indicator"]]
    flagged = [f for f in findings if f["indicator"]]
    broken = result.get("broken", {})

    lines = [
        f"{path} — {len(frame):,} rows × {len(frame.columns)} columns",
        "",
    ]
    if frame.attrs.get("encoding") not in (None, "utf-8-sig"):
        lines += [
            (
                f"  ⚠  not utf-8: read as {frame.attrs['encoding']} "
                "(a Windows-era export; check accented text survived)"
            ),
            "",
        ]
    lines += [
        f"  {len(fixable)} fixable · {len(flagged)} flagged · "
        f"{len(result['clear'])} signals clear"
        + (f" · {len(broken)} could not run" if broken else ""),
        "",
    ]
    for f in fixable:
        where = ", ".join(f["columns"]) or "whole table"
        lines += [
            f"  d{f['disease']:02d}  {f['slug']}  [{where}]",
            f"        {f['evidence']}",
            (
                f"        {GRADE_NOTE.get(f['grade'], f['grade'])}"
                f" · confidence {f['confidence']:.2f}"
            ),
            "",
        ]
    for f in flagged:
        lines += [
            f"  ⚠  d{f['disease']:02d}  {f['slug']}  — flagged, never auto-fixed",
            f"        {f['evidence']}",
            "",
        ]
    for disease, why in sorted(broken.items()):
        lines += [f"  ✗  d{int(disease):02d}  did not run — {why}", ""]

    if not findings:
        lines += ["  nothing found.", ""]
    lines += [
        f"  checked and clean: {_ranges(result['clear'])}",
        f"  ({len(SINGLE_FRAME)} signals run on every file; absence is a checked",
        "   claim here, not a silence)",
    ]
    return "\n".join(lines)


def _ranges(ids) -> str:
    """Compact a sorted id list: 1-4, 7, 9-11."""
    ids = sorted(ids)
    if not ids:
        return "none"
    spans, start, prev = [], ids[0], ids[0]
    for current in ids[1:] + [None]:
        if current != prev + 1:
            spans.append(str(start) if start == prev else f"{start}-{prev}")
            start = current
        prev = current
    return ", ".join(spans)
