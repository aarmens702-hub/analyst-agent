"""Tests for the 22-disease detection engine (detect.py).

Per detector: one synthetic frame that trips it and one clean frame that must
not (false-positive discipline is asserted, not assumed). Integration tests run
against the Raha benchmark pairs in data/raha/ and encode AC1's expectations.

Raha files are loaded with keep_default_na=False: the benchmark's dirt includes
tokens ("N/A", "empty", "Not Available") that read_csv's default NA handling
would silently coerce to NaN, hiding the very diseases the engine must find.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from crivo import detect
from crivo.detect import (
    BC_LAT_MAX,
    BC_LAT_MIN,
    BC_LON_MAX,
    BC_LON_MIN,
    detect_all,
    detect_family,
    detect_one,
)

RAHA = Path(__file__).resolve().parent.parent / "data" / "raha"
needs_raha = pytest.mark.skipif(not RAHA.is_dir(), reason="data/raha absent")

FINDING_KEYS = {
    "disease",
    "slug",
    "columns",
    "evidence",
    "stats",
    "grade",
    "confidence",
    "indicator",
}


def _diseases(result: dict) -> set[int]:
    return {f["disease"] for f in result["findings"]}


def _of(result: dict, disease: int) -> list[dict]:
    return [f for f in result["findings"] if f["disease"] == disease]


def _raha(dataset: str, kind: str) -> pd.DataFrame:
    return pd.read_csv(RAHA / dataset / f"{kind}.csv", keep_default_na=False)


# --- per-disease synthetic fixtures -----------------------------------------


def test_d01_numbers_as_strings() -> None:
    dirty = pd.DataFrame(
        {"price": ["$1,200", "$3,400.50", "15 kg", "12.0 oz", "980"] * 4}
    )
    res = detect_all(dirty)
    assert 1 in _diseases(res)
    (f,) = _of(res, 1)
    assert f["columns"] == ["price"]
    assert f["grade"] == "AUTO"
    assert f["confidence"] >= 0.9
    clean = pd.DataFrame({"price": [1200.0, 3400.5, 15.0, 12.0, 980.0] * 4})
    clean_res = detect_all(clean)
    assert 1 not in _diseases(clean_res)
    assert 1 in clean_res["clear"]


def test_d01_id_like_digit_strings_are_not_flagged() -> None:
    df = pd.DataFrame({"provider_id": [str(10000 + i) for i in range(20)]})
    assert 1 not in _diseases(detect_all(df))


MONEY_STYLES = (
    lambda v: f"${v:,.2f}",  # symbol
    lambda v: f"{v:,.2f}",  # thousands comma
    lambda v: f"{v:.2f}".replace(".", ","),  # European decimal comma
    lambda v: f"USD {v:.2f}",  # ISO code prefix
    lambda v: f"{v:.2f}",  # bare float as text
)


def test_d01_constant_prefix_code_columns_are_not_flagged() -> None:
    """Bench 2026-09-02: 'SIT000000'-style ids — one constant alpha prefix and
    fixed-width digits on every value — were read as numbers carrying
    currency/unit residue. A uniform code scheme is an identifier, not an
    amount wearing a unit."""
    codes = pd.DataFrame({"site_id": [f"SIT{i:06d}" for i in range(60)]})
    assert 1 not in _diseases(detect_all(codes))


@pytest.mark.parametrize("k", [1, 2, 3, 4, 5])
def test_d01_gets_louder_not_quieter_as_money_formats_mix(k) -> None:
    """A1: the old gate demanded 90% of values match ONE pattern, so a column
    got quieter as it got more damaged — five money formats each at ~20%
    meant silence, and the report filed the worst column under "checked and
    clean". The invariant is monotonicity: every added format must keep the
    signal firing (and escalate the grade once the decimal-comma/thousands-
    comma conflict makes single values ambiguous), never mute it."""
    import random

    rng = random.Random(7)
    values = [MONEY_STYLES[rng.randrange(k)](1000 + i * 3) for i in range(600)]

    found = detect_one(pd.DataFrame({"amount": values}), 1, ["amount"])

    assert found is not None, f"{k} money formats -> silence; the gate ran backwards"
    assert found["grade"] == ("GATE" if k >= 3 else "AUTO"), found["grade"]
    if k >= 2:
        assert len(found["stats"]["families"]) >= 2, found["stats"]


DATE_STYLES = (
    lambda i: f"2024-0{i % 9 + 1}-1{i % 9} 14:3{i % 6}:00",  # iso, space sep
    lambda i: f"0{i % 9 + 1}/1{i % 9}/2024",  # slash
    lambda i: f"2024-0{i % 9 + 1}-1{i % 9}T14:3{i % 6}:00Z",  # iso-zoned
    lambda i: f"171{i % 9}0000{i % 9}0",  # epoch seconds, claimed only in company
)


@pytest.mark.parametrize("k", [2, 3, 4])
def test_d03_fires_on_a_format_mix_even_with_an_unclaimed_tail(k) -> None:
    """A1, date side: d03's entire subject is "multiple date formats in one
    column", yet the scan gated on >=90% single-family coverage — the disease
    switched off its own detector. With k=4 an unclaimed epoch tail drops
    coverage to 0.75 and the old gate went silent; the mix must fire and the
    unclaimed share must be named, because a reader deciding whether to trust
    the column needs both numbers."""
    import random

    rng = random.Random(7)
    values = [DATE_STYLES[rng.randrange(k)](i) for i in range(600)]

    found = detect_one(pd.DataFrame({"posted_at": values}), 3, ["posted_at"])

    assert found is not None, f"{k} date formats -> silence; the gate ran backwards"
    if k == 4:
        # epoch became a claimed family (in company); the tail is now named,
        # not unknown — see test_epoch_seconds_join_the_mix_when_dates_keep_them_company
        assert "epoch" in found["evidence"], found["evidence"]


def test_epoch_seconds_join_the_mix_when_dates_keep_them_company() -> None:
    """The transaction fixture's posted_at rotates iso, slash, iso-zoned, and
    epoch seconds; epoch was unclaimed, so the mix reported a 25% unknown
    tail. Ten digits ARE a timestamp when real date formats share the column:
    all four families named, nothing unclaimed."""
    import random

    rng = random.Random(7)
    values = [DATE_STYLES[rng.randrange(4)](i) for i in range(600)]

    found = detect_one(pd.DataFrame({"posted_at": values}), 3, ["posted_at"])

    assert found is not None
    assert "epoch" in found["evidence"], found["evidence"]
    assert "no known" not in found["evidence"], found["evidence"]


def test_a_column_of_bare_ten_digit_integers_is_not_dates() -> None:
    """The other half of the epoch decision, and the reason it was deferred
    until now: order ids, account numbers, and phone-adjacent columns live in
    the same ten digits as unix seconds. Alone, they are integers; only in the
    company of another date family (>= 5%) does epoch count. Without this
    guard, adding the family would have turned every id column into a
    dates-as-strings AUTO fix."""
    ids = [str(1_700_000_000 + i) for i in range(40)]

    result = detect_all(pd.DataFrame({"order_id": ids}))

    assert 2 not in _diseases(result), "an id column must never be a date finding"
    assert 3 not in _diseases(result)


def test_a_half_money_column_is_a_judgement_call_not_a_clean_bill() -> None:
    """The money mirror of the date case below, pinning the same third
    outcome on _d01's own middle branch."""
    values = [f"${i}.00" for i in range(24)] + [
        f"awaiting invoice {i}" for i in range(36)
    ]

    found = detect_one(pd.DataFrame({"amount": values}), 1, ["amount"])

    assert found is not None, "40% money reported as silence"
    assert found["grade"] == "HUMAN"
    assert "60%" in found["evidence"], found["evidence"]


