"""Semantic column typing (capability roadmap B4.1).

Infers what a text column IS from its values with conservative,
evidence-carrying heuristics. A column types only when a strong majority of
its non-null cells match one detector (threshold 0.90, named in the
evidence); an ambiguous column yields nothing. Conservative beats greedy:
this is a receipts tool, so it under-claims rather than over-claims. The
module is independent of pii.py (typing, not exposure) and keyless.
"""

import importlib
import os
import subprocess
import sys

import pandas as pd
import pytest


def _infer(series):
    st = importlib.import_module("crivo.semantic_types")
    return st.infer_types(pd.DataFrame({"c": series}))


# (series, expected_semantic_type or None, expected_match_rate or None)
CASES = [
    # a clean email column types as email with match_rate 1.0
    (pd.Series(["a@x.com", "b@y.org", "c@z.net"]), "email", 1.0),
    # nulls are excluded from the denominator, so the rate stays 1.0
    (pd.Series(["a@x.com", "b@y.org", None]), "email", 1.0),
    # a strong majority (9/10) still clears the 0.90 threshold
    (pd.Series([f"u{i}@x.com" for i in range(9)] + ["nope"]), "email", 0.9),
    # a mixed column below threshold (8/10) types as nothing
    (
        pd.Series([f"u{i}@x.com" for i in range(8)] + ["nope", "also"]),
        None,
        None,
    ),
    # a plain mixed column (2 email / 2 junk) types as nothing
    (pd.Series(["a@x.com", "b@y.org", "nope", "also-nope"]), None, None),
    (pd.Series(["https://a.com", "http://b.org/p", "www.c.net"]), "url", 1.0),
    # formatted phone numbers type as phone
    (pd.Series(["(415) 555-0132", "212-555-0148", "+1 646-555-0111"]), "phone", 1.0),
    # bare 10-digit strings carry no phone formatting and are ambiguous with
    # account numbers, so they type as nothing (not phone)
    (pd.Series(["4155550132", "2125550148", "6465550111"]), None, None),
    # a bare 5-digit column is indistinguishable from 5-digit ids by value, so
    # under the under-claim contract it types as nothing (not zip)
    (pd.Series(["94103", "10001", "60614"]), None, None),
    # 5-digit account codes, unique: also nothing (the over-claim the review caught)
    (pd.Series(["10000", "20000", "30000"]), None, None),
    # the unambiguous ZIP+4 form types as zip_code
    (pd.Series(["94103-1234", "10001-0001"]), "zip_code", 1.0),
    # a column only partly in +4 form (1/3) is below threshold, so nothing
    (pd.Series(["94103", "10001-0001", "60614"]), None, None),
    # a currency column: a dollar amount
    (pd.Series(["$1,234.56", "$10.00", "$999"]), "currency_amount", 1.0),
    # a currency column: an ISO-prefixed amount
    (pd.Series(["USD 1,234.56", "EUR 10.00", "GBP 5"]), "currency_amount", 1.0),
    # symbol and ISO forms mixed
    (pd.Series(["$1,234.56", "USD 10.00"]), "currency_amount", 1.0),
    (pd.Series(["US", "CA", "GB", "DE"]), "country", 1.0),
    (pd.Series(["USA", "CAN", "GBR"]), "country", 1.0),
    (pd.Series(["United States", "Canada", "Germany"]), "country", 1.0),
    # an id column of unique codes types as identifier
    (pd.Series(["INV-1001", "INV-1002", "INV-1003", "INV-1004"]), "identifier", 1.0),
    (pd.Series(["SKU9A", "SKU9B", "SKU9C"]), "identifier", 1.0),
    # a unique numeric column (as strings) does NOT type as identifier
    (pd.Series(["100", "101", "102", "103"]), None, None),
    # a unique numeric column (numeric dtype) is never read as text
    (pd.Series([100, 101, 102, 103]), None, None),
    # multi-word free text is neither a value type nor an identifier
    (pd.Series(["hello world", "foo bar", "lorem ipsum"]), None, None),
    # unique single-token words (product names) are NOT identifiers: a code
    # needs a digit, so a pure-word column types as nothing (review catch)
    (pd.Series(["Widget", "Gadget", "Sprocket"]), None, None),
    # unique date strings stored as text are NOT identifiers (review catch)
    (pd.Series(["2024-01-01", "2024-02-02", "2024-03-03"]), None, None),
]

IDS = [
    "email_clean",
    "email_with_null",
    "email_threshold_pass",
    "email_threshold_fail",
    "email_mixed",
    "url",
    "phone_formatted",
    "phone_bare_not_phone",
    "zip5_bare_ambiguous",
    "zip5_account_codes",
    "zip5_4",
    "zip_partial_below_threshold",
    "currency_dollar",
    "currency_iso",
    "currency_mixed",
    "country_alpha2",
    "country_alpha3",
    "country_names",
    "identifier_codes",
    "identifier_sku",
    "numeric_strings_not_id",
    "numeric_dtype_skipped",
    "free_text_multiword",
    "single_token_words_not_id",
    "date_strings_not_id",
]


@pytest.mark.parametrize("series,expected_type,expected_rate", CASES, ids=IDS)
def test_infer_types(series, expected_type, expected_rate):
    findings = _infer(series)
    if expected_type is None:
        assert findings == []
        return
    assert len(findings) == 1
    f = findings[0]
    assert set(f) == {"column", "semantic_type", "confidence", "evidence", "match_rate"}
    assert f["column"] == "c"
    assert f["semantic_type"] == expected_type
    assert f["match_rate"] == pytest.approx(expected_rate)
    assert 0.0 <= f["confidence"] <= 1.0
    # evidence names the match rate (and the threshold)
    assert f"{expected_rate:.2f}" in f["evidence"]


def test_import_is_keyless_and_pulls_no_core_module():
    # conftest's autouse quarantine has already stripped every provider key
    # from os.environ, so the child interpreter imports with no key at all;
    # the module must import without the network and must not drag a reserved
    # core module into the graph (contract: loop, prompts, skills, provenance)
    code = (
        "import sys\n"
        "import crivo.semantic_types\n"
        "reserved = ('crivo.loop', 'crivo.prompts', 'crivo.skills',"
        " 'crivo.provenance', 'crivo.llm')\n"
        "bad = [m for m in reserved if m in sys.modules]\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
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
