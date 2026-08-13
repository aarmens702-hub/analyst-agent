"""Rendering a change so a person can see it (P5 R2).

The gate currently shows the code and asks whether to run it, which means the
operator has to execute pandas in their head. These are the pieces that let it
show the consequence instead.

Computing a diff is the easy half. The half that matters is marking only what
actually moved — '12.0 oz' -> '12.0' should point at ' oz', not print two
whole values and leave the reader to spot the difference. difflib gives us
that; no dependency and nothing to port.
"""

import re
from difflib import SequenceMatcher

# split on boundaries a reader recognises, keeping the separators so a rebuilt
# string is byte-identical to the original
_TOKENS = re.compile(r"(\s+|[^\w\s]|\w+)")


def _tokens(value: str) -> list[str]:
    return [t for t in _TOKENS.split(value) if t]


def word_segments(before: str, after: str) -> list[tuple[str, str]]:
    """Split a value change into ("equal" | "delete" | "insert", text) runs.

    Adjacent runs of the same kind are merged, so a caller marking a change
    highlights one span rather than a stutter of neighbouring tokens.
    """
    old, new = _tokens(str(before)), _tokens(str(after))
    segments: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, old, new).get_opcodes():
        if op == "equal":
            segments.append(("equal", "".join(old[i1:i2])))
        else:  # replace shows as a delete followed by an insert
            if i1 != i2:
                segments.append(("delete", "".join(old[i1:i2])))
            if j1 != j2:
                segments.append(("insert", "".join(new[j1:j2])))
    merged: list[tuple[str, str]] = []
    for op, text in segments:
        if merged and merged[-1][0] == op:
            merged[-1] = (op, merged[-1][1] + text)
        else:
            merged.append((op, text))
    return merged


DELETE_WRAP = ("[-", "-]")
INSERT_WRAP = ("{+", "+}")


def inline(before: str, after: str) -> str:
    """One line showing a value change, with only the moved part marked.

    Plain text on purpose: it has to survive a terminal, a markdown report, and
    a browser without three renderers. The markers are the git-worddiff ones,
    which a reader already knows how to read.
    """
    parts = []
    for op, text in word_segments(before, after):
        if op == "equal":
            parts.append(text)
        elif op == "delete":
            parts.append(f"{DELETE_WRAP[0]}{text}{DELETE_WRAP[1]}")
        else:
            parts.append(f"{INSERT_WRAP[0]}{text}{INSERT_WRAP[1]}")
    return "".join(parts)


SAMPLE_LIMIT = 3


def column_change(
    name: str,
    changed: int,
    total: int,
    samples,
    untouched=(),
    limit: int = SAMPLE_LIMIT,
) -> str:
    """What approving this fix would do to one column.

    Ordered the way the question is actually asked: how much moves, what it
    looks like, and what was left alone. That last line matters more than it
    reads — a fix that heals a column by damaging its neighbours is the failure
    the frozen-case gate exists to catch, and at the gate the operator
    currently has no way to see it coming.

    Samples are truncated. This renders a preview, never a data dump.
    """
    lines = [f"{name}: {changed:,} of {total:,} cells change"]
    seen: list[str] = []
    for before, after in samples:
        rendered = f"  {inline(str(before), str(after))}"
        if rendered not in seen:
            seen.append(rendered)
        if len(seen) == limit:
            break
    lines += seen
    extra = len(list(samples)) - len(seen)
    if extra > 0:
        lines.append(f"  … {extra:,} more")
    if untouched:
        lines.append(f"  {len(untouched)} columns untouched")
    return "\n".join(lines)