def test_a_half_date_column_is_a_judgement_call_not_a_clean_bill() -> None:
    """A1's third outcome. 60% iso dates + 40% free text is neither "dates as
    strings, fix it" nor "checked and clean" — the old two-outcome gate could
    only say the second, which was a lie. The middle zone must surface as a
    HUMAN-graded finding naming both numbers."""
    values = [f"2024-03-{i % 28 + 1:02d}" for i in range(36)] + [
        f"pending review {i}" for i in range(24)
    ]

    found = detect_one(pd.DataFrame({"updated": values}), 2, ["updated"])

    assert found is not None, "60% dates reported as silence"
    assert found["grade"] == "HUMAN"
    assert "40%" in found["evidence"], found["evidence"]


def test_d02_dates_as_strings_single_format() -> None:
    dirty = pd.DataFrame({"signup_date": [f"2021-03-{d:02d}" for d in range(1, 21)]})
    res = detect_all(dirty)
    assert 2 in _diseases(res)
    assert 3 not in _diseases(res)  # single format is not "mixed"
    (f,) = _of(res, 2)
    assert f["grade"] == "AUTO"
    assert f["confidence"] >= 0.95
    clean = pd.DataFrame(
        {"signup_date": pd.to_datetime([f"2021-03-{d:02d}" for d in range(1, 21)])}
    )
    clean_res = detect_all(clean)
    assert 2 not in _diseases(clean_res)
    assert 2 in clean_res["clear"]


def test_d03_mixed_date_formats_with_ambiguity() -> None:
    dirty = pd.DataFrame(
        {
            "when": [f"2021-03-{d:02d}" for d in range(1, 11)]
            + [f"0{m}/0{d}/2021" for m, d in zip(range(1, 6), range(2, 7))] * 2
        }
    )
    res = detect_all(dirty)
    assert 3 in _diseases(res)
    assert 2 not in _diseases(res)  # mixed-format columns belong to 3, not 2
    (f,) = _of(res, 3)
    assert f["grade"] == "GATE"
    assert f["stats"]["ambiguous"] is True  # all slot values <= 12
    clean = pd.DataFrame({"when": [f"2021-03-{d:02d}" for d in range(1, 21)]})
    assert 3 not in _diseases(detect_all(clean))


def test_d04_sentinel_missing_strings_auto() -> None:
    dirty = pd.DataFrame(
        {"status": (["ok", "late", "early", "N/A"] * 6) + ["N/A", "n/a"]}
    )
    res = detect_all(dirty)
    (f,) = _of(res, 4)
    assert f["grade"] == "AUTO"
    assert f["columns"] == ["status"]
    assert f["stats"]["sentinel_count"] == 8
    clean = pd.DataFrame({"status": ["ok", "late", "early", "queued"] * 6})
    clean_res = detect_all(clean)
    assert 4 not in _diseases(clean_res)
    assert 4 in clean_res["clear"]


def test_d04_sentinel_numeric_zero_human() -> None:
    dirty = pd.DataFrame({"reading": [0] * 20 + [3, 3, 3, 7, 8, 9, 12]})
    res = detect_all(dirty)
    (f,) = _of(res, 4)
    assert f["grade"] == "HUMAN"  # numeric-zero sub-case per the table
    assert f["stats"]["sentinel_value"] == 0
    clean = pd.DataFrame({"reading": [3, 5, 7, 8, 9, 12, 4, 6] * 3})
    assert 4 not in _diseases(detect_all(clean))


def test_d05_suppression_codes() -> None:
    values = [str(v) for v in range(100, 137)] + ["<5", "<5", ".."]
    res = detect_all(pd.DataFrame({"measured": values}))
    (f,) = _of(res, 5)
    assert f["grade"] == "AUTO"
    assert "<5" in f["stats"]["tokens"]
    clean = pd.DataFrame({"measured": [str(v) for v in range(100, 140)]})
    assert 5 not in _diseases(detect_all(clean))  # no residual -> disease 1's turf


def test_d06_whitespace() -> None:
    dirty = pd.DataFrame(
        {"city": ["  Vancouver", "Burnaby ", "Vic toria", "Surrey"] * 5}
    )
    res = detect_all(dirty)
    (f,) = _of(res, 6)
    assert f["grade"] == "AUTO"
    assert f["stats"]["count"] == 15
    clean = pd.DataFrame({"city": ["Vancouver", "Burnaby", "Victoria", "Surrey"] * 5})
    clean_res = detect_all(clean)
    assert 6 not in _diseases(clean_res)
    assert 6 in clean_res["clear"]


def test_d07_case_variants_auto() -> None:
    dirty = pd.DataFrame({"county": ["CLAY", "clay", "Clay", "KENT", "kent"] * 8})
    res = detect_all(dirty)
    (f,) = _of(res, 7)
    assert f["grade"] == "AUTO"  # pure case/whitespace sub-case
    assert f["stats"]["shrink"] > 0.1
    clean = pd.DataFrame({"county": ["clay", "kent", "york", "essex"] * 8})
    assert 7 not in _diseases(detect_all(clean))


