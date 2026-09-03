"""`crivo diagnose <file>` — what is wrong with this data, for free.

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

from crivo.detect import SINGLE_FRAME, detect_all
from crivo.readers import _sniff

GRADE_NOTE = {
    "AUTO": "safe to fix automatically",
    "GATE": "fix with a human check",
    "HUMAN": "needs a judgement call",
}


_SAMPLE_BYTES = 8192


def _text_sample(path: Path, n: int = _SAMPLE_BYTES) -> bytes:
    """Up to `n` *decompressed* bytes for delimiter/encoding sniffing —
    transparent to .gz/.bz2/.xz/.zip so a compressed CSV sniffs exactly like a
    plain one instead of reading gzip magic bytes as text."""
    suffix = path.suffix.lower()
    if suffix == ".gz":
        import gzip

        with gzip.open(path, "rb") as fh:
            return fh.read(n)
    if suffix == ".bz2":
        import bz2

        with bz2.open(path, "rb") as fh:
            return fh.read(n)
    if suffix == ".xz":
        import lzma

        with lzma.open(path, "rb") as fh:
            return fh.read(n)
    if suffix == ".zip":
        import zipfile

        with zipfile.ZipFile(path) as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            if not members:
                return b""
            with zf.open(members[0]) as fh:
                return fh.read(n)
    return path.read_bytes()[:n]


def load(path, **kwargs) -> pd.DataFrame:
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

    Compression is transparent: .gz/.bz2/.xz/.zip CSVs sniff and read exactly
    like their plain form (pandas infers the codec from the path). Extra keyword
    args are forwarded to the pandas reader and override the sniffed defaults.
    """
    path = Path(path)
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path, **kwargs)
    sample = _text_sample(path)
    if sample[:2] in (b"\xff\xfe", b"\xfe\xff"):
        # a utf-16 byte-order mark: the cp1252 fallback below would "succeed"
        # on these bytes and mangle every header ('ÿþi'), so sniff it first
        head = sample.decode("utf-16", errors="replace")
        counts = {sep: head.count(sep) for sep in (",", ";", "\t", "|")}
        sep = max(counts, key=counts.get) if any(counts.values()) else ","
        read_kwargs = {
            "sep": sep,
            "encoding": "utf-16",
            "keep_default_na": False,
            "low_memory": False,
        }
        read_kwargs.update(kwargs)
        frame = pd.read_csv(path, **read_kwargs)
        frame.attrs["encoding"] = "utf-16"
        return frame
    try:
        # a multibyte char cut at the 8KB boundary is not evidence against
        # utf-8; a failure anywhere earlier is — but only forgive it when the
        # sample was actually truncated (a short file's bad byte is real cp1252)
        head, encoding = sample.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError as exc:
        if exc.start >= len(sample) - 3 and len(sample) >= _SAMPLE_BYTES:
            head, encoding = sample[: exc.start].decode("utf-8-sig"), "utf-8-sig"
        else:
            head, encoding = sample.decode("cp1252", errors="replace"), "cp1252"
    counts = {sep: head.count(sep) for sep in (",", ";", "\t", "|")}
    sep = max(counts, key=counts.get) if any(counts.values()) else ","
    read_kwargs = {
        "sep": sep,
        "encoding": encoding,
        "keep_default_na": False,
        "low_memory": False,
    }
    read_kwargs.update(kwargs)  # the caller's options win over the sniffed ones
    # the preamble sniffer never runs when the caller pinned the layout
    user_pinned = "skiprows" in kwargs or "header" in kwargs
    try:
        frame = pd.read_csv(path, **read_kwargs)
    except pd.errors.ParserError:
        if user_pinned:
            raise
        retried = _preamble_retry(path, head, read_kwargs, encoding)
        if retried is None:
            raise
        return retried
    frame.attrs["encoding"] = encoding
    if user_pinned or not _sniff.is_degenerate(frame.columns):
        return frame
    retried = _preamble_retry(path, head, read_kwargs, encoding)
    if retried is not None:
        return retried
    if _sniff.unnamed_flood(frame.columns):
        # >50% Unnamed columns is a shape no real header produces, and no
        # consistent table exists below — garbage out would be a lie
        raise ValueError(
            f"{path.name}: no consistent table found below the junk header; "
            f"first lines: {_sniff.preview(head)}"
        )
    return frame  # a genuine single-column file is legal; leave it alone


