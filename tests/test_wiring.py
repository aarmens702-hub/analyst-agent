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