def test_d07_fuzzy_variants_human() -> None:
    dirty = pd.DataFrame(
        {
            "material": ["weathered granite bedrock"] * 15
            + ["weathered granite bedrok"] * 5
            + ["silty clay loam"] * 20
        }
    )
    res = detect_all(dirty)
    (f,) = _of(res, 7)
    assert f["grade"] == "HUMAN"  # fuzzy sub-case needs a human mapping
    assert f["stats"]["fuzzy_pairs"] >= 1
    clean = pd.DataFrame(
        {"material": ["granite bedrock", "silty clay loam", "coarse sand"] * 12}
    )
    assert 7 not in _diseases(detect_all(clean))


def test_d07_fuzzy_evidence_quotes_original_case_not_folded_text() -> None:
    """_fuzzy_pairs clusters case/whitespace-folded values (it has to, to find
    the near-duplicates), but the evidence must quote the value as it really
    appears in the column — the same D04 mistake in a different detector: a
    model matching on the folded text would find nothing to fix."""
    dirty = pd.DataFrame(
        {
            "material": ["Weathered Granite Bedrock"] * 15
            + ["Weathered Granite Bedrok"] * 5
            + ["Silty Clay Loam"] * 20
        }
    )
    (f,) = [x for x in _of(detect_all(dirty), 7) if x["grade"] == "HUMAN"]
    assert "Weathered Granite Bedrock" in f["evidence"]
    assert "weathered granite bedrock" not in f["evidence"]


def test_d08_mojibake() -> None:
    dirty = pd.DataFrame(
        {"notes": ["cafÃ© rÃ©sumÃ©"] * 5 + ["naÃ¯ve entry"] * 3 + ["plain text"] * 10}
    )
    res = detect_all(dirty)
    (f,) = _of(res, 8)
    assert f["grade"] == "GATE"
    assert f["stats"]["count"] == 8
    assert f["confidence"] >= 0.8  # latin1->utf8 round-trip repaired the sample
    clean = pd.DataFrame({"notes": ["café résumé", "naïve entry", "plain"] * 6})
    assert 8 not in _diseases(detect_all(clean))


def test_d09_duplicate_rows() -> None:
    dirty = pd.DataFrame({"a": [1, 2, 3, 1, 2], "b": ["x", "y", "z", "x", "y"]})
    res = detect_all(dirty)
    (f,) = _of(res, 9)
    assert f["columns"] == []  # table-scoped
    assert f["grade"] == "AUTO"
    assert f["stats"]["dup_rows"] == 2
    assert f["confidence"] == 1.0
    clean = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    clean_res = detect_all(clean)
    assert 9 not in _diseases(clean_res)
    assert 9 in clean_res["clear"]


def test_d10_near_duplicate_rows() -> None:
    dirty = pd.DataFrame(
        {
            "org": ["ACME Corp", "acme corp", "Globex", "Initech"],
            "city": ["Kent", " kent", "Delta", "Surrey"],
        }
    )
    res = detect_all(dirty)
    (f,) = _of(res, 10)
    assert f["grade"] == "HUMAN"
    assert f["stats"]["near_dup_rows"] >= 1
    clean = pd.DataFrame(
        {"org": ["ACME Corp", "Globex", "Initech"], "city": ["Kent", "Delta", "Surrey"]}
    )
    assert 10 not in _diseases(detect_all(clean))


def test_d11_key_violations() -> None:
    ids = list(range(400)) + [42]
    values = [f"row {i}" for i in range(400)] + ["row 42 CONTRADICTS"]
    res = detect_all(pd.DataFrame({"record_id": ids, "label": values}))
    (f,) = _of(res, 11)
    assert f["grade"] == "HUMAN"
    assert "record_id" in f["columns"]
    assert f["stats"]["contradicted_keys"] == 1
    clean = pd.DataFrame(
        {"record_id": range(400), "label": [f"row {i}" for i in range(400)]}
    )
    assert 11 not in _diseases(detect_all(clean))


def test_d11_fires_when_the_key_is_heavily_damaged_but_not_on_float_dupes() -> None:
    """Bench triage 2026-09-02, the A1 shape: 10% duplicated ids dropped
    uniqueness under the 0.995 gate, so more damage meant more silence. An
    id-named or string key with contradiction-carrying duplicates is a
    violation at any damage level — while a merely-numeric column with
    coincidental duplicates (lat readings with a repeated sentinel) is not a
    key and must stay silent, however unique it looks."""
    ids = [f"TX{i:05d}" for i in range(250)]
    for k in range(25):  # 10% of keys overwritten with other existing keys
        ids[10 * k + 9] = ids[10 * k]
    frame = pd.DataFrame({"txn_id": ids, "label": [f"row {i}" for i in range(250)]})
    (f,) = _of(detect_all(frame), 11)
    assert f["grade"] == "HUMAN"
    assert "txn_id" in f["columns"]
    assert f["stats"]["contradicted_keys"] == 25

    floats = pd.DataFrame(
        {
            "lat": [49.2 + i * 0.01 for i in range(240)] + [999.0] * 10,
            "lon": [float(i) for i in range(250)],
        }
    )
    assert 11 not in _diseases(detect_all(floats))

    # a float column is never a key even in the near-perfect window: uniform
    # readings that collide once are measurements, not damaged identifiers
    readings = pd.DataFrame(
        {"reading": [float(i) for i in range(299)] + [7.0], "site": range(300)}
    )
    assert 11 not in _diseases(detect_all(readings))

    # an id-NAMED random attribute that merely collides (two 4-digit account
    # numbers repeating at n=250, the birthday effect) is noise, not a
    # damaged key — the wide path needs real damage before it may speak
    rng_like = [f"{1000 + (i * 37) % 8999}" for i in range(248)] + ["1037", "1074"]
    accounts = pd.DataFrame(
        {"account_no": rng_like, "note": [f"n{i}" for i in range(250)]}
    )
    assert 11 not in _diseases(detect_all(accounts))


def test_d11_sentinel_riddled_text_is_not_a_damaged_key() -> None:
    """A text column holding repeated sentinel tokens beside unique
    neighbours (12 x 'N/A' next to unique names) sits squarely in the wide
    uniqueness window and its 'duplicates' contradict on every attribute —
    but it is d04's patient, not a damaged key. Bare textiness must not
    qualify a column for the wide path; only an id-claiming NAME does."""
    frame = pd.DataFrame(
        {
            "name": [f"beer {i}" for i in range(32)],
            "ibu": ["N/A"] * 12 + [str(v) for v in range(20, 40)],
        }
    )
    assert 11 not in _diseases(detect_all(frame))


