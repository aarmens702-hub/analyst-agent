"""PII detection and masking (capability roadmap B0.1, the ship-safety
prerequisite that gates the shareable HTML report). Keyless and pure-stdlib:
conservative regex detectors plus a Luhn check for cards, so a receipts tool
does not cry wolf. Each hit carries evidence; masking is offered as a safe
fix. Names/addresses need NER and are deliberately out of the keyless path."""

import pandas as pd

from crivo import pii


def test_detects_emails_with_a_count_and_masked_sample():
    df = pd.DataFrame({"contact": ["a@x.com", "b@y.org", "not-an-email", None]})
    (hit,) = pii.scan(df)
    assert hit["column"] == "contact"
    assert hit["pii_type"] == "email"
    assert hit["count"] == 2
    assert "@" in hit["sample"] and "a@x.com" not in hit["sample"]  # sample masked


def test_credit_cards_require_a_luhn_pass():
    # 4111111111111111 passes Luhn; 4111111111111112 does not
    df = pd.DataFrame({"card": ["4111111111111111", "4111111111111112"]})
    (hit,) = pii.scan(df)
    assert hit["pii_type"] == "credit_card"
    assert hit["count"] == 1  # only the Luhn-valid one counts


def test_ssn_and_phone_are_separate_types():
    df = pd.DataFrame({"ssn": ["123-45-6789", "x"], "phone": ["(415) 555-0132", "y"]})
    types = {h["column"]: h["pii_type"] for h in pii.scan(df)}
    assert types == {"ssn": "ssn", "phone": "phone"}


def test_clean_frame_yields_no_findings():
    df = pd.DataFrame({"amount": [1.0, 2.5], "city": ["burnaby", "vancouver"]})
    assert pii.scan(df) == []


def test_numeric_columns_are_never_scanned_as_text():
    # a numeric column of 16-digit-ish ints must not be read as cards
    df = pd.DataFrame({"n": [4111111111111111, 2]})
    assert pii.scan(df) == []


def test_mask_column_redacts_the_detected_type_only():
    df = pd.DataFrame({"contact": ["a@x.com", "plain"]})
    masked = pii.mask_column(df["contact"], "email")
    assert masked.iloc[0] != "a@x.com"
    assert masked.iloc[0].endswith("@x.com") or masked.iloc[0] == pii.REDACTED
    assert masked.iloc[1] == "plain"  # non-matching cells untouched


def test_scan_findings_are_gradeable_and_carry_evidence():
    df = pd.DataFrame({"email": ["a@x.com", "b@y.com"]})
    (hit,) = pii.scan(df)
    assert hit["grade"] in {"GATE", "HUMAN"}  # exposure is never a silent auto-fix
    assert str(hit["count"]) in hit["evidence"]
