"""Notebook HTML rendering (P?/R?): a diagnosis or a clean summary rendered as
a self-contained dark card.

The card paints its OWN background and text colour and never depends on the
notebook's light/dark theme. Every style is inline — no <style>, <script>,
@media, :hover, external font or URL, or <img> — because GitHub and nbconvert
sanitizers strip all of those. Inline styles do not isolate the card FROM the
host CSS (Jupyter's `.jp-RenderedHTMLCommon` rules and inheritance bleed in),
so colour / font / spacing are set explicitly on the wrapper and on every span
it emits. Every interpolated value is `html.escape`d — it is user data. The
functions RETURN the HTML string; they never call `display()`.

Identity tokens are the project's 'verified ledger' look — docs/assets/terminal.svg.
"""

import html

from crivo.detect import SINGLE_FRAME

# --- identity tokens (docs/assets/terminal.svg) -----------------------------
GROUND = "#14171a"  # the card's own background
PANEL = "#1b1f24"
INK = "#c7cdd3"  # body text
BRIGHT = "#e7e9e4"  # the dataset name
MUTED = "#6c7681"
MUTED_2 = "#8c95a0"  # evidence
DIM = "#5c6771"  # the dNN tag and the italic footer, from the asset
TEAL = "#56b9c2"  # column names, the 'fixable' count
TEAL_RULE = "#3a8f97"  # the header rule
BORDER = "#2a2f35"
GRADE = {"AUTO": "#5cb98a", "GATE": "#d5a24f", "HUMAN": "#e05a54"}

MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
SANS = (
    'ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", '
    "Roboto, Helvetica, Arial, sans-serif"
)


def _esc(value) -> str:
    return html.escape(str(value))


def _span(text, color, *, mono=False, bold=False, italic=False) -> str:
    """One inline span with a full, explicit style — colour, font family, size,
    line-height, weight and a transparent background — so nothing inherits from
    the host notebook's CSS. `text` is assumed already escaped / safe markup."""
    font = MONO if mono else SANS
    style = (
        f"color:{color};background:transparent;font-family:{font};"
        f"font-size:13px;line-height:1.5;"
        f"font-weight:{'700' if bold else '400'};"
        f"font-style:{'italic' if italic else 'normal'};"
    )
    return f'<span style="{style}">{text}</span>'


def _card(inner: str) -> str:
    """The wrapper: one <div> that paints its own ground and sets every
    inheritable property explicitly, capped at a readable width."""
    style = (
        f"box-sizing:border-box;max-width:760px;margin:4px 0;padding:14px 18px 16px;"
        f"background:{GROUND};color:{INK};border:1px solid {BORDER};border-radius:10px;"
        f"font-family:{SANS};font-size:13px;line-height:1.5;text-align:left;"
        f"font-weight:400;"
    )
    return f'<div style="{style}">{inner}</div>'


def _rule() -> str:
    return (
        f'<div style="box-sizing:border-box;height:0;border:0;'
        f"border-top:1px solid {TEAL_RULE};background:transparent;"
        f'margin:8px 0 6px;"></div>'
    )


def _header(name, rows, ncols) -> str:
    title = _span(_esc(name), BRIGHT, bold=True)
    dims = _span(f"&nbsp;&nbsp;&nbsp;{rows} rows &#215; {ncols} columns", MUTED)
    return f'<div style="margin:0 0 2px;">{title}{dims}</div>{_rule()}'


GRADE_NOTE = {
    "AUTO": "safe to auto-fix",
    "GATE": "fix with a check",
    "HUMAN": "needs a human",
}


def _dot_sep() -> str:
    return _span("&nbsp;&nbsp;&#183;&nbsp;&nbsp;", MUTED)


def _summary(fixable, flagged, clear) -> str:
    body = _dot_sep().join(
        [
            _span(f"{fixable} fixable", TEAL, bold=True),
            _span(f"{flagged} flagged", GRADE["GATE"], bold=True),
            _span(f"{clear} signals clear", GRADE["AUTO"], bold=True),
        ]
    )
    return f'<div style="margin:2px 0 4px;">{body}</div>'


def _cols_label(columns) -> str:
    joined = ", ".join(_esc(c) for c in columns) or "whole table"
    return f"[{joined}]"