def test_d11_short_all_digit_codes_never_take_the_wide_path() -> None:
    """Heavy collision counts in a SHORT all-digit code are expected by the
    birthday effect (250 draws from 9000 four-digit values collide ~3.5
    times, unlucky seeds more), so the wide path must refuse short codes
    entirely — zips, PINs, account numbers duplicate by construction, not by
    damage. Silence here beats accusing every short code column forever."""
    heavy = [f"{1000 + (i * 37) % 8999}" for i in range(234)] + [
        f"{1000 + j * 11}" for j in range(8) for _ in range(2)
    ]
    frame = pd.DataFrame({"account_no": heavy, "note": [f"n{i}" for i in range(250)]})
    assert 11 not in _diseases(detect_all(frame))


def test_d12_fd_contradictions_indicator() -> None:
    words = ["oak", "elm", "fir", "ash", "yew", "ivy", "gum", "bay", "box", "may"]
    codes = [f"C{i:03d}" for i in range(50) for _ in range(4)]
    labels = [f"{words[i // 5]} {words[i % 5]}" for i in range(50) for _ in range(4)]
    labels[0] = "CONTRADICTING LABEL"  # one dissenting row in one group
    res = detect_all(pd.DataFrame({"code": codes, "label": labels}))
    finds = _of(res, 12)
    assert finds, "approx-FD violation not detected"
    assert all(f["indicator"] is True for f in finds)
    assert all(f["grade"] == "HUMAN" for f in finds)
    clean = pd.DataFrame(
        {
            "code": codes,
            "label": [
                f"{words[i // 5]} {words[i % 5]}" for i in range(50) for _ in range(4)
            ],
        }
    )
    assert 12 not in _diseases(detect_all(clean))


def test_d13_out_of_domain() -> None:
    dirty = pd.DataFrame({"age": [34, 45, 29, 150, -3, 61, 22, 58, 40, 33]})
    res = detect_all(dirty)
    (f,) = _of(res, 13)
    assert f["grade"] == "GATE"
    assert f["stats"]["violations"] == 2
    clean = pd.DataFrame({"age": [34, 45, 29, 61, 22, 58, 40, 33, 71, 5]})
    clean_res = detect_all(clean)
    assert 13 not in _diseases(clean_res)
    assert 13 in clean_res["clear"]


def test_d13_sign_anomaly_fires_without_a_named_domain() -> None:
    """Bench triage 2026-09-02: negatives planted in 'amount' matched no
    DOMAIN_BOUNDS name pattern, so d13 stayed silent. A column that is
    overwhelmingly non-negative with a stray few below zero is out of its own
    domain whatever it is called — while a genuinely signed column (deltas,
    balances) must stay silent."""
    amounts = [float(i % 97 + 1) for i in range(200)]
    amounts[13], amounts[77] = -812.5, -3.25
    (f,) = _of(detect_all(pd.DataFrame({"amount": amounts})), 13)
    assert f["grade"] == "GATE"
    assert f["stats"]["violations"] == 2
    assert "amount" in f["columns"]

    signed = pd.DataFrame({"delta": [float((-1) ** i * (i + 1)) for i in range(60)]})
    assert 13 not in _diseases(detect_all(signed))


def test_d14_broken_coordinates() -> None:
    assert (BC_LAT_MIN, BC_LAT_MAX) == (48.0, 60.0)
    assert (BC_LON_MIN, BC_LON_MAX) == (-139.0, -114.0)
    dirty = pd.DataFrame(
        {
            "lat": [49.2] * 10 + [0.0, 0.0, 91.5],
            "lon": [-123.1] * 10 + [0.0, 0.0, -123.0],
        }
    )
    res = detect_all(dirty)
    (f,) = _of(res, 14)
    assert f["grade"] == "GATE"
    assert set(f["columns"]) == {"lat", "lon"}
    assert f["stats"]["zero_zero"] == 2
    assert f["stats"]["out_of_range"] == 1
    clean = pd.DataFrame(
        {"lat": [49.2, 50.1, 54.3] * 4, "lon": [-123.1, -120.4, -128.0] * 4}
    )
    assert 14 not in _diseases(detect_all(clean))


def test_d15_statistical_outliers_indicator() -> None:
    dirty = pd.DataFrame({"flow": [45 + i % 10 for i in range(100)] + [500]})
    res = detect_all(dirty)
    (f,) = _of(res, 15)
    assert f["grade"] == "HUMAN"
    assert f["indicator"] is True
    assert f["stats"]["count"] == 1
    clean = pd.DataFrame({"flow": [45 + i % 10 for i in range(100)]})
    assert 15 not in _diseases(detect_all(clean))


def test_d16_unit_heterogeneity_explicit() -> None:
    dirty = pd.DataFrame(
        {
            "parameter": ["pm25", "pm25", "pm25", "o3"] * 5,
            "unit": ["ppb", "ug/m3", "ppb", "ppb"] * 5,
            "value": [10.0 + i for i in range(20)],
        }
    )
    res = detect_all(dirty)
    (f,) = _of(res, 16)
    assert f["grade"] == "AUTO"  # explicit unit column sub-case
    assert "unit" in f["columns"]
    clean = dirty.assign(unit=["ppb"] * 20)
    assert 16 not in _diseases(detect_all(clean))


def test_d16_unit_heterogeneity_inferred() -> None:
    metres = [3.0 + 0.02 * i for i in range(30)]
    feet = [round(m * 3.2808, 2) for m in metres]
    res = detect_all(pd.DataFrame({"depth": metres + feet}))
    (f,) = _of(res, 16)
    assert f["grade"] == "HUMAN"  # inferred bimodal sub-case
    assert abs(f["stats"]["ratio"] - 3.2808) / 3.2808 < 0.08
    clean = pd.DataFrame({"depth": metres * 2})
    assert 16 not in _diseases(detect_all(clean))


def test_d17_packed_fields() -> None:
    dirty = pd.DataFrame({"geom": [f"49.2{i}, -123.1{i}" for i in range(20)]})
    res = detect_all(dirty)
    (f,) = _of(res, 17)
    assert f["grade"] == "AUTO"
    assert f["stats"]["kind"] == "latlon_pair"
    clean = pd.DataFrame({"geom": [f"POINT {i}" for i in range(20)]})
    assert 17 not in _diseases(detect_all(clean))


