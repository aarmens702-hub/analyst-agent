"""Messy Excel structure intelligence (capability roadmap B2.1).

pandas.read_excel assumes row 1 is the header and one table per sheet, so a
real workbook (a title row, blank rows, notes, several tables on one sheet)
is silently mangled. This module reads the raw cell grid with openpyxl
(already a dependency) and reports, per sheet, where the header actually is
and whether more than one table lives there, as findings a person confirms
before any reshape. Keyless and pure: openpyxl plus the standard library, no
model, no new dependency.

A "table block" is a run of consecutive non-empty rows at least two columns
wide; a one-column run (a title or a single note) is preamble, not a table.
The header is the first table block's first row. Restructuring is a
judgement call, so findings are graded, never auto-applied.
"""

from __future__ import annotations

import openpyxl

# at least this many columns of content before a run of rows counts as a
# table rather than a title or a single-column note
_MIN_TABLE_WIDTH = 2


def _row_width(row: tuple) -> int:
    """Number of non-blank cells in a row (a whitespace-only cell is blank)."""
    return sum(1 for v in row if v is not None and str(v).strip() != "")


def _blocks(widths: list[int]) -> list[tuple[int, int]]:
    """Index ranges (start, end inclusive) of consecutive non-blank rows."""
    out: list[tuple[int, int]] = []
    start = None
    for i, w in enumerate(widths):
        if w > 0 and start is None:
            start = i
        elif w == 0 and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(widths) - 1))
    return out


def _analyze_sheet(name: str, grid: list[tuple]) -> dict:
    widths = [_row_width(r) for r in grid]
    blocks = _blocks(widths)
    # a table block is at least _MIN_TABLE_WIDTH wide at its widest row
    tables = [
        b for b in blocks if max(widths[b[0] : b[1] + 1], default=0) >= _MIN_TABLE_WIDTH
    ]

    base = {
        "sheet": name,
        "header_row": None,
        "preamble_rows": 0,
        "tables": len(tables),
        "grade": "AUTO",
        "evidence": "",
        "suggestion": "",
    }

    if not tables:
        base["kind"] = "empty" if not any(widths) else "no-table"
        base["evidence"] = "no tabular block found"
        return base

    header_index = tables[0][0]  # 0-indexed row where the first table starts
    header_row = header_index + 1  # 1-indexed for humans
    preamble = header_index
    base["header_row"] = header_row
    base["preamble_rows"] = preamble

    if len(tables) > 1:
        base["kind"] = "multiple-tables"
        base["grade"] = "HUMAN"
        base["evidence"] = f"{len(tables)} tables on one sheet"
        base["suggestion"] = f"split this sheet into its {len(tables)} tables"
    elif header_row > 1:
        base["kind"] = "header-below-row-1"
        base["grade"] = "GATE"
        base["evidence"] = (
            f"header on row {header_row}; {preamble} preamble row(s) above it"
        )
        base["suggestion"] = f"skip the first {preamble} row(s) when reading this sheet"
    else:
        base["kind"] = "clean"
        base["evidence"] = "header on row 1, one table"
    return base


def analyze_workbook(path) -> list[dict]:
    """One structural finding per sheet in the workbook at `path` (B2.1).

    Each finding names the sheet, the real header row (1-indexed, or None for
    an empty sheet), how many preamble rows sit above it, how many tables the
    sheet holds, a grade, evidence, and a concrete suggestion. Reads values
    only (formulas resolved to their cached result); adds no dependency.
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        return [
            _analyze_sheet(ws.title, list(ws.iter_rows(values_only=True)))
            for ws in wb.worksheets
        ]
    finally:
        wb.close()
