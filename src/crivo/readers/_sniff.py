"""Preamble sniffing for the csv family (P2-polish, Group C).

Bank/ERP exports open with metadata lines ("Account: 1234", "Export date:
..."), often a blank, then the real header. The delimiter sniff and header
pick feed on the preamble, so the frame comes back as garbage — or the
tokenizer dies. Conservative by contract: `checkup.load` engages this ONLY
when the normal parse failed or came back degenerate, and an explicit
skiprows=/header= from the caller means it never runs at all. Both public
entry points (crivo.read's csv route and crivo.diagnose's load) share that
one load path, so this is the single brain for both.
"""

_DELIMS = (",", ";", "\t", "|")


def find_table_start(head: str) -> tuple[int, str] | None:
    """(line_index, delimiter) of the first line where a delimiter's count
    stabilizes — the same count >= 1 on >= 3 consecutive lines — or None when
    no consistent table shape exists in the sample. Index 0 means the table
    starts at the top: nothing to skip.

    Two passes: Excel-exported CSVs pad their metadata lines to the full
    delimiter count ("Report,,,"), so count stability alone starts the table
    at the padding. A real header has every field filled — prefer the first
    stable line whose fields are all non-empty, fall back to bare count
    stability only when no such line exists."""
    lines = head.splitlines()

    def _stable_at(i: int, require_filled: bool) -> str | None:
        for delim in _DELIMS:
            count = lines[i].count(delim)
            if count < 1:
                continue
            if not all(lines[i + k].count(delim) == count for k in (1, 2)):
                continue
            if require_filled and not all(
                field.strip() for field in lines[i].split(delim)
            ):
                continue
            return delim
        return None

    for require_filled in (True, False):
        for i in range(len(lines) - 2):
            delim = _stable_at(i, require_filled)
            if delim is not None:
                return i, delim
    return None


def is_degenerate(columns) -> bool:
    """A parse that smells like the header was junk: a single column, or a
    flood of pandas' Unnamed placeholders."""
    names = [str(c) for c in columns]
    if len(names) <= 1:
        return True
    unnamed = sum(1 for n in names if n.startswith("Unnamed:"))
    return unnamed > len(names) / 2


def unnamed_flood(columns) -> bool:
    """>50% Unnamed columns — a shape no legitimate header produces."""
    names = [str(c) for c in columns]
    return (
        bool(names)
        and sum(1 for n in names if n.startswith("Unnamed:")) > len(names) / 2
    )


def preview(head: str, n: int = 5) -> str:
    """The first lines, repr'd and truncated, for error messages."""
    lines = head.splitlines()[:n]
    return "; ".join(repr(line[:60]) for line in lines)
