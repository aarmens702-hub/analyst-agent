"""Side-by-side dataset comparison report (capability roadmap B3.3).

One self-contained HTML file a person can open with no server: inline styles,
no external resources, no script. Given a before frame and an after frame (for
example two monthly extracts) it surfaces the row-count delta, the columns
added and removed, the columns whose dtype changed, and for shared numeric
columns a compact before-to-after of non-null count, min, mean, and max.

Every dynamic value (column name, dtype) is HTML-escaped so a hostile label
cannot inject markup. Pure: standard library plus pandas, no new dependency.
"""

from __future__ import annotations

from html import escape

import pandas as pd

_STYLE = """
body{font:14px system-ui,sans-serif;margin:0;background:#f3f8f7;color:#1d3a3e}
.wrap{max-width:900px;margin:0 auto;padding:32px 24px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 8px}
.sub{color:#48666a;margin:0 0 8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #e2edeb;vertical-align:top}
th{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#7b9296}
ul{margin:4px 0;padding-left:20px} li{margin:2px 0}
code{font-family:ui-monospace,monospace;font-size:12px}
.delta{font-weight:600}
.up{color:#1e7d52} .down{color:#a84b3a} .flat{color:#48666a}
.add{color:#1e7d52} .rem{color:#a84b3a}
.none{color:#48666a}
"""


def _is_numeric(series: pd.Series) -> bool:
    # kinds: i int, u unsigned, f float, c complex; excludes bool, string,
    # datetime, so a proportion or a category never masquerades as a number
    return series.dtype.kind in "iufc"


def _fmt_num(value) -> str:
    if pd.isna(value):
        return "n/a"
    number = float(value)
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.3f}".rstrip("0").rstrip(".")


def _pair(before_value, after_value) -> str:
    return f"{escape(str(before_value))} &rarr; {escape(str(after_value))}"


def _row_count_html(before_n: int, after_n: int) -> str:
    delta = after_n - before_n
    if delta > 0:
        delta_str, cls = f"+{delta}", "up"
    elif delta < 0:
        delta_str, cls = str(delta), "down"
    else:
        delta_str, cls = "0", "flat"
    return (
        f"<p>{before_n:,} &rarr; {after_n:,} rows "
        f'<span class="delta {cls}">({delta_str})</span></p>'
    )


def _cols_html(cols: list, cls: str, empty_msg: str) -> str:
    if not cols:
        return f'<p class="none">{empty_msg}</p>'
    items = "".join(
        f'<li class="{cls}"><code>{escape(str(c))}</code></li>' for c in cols
    )
    return f"<ul>{items}</ul>"


def _dtype_html(changes: list[tuple]) -> str:
    if not changes:
        return '<p class="none">No dtype changes.</p>'
    rows = "".join(
        "<tr>"
        f"<td><code>{escape(str(col))}</code></td>"
        f"<td><code>{escape(before_type)}</code> &rarr; "
        f"<code>{escape(after_type)}</code></td>"
        "</tr>"
        for col, before_type, after_type in changes
    )
    return f"<table><tr><th>column</th><th>dtype</th></tr>{rows}</table>"


def _numeric_html(before: pd.DataFrame, after: pd.DataFrame, cols: list) -> str:
    if not cols:
        return '<p class="none">No shared numeric columns to compare.</p>'
    rows = []
    for col in cols:
        b = before[col]
        a = after[col]
        rows.append(
            "<tr>"
            f"<td><code>{escape(str(col))}</code></td>"
            f"<td>{_pair(int(b.notna().sum()), int(a.notna().sum()))}</td>"
            f"<td>{_pair(_fmt_num(b.min()), _fmt_num(a.min()))}</td>"
            f"<td>{_pair(_fmt_num(b.mean()), _fmt_num(a.mean()))}</td>"
            f"<td>{_pair(_fmt_num(b.max()), _fmt_num(a.max()))}</td>"
            "</tr>"
        )
    header = (
        "<tr><th>column</th><th>non-null</th><th>min</th><th>mean</th><th>max</th></tr>"
    )
    return f"<table>{header}{''.join(rows)}</table>"


def compare_to_html(
    before: pd.DataFrame,
    after: pd.DataFrame,
    name_before: str = "before",
    name_after: str = "after",
) -> str:
    """Render a self-contained side-by-side HTML comparison of two frames
    (capability roadmap B3.3).

    Shows the row-count delta with sign, the columns added and removed, the
    columns whose dtype changed, and for shared numeric columns a compact
    before-to-after of non-null count, min, mean, and max. Inline styles only,
    no external resource, no script; every dynamic value is HTML-escaped.
    """
    before_cols = list(before.columns)
    after_cols = list(after.columns)
    before_set = set(before_cols)
    after_set = set(after_cols)

    added = [c for c in after_cols if c not in before_set]
    removed = [c for c in before_cols if c not in after_set]
    shared = [c for c in before_cols if c in after_set]

    dtype_changes = [
        (col, str(before[col].dtype), str(after[col].dtype))
        for col in shared
        if str(before[col].dtype) != str(after[col].dtype)
    ]
    numeric_shared = [
        col for col in shared if _is_numeric(before[col]) and _is_numeric(after[col])
    ]

    legend = f"each cell reads {escape(name_before)} &rarr; {escape(name_after)}"
    return (
        "<!doctype html>\n<html><head><meta charset='utf-8'>"
        f"<style>{_STYLE}</style></head><body><div class='wrap'>"
        "<h1>Dataset comparison</h1>"
        f"<p class='sub'>{escape(name_before)} vs {escape(name_after)}</p>"
        "<h2>Row count</h2>"
        + _row_count_html(len(before), len(after))
        + "<h2>Columns added</h2>"
        + _cols_html(added, "add", "No columns added.")
        + "<h2>Columns removed</h2>"
        + _cols_html(removed, "rem", "No columns removed.")
        + "<h2>Dtype changes</h2>"
        + _dtype_html(dtype_changes)
        + "<h2>Shared numeric columns</h2>"
        + f"<p class='sub'>{legend}</p>"
        + _numeric_html(before, after, numeric_shared)
        + "</div></body></html>\n"
    )