def test_d17_packed_sibling_split_columns() -> None:
    dirty = pd.DataFrame(
        {
            "legal_line1": ["DISTRICT LO", "STRATA PLA", "LOT 4 BLOC"] * 5,
            "legal_line2": ["T 5 PLAN 88", "N VR2020", "K 2 DL 300"] * 5,
        }
    )
    res = detect_all(dirty)
    finds = _of(res, 17)
    assert any(set(f["columns"]) == {"legal_line1", "legal_line2"} for f in finds)
    clean = pd.DataFrame(
        {
            "legal_line1": ["LOT 5 PLAN 88", "PLAN VR2020", "LOT 4"] * 5,
            "legal_line2": ["", "", ""] * 5,
        }
    )
    assert not _of(detect_all(clean), 17)


def test_d18_header_damage() -> None:
    dirty = pd.DataFrame(
        [["s1", "n1", "x"], ["﻿station", "name", "name"], ["s2", "n2", "y"]],
        columns=["﻿station", "name", "name"],
    )
    res = detect_all(dirty)
    (f,) = _of(res, 18)
    assert f["grade"] == "AUTO"
    assert f["stats"]["bom_columns"] == 1
    assert f["stats"]["duplicate_columns"] >= 1
    assert f["stats"]["header_rows"] == 1
    clean = pd.DataFrame({"station": ["s1", "s2"], "name": ["n1", "n2"]})
    clean_res = detect_all(clean)
    assert 18 not in _diseases(clean_res)
    assert 18 in clean_res["clear"]


def test_d19_empty_and_constant_columns() -> None:
    dirty = pd.DataFrame(
        {
            "blank": [None] * 12,
            "const": ["x"] * 12,
            "varied": [str(i) + "v" for i in range(12)],
        }
    )
    res = detect_all(dirty)
    cols = {f["columns"][0] for f in _of(res, 19)}
    assert cols == {"blank", "const"}
    assert all(f["grade"] == "AUTO" for f in _of(res, 19))
    clean = pd.DataFrame({"varied": [str(i) + "v" for i in range(12)]})
    assert 19 not in _diseases(detect_all(clean))


def test_d20_schema_drift_family() -> None:
    a = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
    b = pd.DataFrame({"x": ["1", "2"], "z": [0.1, 0.2]})
    findings = detect_family({"file_a": a, "file_b": b})
    assert findings, "column-set + dtype drift not detected"
    f = findings[0]
    assert f["disease"] == 20
    assert f["slug"] == "schema-drift"
    assert "y" in f["stats"]["only_in_a"]
    assert "z" in f["stats"]["only_in_b"]
    assert "x" in f["stats"]["dtype_changes"]
    assert detect_family({"p": a, "q": a.copy()}) == []
    # single-frame scope: detect_all never runs disease 20
    res = detect_all(a)
    assert 20 not in _diseases(res)
    assert 20 not in res["clear"]


def test_d21_aggregate_rows() -> None:
    facilities = (["St. Mary", "Delta View", "Peace Arch", "Ridge"] * 9)[:36]
    dirty = pd.DataFrame(
        {
            "facility": facilities
            + ["All Facilities", "All Facilities", "Total", "Total"]
        }
    )
    res = detect_all(dirty)
    (f,) = _of(res, 21)
    assert f["grade"] == "GATE"
    assert "facility" in f["columns"]
    assert f["stats"]["rows"] == 4
    clean = pd.DataFrame({"facility": facilities})
    assert 21 not in _diseases(detect_all(clean))


def test_d22_id_numeric_corruption() -> None:
    zips = [
        35233,
        90210,
        60614,
        98101,
        87501,
        55401,
        73301,
        33101,
        44101,
        66101,
        77001,
        15201,
    ]
    dirty = pd.DataFrame({"zip_code": zips + [2115, 2116, 2117]})  # stripped zeros
    res = detect_all(dirty)
    (f,) = _of(res, 22)
    assert f["grade"] == "GATE"
    assert f["stats"]["modal_width"] == 5
    assert f["stats"]["shorter"] == 3
    clean = pd.DataFrame({"zip_code": zips})
    assert 22 not in _diseases(detect_all(clean))


def test_d22_catches_partially_eaten_string_ids() -> None:
    """Bench triage 2026-09-02: partial Excel damage leaves a MIXED string
    column — most ids still 'TX000123', a minority eaten to bare digits or
    scientific notation — which never parses as numbers, so the numeric-only
    d22 stayed silent. The mixed shape is the same disease and must fire."""
    ids = [f"TX{i:06d}" for i in range(100)]
    ids[7], ids[21], ids[63] = "123", "1.23E+05", "7042"
    (f,) = _of(detect_all(pd.DataFrame({"txn_id": ids, "v": range(100)})), 22)
    assert f["grade"] == "GATE"
    assert "txn_id" in f["columns"]
    assert f["stats"]["eaten"] == 3


def test_d22_intact_and_all_digit_string_ids_stay_silent() -> None:
    """False-positive discipline for the mixed-shape branch: an undamaged
    prefixed column has nothing eaten, and an all-digit string id column
    (zips) has no intact majority — both must stay silent."""
    intact = pd.DataFrame(
        {"txn_id": [f"TX{i:06d}" for i in range(100)], "v": range(100)}
    )
    assert 22 not in _diseases(detect_all(intact))
    zips = pd.DataFrame({"zip": [f"{90000 + i}" for i in range(100)], "v": range(100)})
    assert 22 not in _diseases(detect_all(zips))


def test_d22_sequential_ids_are_not_flagged() -> None:
    df = pd.DataFrame({"brewery_id": list(range(1, 400))})
    assert 22 not in _diseases(detect_all(df))


# --- contract-level behavior ------------------------------------------------


def _kitchen_sink() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "price": ["$1,200", "$3,400.50", "15 kg", "12.0 oz"] * 5,
            "status": (["ok", "N/A", "late", "N/A"] * 5),
            "city": ["  Vancouver", "Burnaby ", "Victoria", "Surrey"] * 5,
            "flow": [45 + i % 10 for i in range(19)] + [900],
        }
    )


def test_finding_schema_and_ordering() -> None:
    res = detect_all(_kitchen_sink(), name="sink")
    assert res["findings"], "kitchen sink produced no findings"
    for f in res["findings"]:
        assert set(f) == FINDING_KEYS
        assert isinstance(f["disease"], int) and 1 <= f["disease"] <= 22
        assert f["slug"] == f["slug"].lower() and " " not in f["slug"]
        assert isinstance(f["columns"], list)
        assert all(isinstance(c, str) for c in f["columns"])
        assert isinstance(f["evidence"], str) and "\n" not in f["evidence"]
        assert isinstance(f["stats"], dict)
        assert f["grade"] in {"AUTO", "GATE", "HUMAN"}
        assert isinstance(f["confidence"], float) and 0.0 <= f["confidence"] <= 1.0
        assert isinstance(f["indicator"], bool)
        assert f["indicator"] is (f["disease"] in {12, 15})
    nums = [f["disease"] for f in res["findings"]]
    assert nums == sorted(nums)
    assert set(res["clear"]).isdisjoint(_diseases(res))
    assert set(res["clear"]) | _diseases(res) == (set(range(1, 20)) | {21, 22})


