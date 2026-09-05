"""Wiring the built capability modules onto the public surface (roadmap #1):
PII scan reachable on a Report and gating the shareable to_html; driver
decomposition as a top-level crivo.drivers. Keyless, no model, no new dep."""

import pandas as pd

import crivo


def test_report_exposes_a_pii_scan():
    report = crivo.diagnose(pd.DataFrame({"email": ["a@x.com", "b@y.com"]}))
    hits = report.pii()
    assert hits and hits[0]["pii_type"] == "email"
    assert hits[0]["grade"] == "HUMAN"  # exposure never auto-fixes


def test_to_html_runs_the_pii_gate_with_masked_samples(tmp_path):
    df = pd.DataFrame({"contact": ["real.person@corp.com", "b@y.com"]})
    out = crivo.diagnose(df, name="leaky").to_html(tmp_path / "r.html")
    text = out.read_text()
    assert "PII" in text  # the ship-gate section is present
    assert "real.person@corp.com" not in text  # raw PII never travels
    assert "@corp.com" in text  # masked sample keeps the shape
    assert text.lower().startswith("<!doctype html")  # still self-contained
    assert "http://" not in text and "https://" not in text


def test_to_html_without_pii_says_so(tmp_path):
    df = crivo.load_example()
    text = crivo.diagnose(df, name="example").to_html(tmp_path / "r.html").read_text()
    assert "example" in text  # existing contract: the dataset is named
    assert "none detected" in text.lower()  # a clean PII scan states itself


def test_drivers_is_a_top_level_keyless_call():
    before = pd.DataFrame({"region": ["w", "e"], "amount": [100, 50]})
    after = pd.DataFrame({"region": ["w", "e"], "amount": [120, 40]})
    out = crivo.drivers(before, after, "amount", "region")
    assert out["delta"] == 10
    assert out["receipt"] is True
    assert sum(c["contribution"] for c in out["contributions"]) == out["delta"]


def test_drivers_is_exported():
    assert "drivers" in crivo.__all__


def test_report_exposes_semantic_types():
    report = crivo.diagnose(pd.DataFrame({"email": ["a@x.com", "b@y.com"]}))
    types = report.semantic_types()
    assert any(t["semantic_type"] == "email" and t["column"] == "email" for t in types)


def test_compare_is_a_top_level_keyless_call():
    before = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    after = pd.DataFrame({"a": [1, 2, 3]})  # b removed, rows grew
    html = crivo.compare(before, after)
    assert html.lower().startswith("<!doctype html")
    assert "http://" not in html and "https://" not in html
    assert "b" in html  # the removed column is reported


def test_export_notebook_writes_a_valid_ipynb(tmp_path):
    import json

    src = tmp_path / "data.csv"
    src.write_text("amount,city\n1,burnaby\n2,vancouver\n")
    out = crivo.export_notebook(str(src), tmp_path / "analysis.ipynb")
    nb = json.loads(out.read_text())
    assert nb["nbformat"] == 4
    assert any(c["cell_type"] == "code" for c in nb["cells"])


def test_new_surface_is_exported():
    assert {"compare", "export_notebook", "analyze_excel"} <= set(crivo.__all__)


def test_report_exposes_cross_column_contradictions():
    df = pd.DataFrame(
        {
            "city": ["SF", "SF", "SF", "SF", "LA", "LA"],
            "state": ["CA", "CA", "CA", "TX", "CA", "CA"],  # one SF mislabeled
        }
    )
    issues = crivo.diagnose(df).cross_column(threshold=0.8)
    fd = next(f for f in issues if f["columns"] == ["city", "state"])
    assert fd["violations"] == 1 and fd["grade"] == "HUMAN"


def test_analyze_excel_reads_a_messy_workbook(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "sheet1"
    for row in [["Report Title"], [], ["region", "amount"], ["west", 10]]:
        ws.append(row)
    p = tmp_path / "book.xlsx"
    wb.save(p)

    findings = crivo.analyze_excel(p)
    assert findings[0]["sheet"] == "sheet1"
    assert findings[0]["header_row"] == 3  # under the title + blank
    assert findings[0]["kind"] == "header-below-row-1"
