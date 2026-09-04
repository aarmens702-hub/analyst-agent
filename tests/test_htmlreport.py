"""Shareable HTML report (capability roadmap B3.1). One self-contained file
(inline styles, no external URLs, no server) that surfaces the diagnosis with
its grades and runs the B0.1 PII scan first as the ship-gate. v1 is static;
interactive charts are the documented follow-up. Pure, no new dependency."""

import pandas as pd

from crivo import htmlreport


def _findings():
    return [
        {
            "disease": 4,
            "slug": "sentinel-missing",
            "columns": ["a"],
            "grade": "AUTO",
            "evidence": "-999 appears 12x",
        },
        {
            "disease": 3,
            "slug": "mixed-date-formats",
            "columns": ["d"],
            "grade": "GATE",
            "evidence": "4 date formats",
        },
    ]


def test_report_is_one_self_contained_document():
    html = htmlreport.diagnose_to_html(
        pd.DataFrame({"a": [1], "d": ["x"]}), _findings()
    )
    assert html.lstrip().startswith("<!doctype html>")
    assert "</html>" in html
    # self-contained: no external resources to fetch (no server, emailable)
    assert "http://" not in html and "https://" not in html


def test_every_finding_shows_its_column_grade_and_evidence():
    html = htmlreport.diagnose_to_html(
        pd.DataFrame({"a": [1], "d": ["x"]}), _findings()
    )
    assert "sentinel-missing" in html and "mixed-date-formats" in html
    assert "AUTO" in html and "GATE" in html
    assert "-999 appears 12x" in html


def test_evidence_is_html_escaped_no_injection():
    findings = [
        {
            "disease": 1,
            "slug": "x",
            "columns": ["c"],
            "grade": "AUTO",
            "evidence": "<script>alert(1)</script>",
        }
    ]
    html = htmlreport.diagnose_to_html(pd.DataFrame({"c": [1]}), findings)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_pii_scan_runs_and_shows_masked_samples_never_raw():
    df = pd.DataFrame({"email": ["real.person@x.com", "b@y.com"]})
    html = htmlreport.diagnose_to_html(df, [])
    assert "PII" in html or "pii" in html
    assert "real.person@x.com" not in html  # raw PII must never reach the file
    assert "@x.com" in html  # the masked sample still shows the shape


def test_clean_data_reports_no_findings_gracefully():
    html = htmlreport.diagnose_to_html(pd.DataFrame({"n": [1, 2, 3]}), [])
    assert "</html>" in html
    assert "no" in html.lower()  # a "no findings" / "nothing to fix" note