def test_json_serializable_round_trip() -> None:
    res = detect_all(_kitchen_sink())
    assert json.loads(json.dumps(res)) == res


def test_detect_one_matches_detect_all_and_clears() -> None:
    df = _kitchen_sink()
    res = detect_all(df)
    for f in res["findings"]:
        again = detect_one(df, f["disease"], f["columns"])
        assert again is not None
        assert again["disease"] == f["disease"]
        assert again["columns"] == f["columns"]
    fixed = df.assign(city=df["city"].str.strip())
    assert detect_one(fixed, 6, ["city"]) is None


def test_detect_one_rejects_bad_disease_numbers() -> None:
    df = pd.DataFrame({"a": [1, 2]})
    with pytest.raises(ValueError):
        detect_one(df, 0, [])
    with pytest.raises(ValueError):
        detect_one(df, 23, [])
    with pytest.raises(ValueError):
        detect_one(df, 20, [])  # family-scoped; use detect_family


def test_pathological_frames_do_not_raise() -> None:
    assert detect_all(pd.DataFrame())["findings"] == []
    assert detect_all(pd.DataFrame(columns=["a", "b"]))["findings"] == []
    detect_all(pd.DataFrame({"n": [float("nan")] * 5}))
    dup = pd.DataFrame([[1, "x"], [1, "x"], [2, "y"]], columns=["k", "k"])
    assert 9 in _diseases(detect_all(dup))
    weird = pd.DataFrame(
        {"obj": [[1, 2], {"a": 1}, None, [1, 2]] * 3, "n": [1, 2, 3, 1] * 3}
    )
    detect_all(weird)  # unhashable + mixed object cells must not error


def test_detect_all_reports_a_broken_detector_instead_of_hiding_it(monkeypatch) -> None:
    """clear makes absence a checked claim: 'this signal ran and found
    nothing.' A detector that raises must not quietly vanish from both
    findings and clear as if it had simply run clean — that makes a
    systematically broken signal invisible forever."""
    from crivo.detect import REGISTRY

    def boom(df, cols):
        raise ValueError("synthetic failure")

    monkeypatch.setitem(REGISTRY, 6, boom)
    df = pd.DataFrame({"city": ["  Vancouver", "Burnaby ", "Victoria", "Surrey"] * 5})
    res = detect_all(df)
    assert 6 not in _diseases(res)
    assert 6 not in res["clear"]
    assert "ValueError" in res["broken"]["6"]
    assert "synthetic failure" in res["broken"]["6"]
    json.dumps(res)  # still serialisable with the new key populated


# --- Raha integration (AC1) -------------------------------------------------


@needs_raha
def test_raha_beers_units_and_sentinels() -> None:
    res = detect_all(_raha("beers", "dirty"), name="beers")
    ds = _diseases(res)
    assert 1 in ds and 4 in ds
    assert any(f["columns"] == ["ounces"] for f in _of(res, 1))  # "12.0 oz"
    assert any(f["columns"] == ["ibu"] for f in _of(res, 4))  # "N/A"
    assert set(res["clear"]).isdisjoint(ds)
    assert set(res["clear"]) | ds == (set(range(1, 20)) | {21, 22})


@needs_raha
def test_raha_hospital_case_spelling_variants() -> None:
    res = detect_all(_raha("hospital", "dirty"), name="hospital")
    finds = _of(res, 7)
    assert finds, "hospital's typo variants not detected"
    assert any(f["grade"] == "HUMAN" for f in finds)  # fuzzy sub-case


@needs_raha
def test_raha_flights_dates_and_contradiction_indicator() -> None:
    res = detect_all(_raha("flights", "dirty"), name="flights")
    ds = _diseases(res)
    assert 2 in ds or 3 in ds
    assert 12 in ds
    assert all(f["indicator"] is True for f in _of(res, 12))
    assert all(f["grade"] == "HUMAN" for f in _of(res, 12))


@needs_raha
def test_raha_flights_clean_has_no_contradictions() -> None:
    assert 12 not in _diseases(detect_all(_raha("flights", "clean")))


@needs_raha
def test_raha_detect_one_verifier_round_trip() -> None:
    dirty = _raha("beers", "dirty")
    found = detect_one(dirty, 4, ["ibu"])
    assert found is not None and found["disease"] == 4
    # clean.csv drops the N/A tokens (blank cells), so the same probe clears
    assert detect_one(_raha("beers", "clean"), 4, ["ibu"]) is None


@needs_raha
def test_raha_movies_runs_and_serializes() -> None:
    res = detect_all(_raha("movies_1", "dirty"), name="movies")
    assert res["findings"]
    assert 17 in _diseases(res)  # comma-packed actors/genre lists
    json.dumps(res)


def test_d04_evidence_quotes_the_tokens_as_they_actually_appear() -> None:
    """The model writes its fix against this string. Reporting a normalised
    'n/a' when the data holds 'N/A' produces a fix that matches nothing —
    observed live, where the fix ran clean and changed zero rows."""
    dirty = pd.DataFrame({"flow": ["N/A"] * 10 + [str(v) for v in range(20)]})
    (f,) = _of(detect_all(dirty), 4)
    assert "N/A" in f["evidence"]
    assert f["stats"]["tokens"] == ["N/A"]


def test_d06_evidence_preserves_the_double_space_it_reports() -> None:
    """The evidence sanitiser used to collapse every whitespace run down to
    one space, which erased the exact anomaly disease 6 exists to report —
    the model was told a value carries stray whitespace while being shown a
    value that looks perfectly clean. Observed live: three failed attempts."""
    dirty = pd.DataFrame(
        {"beer_name": ["Yeti  Imperial Stout"] * 3 + ["Clean Name"] * 10}
    )
    (f,) = _of(detect_all(dirty), 6)
    assert "Yeti  Imperial Stout" in f["evidence"]  # the real double space survives
    assert "\n" not in f["evidence"]  # the no-newline contract still holds


