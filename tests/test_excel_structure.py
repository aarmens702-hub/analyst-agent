"""Messy Excel structure intelligence (capability roadmap B2.1).

Real workbooks are not tidy: a title row, blank rows, notes, several tables on
one sheet. pandas.read_excel assumes row 1 is the header and one table per
sheet, so it silently mangles these. This module reads the raw cell grid with
openpyxl (already a dependency, no new one) and reports, per sheet, where the
header really is and whether more than one table lives there, as findings a
person confirms. Keyless and pure (openpyxl + stdlib)."""

import openpyxl

from crivo import excel_structure


def _wb(tmp_path, sheets):
    """sheets: {name: list-of-rows}; write a real .xlsx and return its path."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(list(row))
    path = tmp_path / "book.xlsx"
    wb.save(path)
    return path


def _one(findings, sheet):
    return next(f for f in findings if f["sheet"] == sheet)


def test_tidy_sheet_reports_header_row_1_and_clean(tmp_path):
    path = _wb(tmp_path, {"tidy": [["id", "amount"], [1, 10], [2, 20]]})
    f = _one(excel_structure.analyze_workbook(path), "tidy")
    assert f["header_row"] == 1
    assert f["kind"] == "clean"
    assert f["tables"] == 1


def test_title_and_notes_rows_push_the_header_down(tmp_path):
    path = _wb(
        tmp_path,
        {
            "report": [
                ["Quarterly Sales Report"],  # title, one cell
                [],  # blank
                ["region", "units", "revenue"],  # the real header, row 3
                ["west", 10, 100],
                ["east", 8, 80],
            ]
        },
    )
    f = _one(excel_structure.analyze_workbook(path), "report")
    assert f["header_row"] == 3
    assert f["preamble_rows"] == 2
    assert f["kind"] == "header-below-row-1"
    assert "3" in f["evidence"]


def test_two_tables_on_one_sheet_are_detected(tmp_path):
    path = _wb(
        tmp_path,
        {
            "twotab": [
                ["a", "b"],
                [1, 2],
                [3, 4],
                [],  # blank separator
                [],
                ["x", "y", "z"],
                [5, 6, 7],
            ]
        },
    )
    f = _one(excel_structure.analyze_workbook(path), "twotab")
    assert f["tables"] == 2
    assert f["kind"] == "multiple-tables"


def test_empty_sheet_is_reported_not_crashed(tmp_path):
    path = _wb(tmp_path, {"blank": [[], []]})
    f = _one(excel_structure.analyze_workbook(path), "blank")
    assert f["kind"] == "empty"
    assert f["header_row"] is None


def test_every_sheet_gets_exactly_one_finding(tmp_path):
    path = _wb(
        tmp_path,
        {
            "s1": [["h"], [1]],
            "s2": [["a", "b"], [1, 2]],
        },
    )
    findings = excel_structure.analyze_workbook(path)
    assert {f["sheet"] for f in findings} == {"s1", "s2"}
    assert len(findings) == 2


def test_findings_are_gradeable_and_suggest_a_fix(tmp_path):
    path = _wb(
        tmp_path,
        {"r": [["Title"], [], ["col_a", "col_b"], [1, 2]]},
    )
    f = _one(excel_structure.analyze_workbook(path), "r")
    assert f["grade"] in {"GATE", "HUMAN"}  # restructuring is a judgement call
    assert f["suggestion"]  # a concrete next step, e.g. skip N rows


def test_import_is_keyless_and_pulls_no_core_module():
    import os
    import subprocess
    import sys

    code = (
        "import sys, crivo.excel_structure\n"
        "bad=[m for m in ('crivo.loop','crivo.prompts','crivo.skills',"
        "'crivo.provenance','crivo.llm') if m in sys.modules]\n"
        "print(bad); sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