def _preamble_retry(path, head, read_kwargs, encoding):
    """Re-read past a metadata preamble when a stable table shape exists
    below it (arc P2-polish, group C). Returns the reframed table stamped
    with what was skipped, or None when there is nothing better to do."""
    start = _sniff.find_table_start(head)
    if start is None or start[0] == 0:
        return None
    skiprows, sep = start
    retry_kwargs = dict(read_kwargs, skiprows=skiprows, sep=sep)
    try:
        frame = pd.read_csv(path, **retry_kwargs)
    except pd.errors.ParserError:
        return None
    if _sniff.is_degenerate(frame.columns):
        return None
    frame.attrs["encoding"] = encoding
    frame.attrs["preamble_rows"] = skiprows
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


# (colour, one-line meaning) per grade, for the styled console report
_GRADE_STYLE = {
    "AUTO": ("green", "safe to auto-fix"),
    "GATE": ("yellow", "fix with a check"),
    "HUMAN": ("red", "needs a judgement call"),
}


def render_console(path, frame: pd.DataFrame, result: dict, console=None) -> None:
    """The report, styled for a terminal: colour by grade, dim the evidence,
    a rule under the filename. rich degrades to clean plain text the moment
    stdout is not a terminal (a pipe, a file, a test), so `--json` and piped
    output are never touched by this path."""
    from rich.console import Console, Group
    from rich.padding import Padding
    from rich.rule import Rule
    from rich.text import Text

    console = console or Console()
    findings = result["findings"]
    fixable = [f for f in findings if not f["indicator"]]
    flagged = [f for f in findings if f["indicator"]]
    broken = result.get("broken", {})

    head = Text()
    head.append(Path(str(path)).name, style="bold")
    head.append(f"   {len(frame):,} rows × {len(frame.columns)} columns", style="dim")
    console.print()
    console.print(Rule(head, style="cyan", align="left"))

    if frame.attrs.get("encoding") not in (None, "utf-8-sig"):
        console.print(
            Text(
                f"⚠  not utf-8 — read as {frame.attrs['encoding']} "
                "(a Windows-era export; check accented text survived)",
                style="yellow",
            )
        )

    summary = Text("  ")
    summary.append(f"{len(fixable)} fixable", style="bold cyan")
    summary.append("  ·  ", style="dim")
    summary.append(f"{len(flagged)} flagged", style="bold yellow")
    summary.append("  ·  ", style="dim")
    summary.append(f"{len(result['clear'])} signals clear", style="bold green")
    if broken:
        summary.append("  ·  ", style="dim")
        summary.append(f"{len(broken)} could not run", style="bold red")
    console.print()
    console.print(summary)
    console.print()

    for f in fixable:
        colour, note = _GRADE_STYLE.get(f["grade"], ("white", f["grade"]))
        where = ", ".join(f["columns"]) or "whole table"
        title = Text("  ● ", style=colour)
        title.append(f"d{f['disease']:02d}  ", style="dim")
        title.append(f["slug"], style=f"bold {colour}")
        title.append(f"  [{where}]", style="cyan")
        tail = Text()
        tail.append(note, style=colour)
        tail.append(f"  ·  confidence {f['confidence']:.2f}", style="dim")
        console.print(
            Group(
                title,
                Padding(Text(f["evidence"], style="dim"), (0, 0, 0, 6)),
                Padding(tail, (0, 0, 0, 6)),
            )
        )
        console.print()

    for f in flagged:
        title = Text("  ⚠ ", style="yellow")
        title.append(f"d{f['disease']:02d}  ", style="dim")
        title.append(f["slug"], style="bold yellow")
        title.append("  — flagged, never auto-fixed", style="dim")
        console.print(title)
        console.print(Padding(Text(f["evidence"], style="dim"), (0, 0, 1, 6)))

    for disease, why in sorted(broken.items()):
        console.print(
            Text(f"  ✗ d{int(disease):02d}  did not run — {why}", style="red")
        )

    if not findings:
        console.print(Text("  nothing found — the file is clean.", style="green"))
        console.print()

    console.print(Text(f"  checked and clean: {_ranges(result['clear'])}", style="dim"))
    console.print(
        Text(
            f"  {len(SINGLE_FRAME)} signals run on every file; "
            "absence here is a checked claim, not a silence",
            style="dim italic",
        )
    )
    console.print()


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
