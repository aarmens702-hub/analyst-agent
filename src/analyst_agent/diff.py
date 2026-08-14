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
    # autojunk=False deliberately: difflib's default calls any token appearing
    # in >1% of a sequence "junk" once the sequence passes 200 elements, which
    # for a packed or delimited cell — the values a preview most needs to
    # render — collapses the whole thing into one replace. That is precisely
    # the two-whole-values output this module exists to avoid. Values are
    # length-capped before they reach here, which bounds the quadratic cost.
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
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
VALUE_CHARS = 60  # per marked run, matching detect._samples' truncation discipline
CONTEXT_CHARS = 24  # per unchanged run: enough to orient, not a data dump
DIFF_BOUND = 2000  # chars of each value the matcher sees; past it, we say so


def _render_change(before, after) -> str:
    """One bounded line with only the moved part marked.

    Diff first, THEN clip: clipping both values to 60 chars before diffing
    rendered any change past character 59 as a line in which nothing appears
    to change — the exact output this module exists to prevent. Unchanged runs
    are squeezed to their ends, marked runs are capped, and a change the
    matcher's own bound hides is said out loud rather than omitted. Newlines
    collapse so one sample stays one line."""
    before_line = " ".join(str(before).splitlines())
    after_line = " ".join(str(after).splitlines())
    parts: list[str] = []
    marked = False
    for op, text in word_segments(before_line[:DIFF_BOUND], after_line[:DIFF_BOUND]):
        if op == "equal":
            if len(text) > CONTEXT_CHARS:
                text = text[:11] + "…" + text[-11:]
            parts.append(text)
            continue
        marked = True
        if len(text) > VALUE_CHARS:
            text = text[: VALUE_CHARS - 1] + "…"
        wrap = DELETE_WRAP if op == "delete" else INSERT_WRAP
        parts.append(f"{wrap[0]}{text}{wrap[1]}")
    if not marked and before_line != after_line:
        parts.append(" ⋯ (change beyond preview bound)")
    return "".join(parts)


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
    shown = 0
    for before, after in samples:
        # bound the work, not the distinct output: a column of 2.4M identical
        # changes must not run 2.4M matcher passes to render one line
        shown += 1
        rendered = f"  {_render_change(before, after)}"
        if rendered not in seen:
            seen.append(rendered)
        if shown >= limit:
            break
    lines += seen
    # derived from the count we were given, never by walking `samples` again —
    # the caller's zip() is one-shot, and re-reading it reported nothing left
    extra = max(changed - shown, 0)
    if extra:
        lines.append(f"  … {extra:,} more")
    if untouched:
        lines.append(f"  {len(untouched)} columns untouched")
    return "\n".join(lines)