def test_d06_compatibility_characters_are_not_reported_as_whitespace_damage() -> None:
    """_d06 used to compare values against a full-NFKC normalisation, and NFKC
    expands compatibility characters like (TM) into multi-character forms
    ('TM'); the difference got reported as stray whitespace. It is not
    whitespace — three of the four real beers findings were this false
    positive."""
    dirty = pd.DataFrame(
        {
            "beer_name": ["The CROWLER™"] * 3
            + ["GreyBeard™ IPA"] * 3
            + ["Clean Name"] * 10
        }
    )
    assert 6 not in _diseases(detect_all(dirty))


VANCOUVER = Path(__file__).resolve().parent.parent / "data" / "vancouver"
needs_vancouver = pytest.mark.skipif(
    len(list(VANCOUVER.glob("property-tax-*.csv"))) < 2,
    reason="fewer than two Vancouver slices — run scripts/fetch_vancouver.py",
)


@needs_vancouver
def test_detect_family_finds_the_real_vancouver_era_drift() -> None:
    """The compounding demo rests on this being true of real files: the
    2006-2010 era has no `note` column and types drift across eras."""
    # earliest and latest available, so the pair straddles the era boundary
    # whatever subset of years happens to be downloaded
    slices = sorted(VANCOUVER.glob("property-tax-*.csv"))
    frames = {
        p.stem: pd.read_csv(p, sep=";", encoding="utf-8-sig", nrows=500)
        for p in (slices[0], slices[-1])
    }
    (finding,) = detect_family(frames)

    assert finding["disease"] == 20
    assert finding["grade"] == "GATE"
    drifted = set(finding["stats"]["only_in_a"]) | set(finding["stats"]["only_in_b"])
    assert "note" in drifted, "the era boundary the harmonizer exists to bridge"


def test_detect_one_refuses_to_call_a_crash_a_clean_signal(monkeypatch) -> None:
    """detect_one IS verification layer 1: verify.py asserts `_v is None` to
    mean the fix worked. Swallowing a detector crash and returning None turns
    every fix for that disease into a silent pass — it manufactures proof
    rather than merely hiding a disease, which is what detect_all's `broken`
    key handles."""
    df = pd.DataFrame({"city": ["  Vancouver", "Burnaby"] * 10})
    assert detect_one(df, 6, ["city"]) is not None

    def explode(frame, cols):
        raise ValueError("boom")

    monkeypatch.setitem(detect.REGISTRY, 6, explode)
    with pytest.raises(ValueError, match="boom"):
        detect_one(df, 6, ["city"])


def test_d06_knows_which_invisible_characters_are_damage() -> None:
    """Zero-width is not one category. ZWJ and ZWNJ carry meaning — they build
    emoji and Indic conjuncts — so mapping them to a space both misreports them
    as whitespace damage and splits the word the fix claims to repair. Real
    Unicode spaces are the opposite problem: dropping NFKC made every one but
    NBSP invisible, and the run then asserts the signal ran and found nothing."""

    def fires(values):
        res = detect_all(pd.DataFrame({"c": values * 6 + ["ops team"] * 10}))
        return any(f["disease"] == 6 for f in res["findings"]), res

    # meaning-bearing: must be left entirely alone
    for label, value in [
        ("emoji ZWJ", "\U0001f469‍\U0001f4bb team"),
        ("Devanagari ZWJ", "क्‍ष team"),
        ("ZWNJ", "‌ team"),
    ]:
        hit, _ = fires([value])
        assert not hit, f"{label} is not whitespace damage"

    # genuine damage: every Unicode space, not just NBSP
    for label, ch in [
        ("NBSP", "\xa0"),
        ("NARROW NBSP", " "),
        ("FIGURE", " "),
        ("IDEOGRAPHIC", "　"),
    ]:
        hit, res = fires([f"North{ch}Vancouver"])
        assert hit, f"{label} went undetected"
        assert 6 not in res["clear"], f"{label} was undetected AND claimed clear"

    # a zero-width space is damage, and repairing it must not split the word
    hit, _ = fires(["Bud\u200bweiser"])
    assert hit
    assert detect._ws_tidy(pd.Series(["Bud\u200bweiser"])).iloc[0] == "Budweiser"


def test_evidence_stays_one_line_for_every_line_breaker() -> None:
    """The narrowed sanitiser is weaker than the str.split() it replaced: only
    \\r\\n\\t are handled, so every other line-breaking character survives. Column
    names reach the evidence uninterpolated (_d11, _d12, _d20), and a header
    carrying a form feed or U+2028 — mainframe, PDF- and web-derived exports —
    then splits a diagnosis line or a report bullet across two records."""
    for ch in ("\x0b", "\x0c", "\x85", " ", " ", "\r\n", "\t"):
        finding = detect._finding(6, ["c"], f"before{ch}after", {}, "AUTO", 0.9)
        assert len(finding["evidence"].splitlines()) == 1, repr(ch)
        assert "before" in finding["evidence"] and "after" in finding["evidence"]


def test_d06_damage_between_probe_strides_is_still_found() -> None:
    """_probe consults every k-th row, and the gate turned its silence into
    "checked and clean" — a probabilistic CLEAR whose odds depended on where
    the damage happened to sit. The probe may fast-path the common case, but
    it never gets to decide the claim: a probe-negative is confirmed on the
    full column (one compiled-regex pass, ~21ms per 300k rows) before d06 is
    allowed into the clear list."""
    values = ["Professional Services"] * 12_000
    values[1] = "Professional  Services"  # doubled space, off every stride

    found = detect_one(pd.DataFrame({"desc": values}), 6, ["desc"])

    assert found is not None, "sampled silence became a false clear"
    assert "1/12000" in found["evidence"] or "1/12,000" in found["evidence"], found[
        "evidence"
    ]


def test_d17_counts_are_measured_where_they_are_claimed() -> None:
    """_pack_kind decides the kind on a probe, and the evidence then asserted
    that every value packs ("19992 values pack ...") — a full-column claim
    from sampled evidence. The count in the evidence must be the measured
    full-column count, with the denominator beside it."""
    values = [f"a|b|c{i}" for i in range(85)] + [f"plain {i}" for i in range(15)]

    found = detect_one(pd.DataFrame({"tags": values}), 17, ["tags"])

    assert found is not None
    assert "85/100" in found["evidence"], found["evidence"]
    assert found["stats"]["packed"] == 85