def _finding(finding) -> str:
    color = GRADE.get(finding["grade"], MUTED_2)
    meta = (
        _span("&#9679;&nbsp;", color)
        + _span(f"d{finding['disease']:02d}", DIM, mono=True)
        + _span("&nbsp;&nbsp;", MUTED)
        + _span(_esc(finding["slug"]), color, bold=True)
        + _span("&nbsp;&nbsp;", MUTED)
        + _span(_cols_label(finding["columns"]), TEAL, mono=True)
    )
    evidence = _span(_esc(finding["evidence"]), MUTED_2)
    # _cols_label already escapes each name; grade is enum but escape the
    # fallback so no interpolated value ever reaches the card unescaped
    note = _span(GRADE_NOTE.get(finding["grade"], _esc(finding["grade"])), color)
    if finding["indicator"]:
        tail = _span("&nbsp;&nbsp;&#183;&nbsp;&nbsp;flagged, never auto-fixed", MUTED)
    else:
        tail = _span(
            f"&nbsp;&nbsp;&#183;&nbsp;&nbsp;confidence {finding['confidence']:.2f}",
            MUTED,
        )
    return (
        f'<div style="margin-top:10px;">{meta}</div>'
        f'<div style="margin:2px 0 0 18px;">{evidence}</div>'
        f'<div style="margin:2px 0 0 18px;">{note}{tail}</div>'
    )


def _compact_ranges(nums) -> str:
    """[2,3,4,5,8,9,10] -> '2-5, 8-10' — the checked-and-clean footer's spine."""
    ordered = sorted({int(n) for n in nums})
    if not ordered:
        return "none"
    spans, start, prev = [], ordered[0], ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        spans.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = n
    spans.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(spans)


def _encoding_warning(frame) -> str:
    """An amber line iff the file was read as something other than UTF-8."""
    enc = frame.attrs.get("encoding")
    if enc in (None, "utf-8", "utf-8-sig"):
        return ""
    text = _span("&#9888;&nbsp;" + _esc(f"not utf-8 — read as {enc}"), GRADE["GATE"])
    return f'<div style="margin:2px 0 4px;">{text}</div>'


def _footer(clear, n_signals) -> str:
    ranges = _span("checked and clean: " + _esc(_compact_ranges(clear)), MUTED)
    tagline = _span(
        f"{n_signals} signals run on every file &#8212; "
        "absence is a checked claim, not a silence",
        DIM,
        italic=True,
    )
    return (
        f"{_rule()}"
        f'<div style="margin:6px 0 0;">{ranges}</div>'
        f'<div style="margin:2px 0 0;">{tagline}</div>'
    )


def report_html(name, frame, result):
    findings = result.get("findings", [])
    fixable = sum(1 for f in findings if not f["indicator"])
    flagged = sum(1 for f in findings if f["indicator"])
    parts = [
        _header(name, len(frame), len(frame.columns)),
        _encoding_warning(frame),
        _summary(fixable, flagged, len(result.get("clear", []))),
    ]
    parts.extend(_finding(f) for f in findings)
    parts.append(_footer(result.get("clear", []), len(SINGLE_FRAME)))
    return _card("".join(parts))


def _fix_head(mark, mark_color, slug_color, fix) -> str:
    return (
        _span(f"{mark}&nbsp;", mark_color)
        + _span(f"d{fix['disease']:02d}", DIM, mono=True)
        + _span("&nbsp;&nbsp;", MUTED)
        + _span(_esc(fix["slug"]), slug_color, bold=True)
        + _span("&nbsp;&nbsp;", MUTED)
        + _span(_cols_label(fix["columns"]), TEAL, mono=True)
    )


def _applied_row(fix, examples) -> str:
    rows = [
        f'<div style="margin-top:10px;">{_fix_head("✓", GRADE["AUTO"], GRADE["AUTO"], fix)}</div>'
    ]
    for ex in examples[:3]:
        if ex.get("removed"):
            text = _esc(f"{ex['column']} removed (constant {ex['value']!r})")
        else:
            text = _esc(repr(ex["old"])) + " → " + _esc(str(ex["new"]))
        rows.append(
            f'<div style="margin:2px 0 0 18px;">{_span(text, MUTED_2, mono=True)}</div>'
        )
    return "".join(rows)


def _deferred_row(fix) -> str:
    head = _fix_head("&#183;", MUTED, MUTED_2, fix)
    reason = fix.get("reason")
    if reason:
        head += _span("&nbsp;&#8212; " + _esc(reason), MUTED)
    return f'<div style="margin-top:8px;">{head}</div>'


def clean_html(summary):
    applied, deferred = summary.applied, summary.needs_review
    samples = {
        (s["disease"], tuple(s["columns"])): s["examples"] for s in summary.samples()
    }
    header = _span(
        f"cleaned&nbsp;&nbsp;{len(applied)} applied &#183; {len(deferred)} deferred",
        BRIGHT,
        bold=True,
    )
    parts = [f'<div style="margin:0 0 2px;">{header}</div>{_rule()}']
    parts.extend(
        _applied_row(f, samples.get((f["disease"], tuple(f["columns"])), []))
        for f in applied
    )
    parts.extend(_deferred_row(f) for f in deferred)
    return _card("".join(parts))
