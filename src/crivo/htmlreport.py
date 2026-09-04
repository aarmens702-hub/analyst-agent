"""Shareable HTML diagnosis report (capability roadmap B3.1).

One self-contained file a non-technical teammate can open: inline styles, no
external resources, no server. It surfaces the diagnosis with each finding's
grade and, as the ship-gate (B0.1), runs the PII scan first and shows only
masked samples, so a report that travels never carries raw PII. Every dynamic
value is HTML-escaped. v1 is static; interactive charts (vega-lite/plotly
JSON) are the documented follow-up. Pure, no new dependency.
"""

from __future__ import annotations

from html import escape

import pandas as pd

from crivo import pii

_GRADE_COLOR = {"AUTO": "#1e7d52", "GATE": "#a86a14", "HUMAN": "#a84b3a"}

_STYLE = """
body{font:14px system-ui,sans-serif;margin:0;background:#f3f8f7;color:#1d3a3e}
.wrap{max-width:900px;margin:0 auto;padding:32px 24px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 8px}
.sub{color:#48666a;margin:0 0 8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #e2edeb;vertical-align:top}
th{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#7b9296}
.chip{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:20px;color:#fff}
.pii{background:#fbece9;border:1px solid #a84b3a;border-radius:8px;padding:12px 16px;margin:12px 0}
.none{color:#48666a}
"""


def _grade_chip(grade: str) -> str:
    color = _GRADE_COLOR.get(grade, "#48666a")
    return f'<span class="chip" style="background:{color}">{escape(grade)}</span>'


def _findings_table(findings: list[dict]) -> str:
    if not findings:
        return '<p class="none">No data-quality findings. Nothing to fix.</p>'
    rows = []
    for f in findings:
        cols = ", ".join(f.get("columns", []))
        rows.append(
            "<tr>"
            f"<td>{escape(f.get('slug', ''))}</td>"
            f"<td>{escape(cols)}</td>"
            f"<td>{_grade_chip(f.get('grade', ''))}</td>"
            f"<td>{escape(str(f.get('evidence', '')))}</td>"
            "</tr>"
        )
    return (
        "<table><tr><th>check</th><th>columns</th><th>grade</th>"
        "<th>evidence</th></tr>" + "".join(rows) + "</table>"
    )


def _pii_section(df: pd.DataFrame) -> str:
    hits = pii.scan(df)
    if not hits:
        return '<p class="none">PII scan: none detected.</p>'
    rows = []
    for h in hits:
        rows.append(
            "<tr>"
            f"<td>{escape(h['column'])}</td>"
            f"<td>{escape(h['pii_type'])}</td>"
            f"<td>{h['count']}</td>"
            f"<td>{escape(h['sample'])}</td>"
            "</tr>"
        )
    return (
        '<div class="pii"><b>PII detected &mdash; review before sharing.</b>'
        " Samples below are masked; the report never carries raw values.</div>"
        "<table><tr><th>column</th><th>type</th><th>count</th>"
        "<th>masked sample</th></tr>" + "".join(rows) + "</table>"
    )


def _profile_table(df: pd.DataFrame) -> str:
    rows = []
    for col in df.columns:
        s = df[col]
        rows.append(
            "<tr>"
            f"<td>{escape(str(col))}</td>"
            f"<td>{escape(str(s.dtype))}</td>"
            f"<td>{int(s.notna().sum())} / {len(s)}</td>"
            "</tr>"
        )
    return (
        "<table><tr><th>column</th><th>dtype</th><th>non-null</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def diagnose_to_html(df: pd.DataFrame, findings: list[dict]) -> str:
    """Render a self-contained HTML diagnosis report for `df` and its
    findings (B3.1). Runs the PII scan first as the ship-gate; escapes every
    dynamic value; references no external resource."""
    rows, cols = df.shape
    return (
        "<!doctype html>\n<html><head><meta charset='utf-8'>"
        f"<style>{_STYLE}</style></head><body><div class='wrap'>"
        "<h1>Data diagnosis</h1>"
        f"<p class='sub'>{rows} rows &times; {cols} columns</p>"
        "<h2>Personal data</h2>"
        + _pii_section(df)
        + "<h2>Findings</h2>"
        + _findings_table(findings)
        + "<h2>Columns</h2>"
        + _profile_table(df)
        + "</div></body></html>\n"
    )