def test_d17_does_not_read_thousands_commas_as_packed_fields() -> None:
    """Observed live on HM Treasury's Amount column: '26,594.25' fired d17 as
    comma_list, overlapping d01 — the commas are number formatting, not field
    packing. A number-shaped column is d01's subject; a genuine comma-packed
    column must still fire, and pipe/semicolon lists are untouched because
    nothing writes money with those."""
    money = [f"{v:,.2f}" for v in range(10_000, 10_040)]
    assert detect_one(pd.DataFrame({"amount": money}), 17, ["amount"]) is None

    packed = [f"north{i},south{i},east{i}" for i in range(40)]
    found = detect_one(pd.DataFrame({"tags": packed}), 17, ["tags"])
    assert found is not None
    assert found["stats"]["kind"] == "comma_list"


def test_the_unicode_space_table_matches_the_unicode_database() -> None:
    """_UNICODE_SPACES is a literal so importing detect stops scanning all
    1,114,112 codepoints — ~60ms on every kernel start and crash replay. A
    literal is only safe while it matches the running Python's Unicode
    database, so this recomputation is that scan, moved from every import
    into the suite."""
    import unicodedata

    zs = {c for c in range(0x110000) if unicodedata.category(chr(c)) == "Zs"}
    assert detect._UNICODE_SPACES == zs


def test_a_broken_detectors_reason_stays_one_bounded_line() -> None:
    """`broken` was the one string in the pipeline with no sanitiser and no
    length bound. A detector crash is an arbitrary third-party exception, and
    pandas messages routinely embed newlines and long index reprs — rendered
    raw as a markdown bullet, the reason split the report line in half:
    verbatim the failure EVIDENCE_LINEBREAKS was added to stop. Same collapse,
    same cap, for every line-breaking character an exception can carry."""

    def boom(df, cols):
        raise ValueError("cannot do it\nHint: reindex\u2028the axis\r\n" + "x" * 900)

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(detect.REGISTRY, 7, boom)
        result = detect_all(pd.DataFrame({"a": ["x"] * 9}))

    reason = result["broken"]["7"]
    assert len(reason.splitlines()) == 1, repr(reason)
    assert "cannot do it" in reason and "Hint" in reason
    assert len(reason) <= 300, f"{len(reason)} chars for one report bullet"


def test_d06_exotic_count_is_the_number_of_invisible_characters() -> None:
    """Ground truth computed here, without importing detect's own tables — a
    test that recomputes a number using the code under test only proves the
    code agrees with itself. Building the damage table from Unicode Zs swept in
    U+0020, so 'N with NBSP/zero-width characters' counted every value that had
    an ordinary space: 15 of 20 where the true answer was 5."""
    values = ["  Vancouver", "Burnaby ", "Vic\xa0toria", "Surrey"] * 5
    invisible = sum(
        # named by codepoint, never by literal: an invisible character
        # in a fixture is unreviewable — the exact hazard this file
        # exists to catch elsewhere
        any(ord(c) in {0xA0, 0x200B, 0xFEFF, 0x2007, 0x202F, 0x3000} for c in v)
        for v in values
    )
    (f,) = _of(detect_all(pd.DataFrame({"city": values})), 6)

    assert f["stats"]["exotic"] == invisible == 5
    assert "5 with NBSP" in f["evidence"], f["evidence"]


MACHINE_STAMPS = {
    "zulu": "2024-04-{day:02d}T14:33:00Z",
    "numeric-offset": "2024-04-{day:02d}T14:33:00+00:00",
    "fractional-zoned": "2024-04-{day:02d}T14:33:00.123Z",
    "fractional-naive": "2024-04-{day:02d}T14:33:00.123456",
    "space-separated-offset": "2024-04-{day:02d} 14:33:00+00:00",
}


@pytest.mark.parametrize("shape", sorted(MACHINE_STAMPS))
def test_d02_claims_every_machine_timestamp_shape(shape) -> None:
    """The T/Z form is *the* timestamp format in machine-written exports —
    transaction feeds, API dumps, Postgres — and no family claimed it, so a
    column of nothing but export timestamps was reported "checked and clean".
    Parameterised over every zone-suffix and fraction shape the wild produces,
    not the one that surfaced the gap."""
    values = [MACHINE_STAMPS[shape].format(day=i % 27 + 1) for i in range(40)]
    found = detect_one(pd.DataFrame({"posted_at": values}), 2, ["posted_at"])
    assert found is not None, f"{shape} timestamps were reported clean"


def test_d03_treats_a_naive_and_zoned_mix_as_two_formats() -> None:
    """Zoned is deliberately a separate family from naive iso: the two cannot
    land in one datetime64 without a decision, so the mix is d03's subject —
    the case a single wider 'iso' pattern would have silently absorbed."""
    values = ["2024-04-13 14:33:00", "2024-04-13T14:33:00Z"] * 20
    found = detect_one(pd.DataFrame({"t": values}), 3, ["t"])
    assert found is not None
    assert "2 date formats" in found["evidence"], found["evidence"]


NON_UNIQUE_INDEXES = {
    "one-label": lambda n: pd.Index([0] * n),
    "low-cardinality": lambda n: pd.Index([i % 4 for i in range(n)]),
    "string-key": lambda n: pd.Index(["USD", "EUR"] * (n // 2)),
}


@pytest.mark.parametrize("shape", sorted(NON_UNIQUE_INDEXES))
def test_findings_do_not_depend_on_the_frames_index(shape) -> None:
    """set_index() on a low-cardinality column — currency, category, a date
    bucket — is the standard first move on a transaction table. Label-aligned
    boolean masks then pay a non-unique lookup per row and detection goes
    quadratic: measured >90s on 2,000 rows sharing one label, and nothing
    catches a hang the way detect_all's per-detector except catches a crash.
    The invariant: same data, same findings, comparable time, any index."""
    from time import perf_counter

    # 2,000 is calibrated, not arbitrary: at n=1,000 the low-cardinality shape
    # sits inside the 5s bound even unfixed (multiplicity 250 is not deep
    # enough into the quadratic), so the test would pass while the bug lives.
    n = 2000
    data = {
        "material": ["Steel", "steel", "STEEL", "Alum", "alum"] * (n // 5),
        "price": [f"${v}.00" for v in range(n)],
    }
    baseline = detect_all(pd.DataFrame(data), "txn")
    assert 1 in _diseases(baseline) and 7 in _diseases(baseline)

    started = perf_counter()
    result = detect_all(pd.DataFrame(data, index=NON_UNIQUE_INDEXES[shape](n)), "txn")
    elapsed = perf_counter() - started

    assert result == baseline
    assert elapsed < 5, f"{elapsed:.1f}s at {n:,} rows — label alignment is quadratic"
