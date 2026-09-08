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
    """This fixture mixes dollars, kilograms and ounces in one column, so the
    finding is real but the repair is not mechanical. The grade assertion was
    AUTO, and the numeric frame below was literally what the AUTO fixer
    produced: three incompatible quantities flattened into one float column,
    and verified, because a numeric column can no longer trip a text signal.
    The numeric half of the test still stands on its own claim, that a real
    numeric column is reported clear."""
    dirty = pd.DataFrame(
        {"price": ["$1,200", "$3,400.50", "15 kg", "12.0 oz", "980"] * 4}
    )
    res = detect_all(dirty)
    assert 1 in _diseases(res)
    (f,) = _of(res, 1)
    assert f["columns"] == ["price"]
    assert f["grade"] == "HUMAN"
    assert f["confidence"] >= 0.9
    numeric = pd.DataFrame({"price": [1200.0, 3400.5, 15.0, 12.0, 980.0] * 4})
    numeric_res = detect_all(numeric)
    assert 1 not in _diseases(numeric_res)
    assert 1 in numeric_res["clear"]


def test_d01_id_like_digit_strings_are_not_flagged() -> None:
    df = pd.DataFrame({"provider_id": [str(10000 + i) for i in range(20)]})
    assert 1 not in _diseases(detect_all(df))


MIXED_UNITS = [
    "15 kg",
    "33 lb",
    "12 oz",
    "8 kg",
    "150 lb",
    "24 oz",
    "3 kg",
    "90 lb",
] * 5


def test_d01_mixed_unit_suffixes_never_grade_auto() -> None:
    """The unit-suffix family reads every '<number> <letters>' as one shape,
    so a column of kilograms, pounds and ounces looked uniform and graded
    AUTO at confidence 1.0. The fixer then stripped the suffixes and left
    15, 33, 12 in one numeric column, and verification passed BECAUSE of the
    damage: the column was no longer text, so d01 could not see it again.

    Which unit survives, and at what factor, is domain knowledge the data
    does not carry, so the honest answer is to hand the column to a person
    with the units named."""
    found = detect_one(pd.DataFrame({"weight": MIXED_UNITS}), 1, ["weight"])

    assert found is not None, "the column is still numbers-as-strings"
    assert found["grade"] == "HUMAN", found["grade"]
    assert found["stats"]["units"] == ["kg", "lb", "oz"], found["stats"]
    for unit in ("kg", "lb", "oz"):
        assert unit in found["evidence"], found["evidence"]


def test_a_mixed_unit_column_survives_clean_untouched() -> None:
    """The grade is only half the promise. HUMAN keeps the column out of the
    auto path entirely, so `clean` must hand it back exactly as it found it
    and file it for review rather than report a verified fix."""
    from crivo.autoclean import clean

    before = pd.DataFrame({"weight": MIXED_UNITS})
    after, summary = clean(before)

    assert summary.applied == [], summary.applied
    assert any(r["disease"] == 1 for r in summary.needs_review), summary.needs_review
    pd.testing.assert_frame_equal(after, before)


def test_d01_one_unit_spelled_many_ways_is_still_an_auto_fix() -> None:
    """The other side of the refusal, and the line it must not cross. Raha's
    beers column spells a single unit six ways ('12.0 oz', '12.0 oz.',
    '12.0 OZ.', '12.0 ounce', '12.0 oz. Alumi-Tek'); every value is ounces,
    so stripping the suffix loses nothing and the fix stays mechanical.
    Refusing here would be over-refusal, not caution."""
    spellings = ["12.0 oz", "16.0 oz.", "12.0 ounce", "16.0 OZ.", "24.0 oz. Alumi-Tek"]
    found = detect_one(pd.DataFrame({"ounces": spellings * 8}), 1, ["ounces"])

    assert found is not None
    assert found["grade"] == "AUTO", found["grade"]
    assert found["stats"]["units"] == ["ounce"], found["stats"]


def test_d01_one_stray_unit_is_enough_to_stop_the_auto_fix() -> None:
    """A single 'kg' among the ounces is not noise to average away: those
    rows would be merged into the same numeric column at the wrong scale,
    silently, and nothing downstream could tell. Not all the same unit means
    a person looks, whatever the minority share."""
    values = ["12.0 oz", "16.0 oz.", "12.0 ounce"] * 13 + ["15 kg"]
    found = detect_one(pd.DataFrame({"ounces": values}), 1, ["ounces"])

    assert found is not None
    assert found["grade"] == "HUMAN", found["grade"]
    assert found["stats"]["units"] == ["kg", "ounce"], found["stats"]


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


def test_d10_catches_near_dups_with_tiny_numeric_drift() -> None:
    """Bench 2026-09-02: 'near' meant duplicate-after-text-folding only, so a
    copied row with amount +0.01 was invisible. Rows identical everywhere but
    one numeric column whose values sit within a hair of each other are the
    same entity recorded twice — while genuine repeat business (same purchase
    shape, a DIFFERENT id) is a real second transaction and must stay
    silent: ids aren't close, they're different."""
    base = pd.DataFrame(
        {
            "txn_id": [f"TX{i:05d}" for i in range(40)],
            "merchant": [f"shop {i % 7}" for i in range(40)],
            "amount": [float(10 + i) for i in range(40)],
        }
    )
    tweaked = base.iloc[[3, 11, 27]].assign(amount=lambda d: d["amount"] + 0.01)
    dirty = pd.concat([base, tweaked], ignore_index=True)
    (f,) = _of(detect_all(dirty), 10)
    assert f["stats"]["near_dup_rows"] == 3

    repeats = base.iloc[[5, 9]].assign(txn_id=["TX90001", "TX90002"])
    genuine = pd.concat([base, repeats], ignore_index=True)
    assert 10 not in _diseases(detect_all(genuine))


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


def test_d05_fires_on_a_mixed_float_and_token_column() -> None:
    """Bench 2026-09-02: the realistic degrade keeps unsuppressed cells as
    floats (object column of float + '<5'/'SUPPRESSED'), and `.str` ops on a
    mixed Series silently NaN every float — the parse gate then read ~0% and
    d5 stayed silent on exactly the shape it exists for."""
    mixed = pd.DataFrame(
        {"score": [float(v) for v in range(20, 47)] + ["<5", "SUPPRESSED", "<10"]}
    )
    (f,) = _of(detect_all(mixed), 5)
    assert f["stats"]["flagged"] == 3
    assert "<5" in str(f["stats"]["tokens"])
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


def test_d13_implausible_dates() -> None:
    """Taxonomy v2 fold (arc W2): datetime columns get domain sense too —
    far-future timestamps and exact-epoch artifacts (1970-01-01 00:00:00 is
    what a zeroed integer becomes, not a date anyone typed). Ordinary
    historical dates are legitimate and must stay silent."""
    seen = pd.Series(pd.date_range("2023-01-01", periods=26, freq="D"))
    seen.iloc[3] = pd.Timestamp("2091-05-01")
    seen.iloc[11] = pd.Timestamp("1970-01-01")
    seen.iloc[19] = pd.Timestamp("1970-01-01")
    (f,) = _of(detect_all(pd.DataFrame({"seen_at": seen})), 13)
    assert f["grade"] == "GATE"
    assert f["stats"]["violations"] == 3

    history = pd.Series(pd.date_range("1981-06-01", periods=26, freq="ME"))
    assert 13 not in _diseases(detect_all(pd.DataFrame({"born": history})))


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


def test_d14_detects_in_range_coordinate_swaps() -> None:
    """Bench 2026-09-02: a lat/lon swap whose values stay globally valid was
    invisible — d14 only knew null island and out-of-range. When the two
    columns' robust bands are disjoint, a row sitting in each other's band is
    an exchange; when the bands overlap (regional data straddling the same
    values), the signature is undefined and must stay silent."""
    lats = [40.0 + (i % 100) / 10 for i in range(60)]
    lons = [60.0 + (i % 200) / 10 for i in range(60)]
    for k in (5, 25, 45):  # swapped: globally valid, in each other's band
        lats[k], lons[k] = lons[k], lats[k]
    (f,) = _of(detect_all(pd.DataFrame({"lat": lats, "lon": lons})), 14)
    assert f["stats"]["swapped"] == 3

    overlap = pd.DataFrame(
        {
            "lat": [50.0 + (i % 60) / 10 for i in range(60)],
            "lon": [52.0 + (i % 60) / 10 for i in range(60)],
        }
    )
    assert 14 not in _diseases(detect_all(overlap))


def test_d18_padded_column_names_fire() -> None:
    """Arc W2 (found by the header-fixer build): ' amount ' style padded
    names were invisible to d18 even though the fixer repairs them — the
    detector must see everything its fixer can fix, or verify-or-revert has
    a blind edge."""
    frame = pd.DataFrame({" amount ": [1.0] * 12, "note  x": ["a"] * 12})
    (f,) = _of(detect_all(frame), 18)
    assert f["stats"]["padded"] == 2

    healthy = pd.DataFrame({"amount": [1.0] * 12, "note": ["a"] * 12})
    assert 18 not in _diseases(detect_all(healthy))


def test_d21_a_single_total_row_with_a_sum_signature_fires() -> None:
    """Bench 2026-09-02: the count >= 2 floor silenced the classic case — ONE
    trailing TOTAL row. A lone aggregate label may fire only when the row
    corroborates as an aggregate (some numeric value ≈ the sum of the rest);
    a merchant legitimately NAMED 'Total' with ordinary numbers stays silent."""
    amounts = [float(10 + i) for i in range(30)]
    frame = pd.DataFrame(
        {
            "merchant": [f"shop {i}" for i in range(30)] + ["TOTAL"],
            "amount": amounts + [sum(amounts)],
        }
    )
    (f,) = _of(detect_all(frame), 21)
    assert f["grade"] in {"GATE", "HUMAN"}

    innocent = pd.DataFrame(
        {
            "merchant": [f"shop {i}" for i in range(30)] + ["Total"],
            "amount": amounts + [55.0],
        }
    )
    assert 21 not in _diseases(detect_all(innocent))


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


def test_d22_catches_excel_apostrophe_guards() -> None:
    """Taxonomy v2 fold (arc W2): a leading apostrophe is Excel's text-guard
    leaking into the data ("'000123") — same disease family as eaten zeros:
    a numeric cast somewhere mangled the id column's representation."""
    ids = [f"{100000 + i:06d}" for i in range(100)]
    for k in (7, 21, 63):
        ids[k] = "'" + ids[k]
    (f,) = _of(detect_all(pd.DataFrame({"account_id": ids, "v": range(100)})), 22)
    assert f["grade"] == "GATE"
    assert f["stats"]["guarded"] == 3


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
        assert isinstance(f["disease"], int) and f["disease"] in detect.SLUGS
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
    assert set(res["clear"]) | _diseases(res) == set(detect.SINGLE_FRAME)


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


def _duplicated_columns() -> pd.DataFrame:
    body = [f"v{i}" for i in range(20)]
    return pd.DataFrame({"a": body, "b": body, "c": body, "keep": list(range(20))})


def test_detect_one_refuses_a_partial_fix_on_a_multi_column_finding() -> None:
    """Verification compared the residual finding's column list to the
    original one for equality, so half a repair passed: three identical
    columns reported as one d25 finding, one of them made distinct, and the
    surviving pair came back under a SHORTER list that could never be equal.
    The signal is still firing on columns the fix claimed to have cleared."""
    df = _duplicated_columns()
    ((cols),) = [f["columns"] for f in detect_all(df)["findings"] if f["disease"] == 25]
    assert cols == ["a", "b", "c"]

    half = df.assign(c=[f"w{i}" for i in range(20)])  # only c made distinct
    residual = detect_one(half, 25, cols)

    assert residual is not None, "a half-repaired duplicate group is not repaired"
    assert residual["columns"] == ["a", "b"]


def test_detect_one_refuses_a_fix_that_renames_the_diseased_column() -> None:
    """Renaming the column moves it out of the detector's reach, which used
    to be indistinguishable from curing it: the re-run found the same
    whitespace damage under the new name, and the new name never equalled
    the old one, so the finding was discarded and the fix called verified."""
    dirty = pd.DataFrame(
        {"city": ["  Vancouver", "Burnaby ", "Victoria", "Surrey "] * 5}
    )
    assert detect_one(dirty, 6, ["city"]) is not None

    renamed = dirty.rename(columns={"city": "city_clean"})  # nothing repaired

    residual = detect_one(renamed, 6, ["city"])
    assert residual is not None, "a renamed column is not a repaired column"
    assert residual["columns"] == ["city"]
    assert "no longer in the frame" in residual["evidence"]


def test_detect_one_refuses_a_fix_that_deletes_the_diseased_column() -> None:
    """The worst version of the same hole: dropping the column deletes the
    data AND the evidence, and the signal that cannot run then reads as a
    signal that found nothing. Losing a column is never a repair."""
    dirty = pd.DataFrame(
        {"city": ["  Vancouver", "Burnaby ", "Victoria", "Surrey "] * 5, "n": range(20)}
    )
    residual = detect_one(dirty.drop(columns=["city"]), 6, ["city"])

    assert residual is not None
    assert residual["disease"] == 6 and residual["grade"] == "HUMAN"


def test_detect_one_still_verifies_rename_and_drop_repairs() -> None:
    """The exception the missing-column rule has to carry: d18's repair IS a
    rename and d19's IS a drop, so for those two the original name being gone
    is the fix working, not the check being dodged (COLUMN_CHANGING)."""
    from crivo.autoclean import _drop_constant, _fix_headers

    damaged = pd.DataFrame(
        {"  Region ": range(12), "Unnamed: 1": range(12), "ok": list("abcdefghijkl")}
    )
    ((cols),) = [
        f["columns"] for f in detect_all(damaged)["findings"] if f["disease"] == 18
    ]
    assert detect_one(_fix_headers(damaged, cols), 18, cols) is None

    constant = pd.DataFrame({"k": range(12), "dead": ["x"] * 12})
    assert detect_one(_drop_constant(constant, ["dead"]), 19, ["dead"]) is None


def test_detect_one_rejects_bad_disease_numbers() -> None:
    df = pd.DataFrame({"a": [1, 2]})
    with pytest.raises(ValueError):
        detect_one(df, 0, [])
    with pytest.raises(ValueError):
        detect_one(df, 99, [])  # forever off the taxonomy
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


def test_d23_boolean_chaos() -> None:
    """Taxonomy v2 (arc W2): one truth, many spellings — Y/yes/TRUE/1 mixed in
    a single column. A CONSISTENT convention (a clean Y/N pair) is fine; the
    disease is representation mixing, so it fires only when the distinct
    values span two or more boolean spelling families."""
    mixed = pd.DataFrame(
        {"active": ["Y", "N", "yes", "no", "TRUE", "FALSE", "1", "0"] * 5}
    )
    (f,) = _of(detect_all(mixed), 23)
    assert f["grade"] == "AUTO"
    assert "active" in f["columns"]

    consistent = pd.DataFrame({"active": ["Y", "N"] * 20})
    assert 23 not in _diseases(detect_all(consistent))
    numericish = pd.DataFrame({"flag": ["0", "1"] * 20})  # one family: not chaos
    assert 23 not in _diseases(detect_all(numericish))


def test_d24_stray_header_and_footer_rows() -> None:
    """Taxonomy v2 (arc W2): concatenated exports leave the header echoed as a
    data row and mostly-empty footer junk at the end. Both are row-granular
    structure damage (GATE — row deletion stays a judgment call); a frame
    with clean structure must stay silent."""
    frame = pd.DataFrame(
        {
            "txn_id": [f"T{i}" for i in range(30)] + ["txn_id", ""],
            "amount": [str(float(i + 1)) for i in range(30)] + ["amount", ""],
            "note": ["ok"] * 30 + ["note", "generated by export tool v2"],
        }
    )
    (f,) = _of(detect_all(frame), 24)
    assert f["grade"] == "GATE"
    assert f["stats"]["header_rows"] == 1
    assert f["stats"]["footer_rows"] == 1

    clean = pd.DataFrame(
        {
            "txn_id": [f"T{i}" for i in range(30)],
            "amount": [str(float(i + 1)) for i in range(30)],
            "note": ["ok"] * 30,
        }
    )
    assert 24 not in _diseases(detect_all(clean))


def test_d25_duplicated_columns() -> None:
    """Taxonomy v2 (arc W2): identical content under two names — post-join
    _x/_y debris. GATE (dropping a column is a judgment call). Columns that
    merely correlate, or share dtype but not values, stay silent."""
    base = [float(i) for i in range(40)]
    frame = pd.DataFrame({"amount": base, "amount_x": base, "note": ["ok"] * 40})
    (f,) = _of(detect_all(frame), 25)
    assert f["grade"] == "GATE"
    assert set(f["columns"]) == {"amount", "amount_x"}

    distinct = pd.DataFrame(
        {"amount": base, "fee": [v / 10 for v in base], "note": ["ok"] * 40}
    )
    assert 25 not in _diseases(detect_all(distinct))


def test_d26_truncation_artifacts() -> None:
    """Taxonomy v2 (arc W2): a varchar ceiling shows as MANY DISTINCT values
    piled at exactly one length in an otherwise varied column (or a literal
    trailing ellipsis). Fixed-width code columns are all one length by design
    and must stay silent — variety plus a shared ceiling is the signature."""
    short = [("word " * (1 + i % 4))[: 5 + i % 7] for i in range(25)]
    # space-free at the cut so .strip() can't eat the wall
    cut = [(f"note{i:02d}" + "abcdefghijklmno")[:14] for i in range(10)]
    cut[0] = cut[0][:13] + "…"
    (f,) = _of(detect_all(pd.DataFrame({"bio": cut + short})), 26)
    assert f["grade"] == "HUMAN"
    assert f["stats"]["at_ceiling"] >= 4
    assert f["stats"]["ellipsis"] == 1

    codes = pd.DataFrame({"code": [f"C{i:04d}" for i in range(30)]})
    assert 26 not in _diseases(detect_all(codes))


def test_d26_comb_shaped_template_lengths_stay_silent() -> None:
    """FP discipline: templated text ("bio N " + "detail " * k) produces a
    comb-shaped length distribution whose gaps mimic interior ceilings — the
    exact shape this detector's first draft fired on. Pinned so no future
    'improvement' reintroduces the interior heuristic without answering it."""
    comb = pd.DataFrame(
        {"bio": [f"bio {i} " + "detail " * (2 + i % 5) for i in range(24)]}
    )
    assert 26 not in _diseases(detect_all(comb))


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
    # clear + found must partition the whole single-frame taxonomy, whatever
    # its current size — growth must never silently shrink the checked claim
    assert set(res["clear"]) | ds == set(detect.SINGLE_FRAME)


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


# --- integration pass: the gaps the attackers found still open ---------------


def test_d01_mixed_currency_symbols_never_grade_auto() -> None:
    """The unit check read alphabetic suffixes only, so the currency half of
    the same hole stayed open: '$100', '€50' and '£20' are one shape to every
    family in NUMBER_FAMILIES, the column graded AUTO, and the fixer returned
    one unlabelled numeric column with three currencies added together. A
    symbol is a unit worn in front."""
    values = ["$100", "€50", "£20", "$300", "€75"] * 6
    found = detect_one(pd.DataFrame({"amt": values}), 1, ["amt"])

    assert found is not None
    assert found["grade"] == "HUMAN", found["grade"]
    assert found["stats"]["units"] == ["$", "£", "€"], found["stats"]


def test_d01_one_currency_beside_bare_numbers_is_still_an_auto_fix() -> None:
    """A bare number wears no unit, so it says nothing about the column's.
    Refusing '$100' beside '980' would refuse most money columns there are."""
    values = ["$1,200", "980", "$3,400.50", "742", "$15"] * 6
    found = detect_one(pd.DataFrame({"amt": values}), 1, ["amt"])

    assert found is not None
    assert found["grade"] == "AUTO", found["grade"]
    assert found["stats"]["units"] == ["$"], found["stats"]


def test_d01_one_unit_written_singular_and_plural_is_still_an_auto_fix() -> None:
    """UNIT_SPELLINGS listed only the ounces group, so 'lb' beside 'lbs' read
    as two units and a mechanically fixable column went to a human for
    nothing. Two characters is the floor for dropping the plural 's', because
    below it milliseconds would merge into metres."""
    values = ["5 lb", "3 lbs", "9 lb", "12 lbs"] * 6
    found = detect_one(pd.DataFrame({"w": values}), 1, ["w"])

    assert found is not None
    assert found["grade"] == "AUTO", found["grade"]
    assert found["stats"]["units"] == ["lb"], found["stats"]


def test_detect_one_verifies_a_repair_a_sibling_finding_merely_overlaps() -> None:
    """Overlap refused a complete repair whenever another finding of the same
    disease happened to share a column, which d11 findings do by
    construction: each names its own key plus the attributes it contradicts,
    and two damaged keys contradict the same attributes. Containment asks the
    right question: is this residual finding ABOUT the target?"""
    # two independently damaged keys: order_id repeats rows 0-9 at 90-99,
    # invoice_id repeats rows 20-29 at 80-89. Each one's duplicates disagree
    # about city and note AND about the other key, so both findings name all
    # four columns and differ only in which they lead with.
    order = [f"O{k:03d}" for k in range(90)] + [f"O{k:03d}" for k in range(10)]
    invoice = [f"I{i:03d}" for i in range(100)]
    for i in range(80, 90):
        invoice[i] = f"I{i - 60:03d}"
    df = pd.DataFrame(
        {
            "order_id": order,
            "invoice_id": invoice,
            "city": ["east"] * 80 + ["west"] * 20,
            "note": ["a"] * 80 + ["b"] * 20,
        }
    )
    both = ["order_id", "invoice_id", "city", "note"]
    leads = [f["columns"][0] for f in detect_all(df)["findings"] if f["disease"] == 11]
    assert sorted(leads) == ["invoice_id", "order_id"], leads

    repaired = df.assign(order_id=[f"O{i:03d}" for i in range(len(df))])

    assert detect_one(repaired, 11, both) is None
    assert detect_one(repaired, 11, ["invoice_id", *both[2:]]) is not None


def test_detect_one_still_verifies_a_duplicate_column_drop() -> None:
    """Dropping one of two identical columns is what approving a d25 GATE
    means, and it removes the target name by construction. COLUMN_CHANGING
    was sized against FIXERS, the clean() surface, while detect_one also
    verifies the GATE and HUMAN repairs a person makes."""
    df = _duplicated_columns()

    assert detect_one(df.drop(columns=["a"]), 25, ["a", "b", "c"]) is not None
    assert detect_one(df.drop(columns=["a", "b"]), 25, ["a", "b", "c"]) is None


def test_detect_one_refuses_a_header_repair_that_leaves_the_echo_row() -> None:
    """d18's evidence covers damaged NAMES and data ROWS that repeat them.
    The rows name no column, so the residual finding came back with an empty
    column list, which matched no target under either equality or overlap:
    the rename cleared verification while the junk row was still in the
    table. An empty column list is contained in every target set."""
    frame = pd.DataFrame(
        {
            "  Region ": ["Region"] + ["e", "w"] * 8,
            "amount": ["amount"] + ["1", "2"] * 8,
        }
    )
    ((cols),) = [
        f["columns"] for f in detect_all(frame)["findings"] if f["disease"] == 18
    ]
    assert cols == ["  Region "]  # the padding is all the detector saw

    renamed = frame.copy()
    renamed.columns = ["Region", "amount"]  # padding stripped, junk row left

    residual = detect_one(renamed, 18, cols)
    assert residual is not None, "the header-repeat row is still there"
    assert residual["columns"] == []
    assert "repeat the header" in residual["evidence"]


# --- wave 1.5: what the verification packet's unit and re-check rules broke ---


def test_d01_a_symbol_and_its_own_iso_code_are_one_unit() -> None:
    """Triage 4. `_units_worn` unioned the leading symbol with the ISO code
    drawn from the SAME value, so every '$1,200.00 USD' wore two units, a
    uniform single-currency column regressed AUTO to HUMAN, and the evidence
    told the reader the column mixed currencies when it did not. One value
    naming its currency twice names one currency."""
    values = ["$1,200.00 USD", "$980.00 USD", "$15.50 USD", "$3,400.00 USD"] * 6
    found = detect_one(pd.DataFrame({"amt": values}), 1, ["amt"])

    assert found is not None
    assert found["grade"] == "AUTO", found["grade"]
    assert found["stats"]["units"] == ["$"], found["stats"]
    assert "more than one unit" not in found["evidence"], found["evidence"]


def test_d01_a_space_before_the_degree_letter_is_the_same_unit() -> None:
    """Triage 4. '20°C' read as '°c' and '21° C' as '°', because the letter
    run stops at the space, so one thermometer's column went to a human."""
    values = ["20°C", "21° C", "19°C", "22° C"] * 6
    found = detect_one(pd.DataFrame({"temp": values}), 1, ["temp"])

    assert found is not None
    assert found["grade"] == "AUTO", found["grade"]
    assert found["stats"]["units"] == ["°c"], found["stats"]


def test_d01_fluid_ounces_beside_ounces_is_one_unit() -> None:
    """Triage 4. Raha's beers column writes the same volume as '12 oz' and
    '16 fl oz'; the first letter run read the second as 'fl', so the column
    that UNIT_SPELLINGS exists for was refused anyway."""
    values = ["12 oz", "16 fl oz", "12 oz", "16 fl oz", "24 fl oz"] * 6
    found = detect_one(pd.DataFrame({"volume": values}), 1, ["volume"])

    assert found is not None
    assert found["grade"] == "AUTO", found["grade"]
    assert found["stats"]["units"] == ["ounce"], found["stats"]


def test_d01_two_different_currencies_in_one_value_still_go_to_a_human() -> None:
    """The line the fold must not cross: folding a symbol into its OWN ISO
    code is not folding every symbol into every code. A euro sign wearing a
    dollar code is two currencies, whichever one the value meant."""
    values = ["€1,200.00 USD", "€980.00 USD", "€15.50 USD"] * 8
    found = detect_one(pd.DataFrame({"amt": values}), 1, ["amt"])

    assert found is not None
    assert found["grade"] == "HUMAN", found["grade"]
    assert len(found["stats"]["units"]) == 2, found["stats"]


def test_d01_a_dollar_sign_beside_an_unfolded_code_still_goes_to_a_human() -> None:
    """'$' is read as the ISO code it shares a group with, and that group
    holds USD alone. A column of '$100' beside '50 CAD' is two currencies
    under one symbol the data cannot disambiguate."""
    values = ["$100.00", "50.00 CAD", "$275.00", "80.00 CAD"] * 6
    found = detect_one(pd.DataFrame({"amt": values}), 1, ["amt"])

    assert found is not None
    assert found["grade"] == "HUMAN", found["grade"]
    assert found["stats"]["units"] == ["$", "cad"], found["stats"]


def test_d01_a_bare_degree_beside_celsius_still_goes_to_a_human() -> None:
    """The ambiguity the degree fold deliberately keeps: '20°' names no scale,
    so beside '21°C' it is either the same unit written short or Fahrenheit,
    or an angle. Two readings, one of them a silent error, so a person looks.
    """
    values = ["20°", "21°C", "19°", "22°C"] * 6
    found = detect_one(pd.DataFrame({"temp": values}), 1, ["temp"])

    assert found is not None
    assert found["grade"] == "HUMAN", found["grade"]
    assert found["stats"]["units"] == ["°", "°c"], found["stats"]


def test_d01_a_rate_is_not_the_same_unit_as_its_numerator() -> None:
    """Triage 5, the mirror of 4: the first letter run read '80 km/h' as 'km',
    so a column mixing a distance with a speed looked uniform and graded AUTO.
    Stripping both suffixes puts kilometres and kilometres per hour in one
    float column."""
    values = ["60 km", "80 km/h", "45 km", "110 km/h"] * 6
    found = detect_one(pd.DataFrame({"trip": values}), 1, ["trip"])

    assert found is not None
    assert found["grade"] == "HUMAN", found["grade"]
    assert found["stats"]["units"] == ["km", "km/h"], found["stats"]

    lab = ["5 mg", "8 mg/dL", "12 mg", "3 mg/dL"] * 6
    blood = detect_one(pd.DataFrame({"dose": lab}), 1, ["dose"])
    assert blood is not None
    assert blood["grade"] == "HUMAN", blood["grade"]
    assert blood["stats"]["units"] == ["mg", "mg/dl"], blood["stats"]


def test_d01_a_uniform_rate_column_is_still_an_auto_fix() -> None:
    """And the over-refusal the same change must not buy: every value wearing
    the same rate is one unit, whatever the slash."""
    values = ["80 km/h", "110 km/h", "45 km/h", "95 km/h"] * 6
    found = detect_one(pd.DataFrame({"speed": values}), 1, ["speed"])

    assert found is not None
    assert found["grade"] == "AUTO", found["grade"]
    assert found["stats"]["units"] == ["km/h"], found["stats"]


def test_detect_one_verifies_a_target_a_header_repair_already_renamed() -> None:
    """Triage 16, cross-packet. A finding freezes its target's name when it is
    raised. An already-verified d18 header repair renames that column, and
    every finding still queued against the old name then read as a target that
    left the frame: d22, d23 and d26 on that column became unverifiable for
    the rest of the run, whatever the fixer did."""
    from crivo.autoclean import _fix_booleans, _fix_headers

    frame = pd.DataFrame(
        {
            "  active ": ["Y", "N", "yes", "no", "TRUE", "FALSE", "1", "0"] * 5,
            "n": range(40),
        }
    )
    ((bools),) = [
        f["columns"] for f in detect_all(frame)["findings"] if f["disease"] == 23
    ]
    ((header),) = [
        f["columns"] for f in detect_all(frame)["findings"] if f["disease"] == 18
    ]
    assert bools == ["  active "]

    repaired = _fix_headers(frame, header)
    assert list(repaired.columns) == ["active", "n"]

    residual = detect_one(repaired, 23, bools)
    assert residual is not None, "the boolean chaos is still there, under the new name"
    assert "no longer in the frame" not in residual["evidence"], residual["evidence"]
    assert residual["columns"] == ["active"]

    fixed = _fix_booleans(repaired, ["active"])
    assert detect_one(fixed, 23, bools) is None, "the repair under the new name counts"


def test_detect_one_still_refuses_a_damaged_target_that_was_dropped() -> None:
    """The line triage 16's resolution must not cross: a damaged name resolves
    onto the column a repair renamed it to, and onto nothing else. With no
    such column in the frame the target is a lost column, and lost is not
    verified."""
    frame = pd.DataFrame(
        {
            "  city ": ["  Vancouver", "Burnaby ", "Victoria", "Surrey "] * 5,
            "n": range(20),
        }
    )
    residual = detect_one(frame.drop(columns=["  city "]), 6, ["  city "])

    assert residual is not None
    assert residual["grade"] == "HUMAN"
    assert "no longer in the frame" in residual["evidence"], residual["evidence"]


def test_detect_one_refuses_a_header_repair_that_leaves_a_new_damaged_name() -> None:
    """Triage 17. Containment asked whether the residual finding's columns are
    among the targets, and a d18 repair that renames one damaged header while
    coining another names a column no target ever held: the residual matched
    nothing, and half a header repair was recorded verified."""
    frame = pd.DataFrame(
        {
            "  Region ": ["east", "west"] * 8,
            "Unnamed: 1": ["1", "2"] * 8,
            "ok": list("abcdefghijklmnop"),
        }
    )
    ((cols),) = [
        f["columns"] for f in detect_all(frame)["findings"] if f["disease"] == 18
    ]
    assert cols == ["  Region ", "Unnamed: 1"]

    half = frame.copy()
    half.columns = ["Region", " Total ", "ok"]  # one repaired, one freshly damaged

    residual = detect_one(half, 18, cols)
    assert residual is not None, "the frame still carries a damaged header name"
    assert residual["columns"] == [" Total "], residual["columns"]


def test_detect_one_refuses_a_target_that_resolves_onto_a_dedupe_twin() -> None:
    """The item 16 resolution reopened silent false verification on the most
    ordinary CSV artifact there is: a frame carrying both 'active' and
    '  active '. _fix_headers renames the padded twin to 'active_2' because
    'active' is taken, and the frozen target then resolved onto 'active' - a
    healthy, unrelated column - so detect_one returned None while the d23
    boolean chaos sat untouched under 'active_2'. Before the packet this
    refused loudly with "no longer in the frame"."""
    from crivo.autoclean import _fix_headers

    frame = pd.DataFrame(
        {
            "active": ["yes", "no"] * 20,
            "  active ": ["Y", "N", "yes", "no", "TRUE", "FALSE", "1", "0"] * 5,
        }
    )
    repaired = _fix_headers(frame, ["  active "])
    assert list(repaired.columns) == ["active", "active_2"], list(repaired.columns)

    residual = detect_one(repaired, 23, ["  active "])

    assert residual is not None, "the boolean chaos is still in the frame"


def test_detect_one_resolves_an_unnamed_target_only_onto_its_own_number() -> None:
    """The positional branch compared the GENERATED name's number to the frame
    position instead of to the N in 'Unnamed: N' that _fix_headers built it
    from, so on columns ['x', 'column_1', 'y'] the frozen targets 'Unnamed: 0',
    'Unnamed: 2' and 'Unnamed: 99' all resolved onto 'column_1'. A target that
    was DROPPED was therefore re-checked on a surviving column and verified.
    'column_1' is the name _fix_headers writes for 'Unnamed: 1' and for no
    other."""
    from crivo.detect import _resolve_renames

    frame = pd.DataFrame({"x": [1], "column_1": [2], "y": [3]})

    assert _resolve_renames(frame, ["Unnamed: 1"]) == {"Unnamed: 1": "column_1"}
    for wrong in ("Unnamed: 0", "Unnamed: 2", "Unnamed: 99"):
        assert _resolve_renames(frame, [wrong]) == {}, wrong


def test_detect_one_never_resolves_a_blank_frozen_target() -> None:
    """A blank name carries no identity at all - no spelling to repair and no
    position to state - and it went through the positional branch anyway,
    landing on whatever 'column_<i>' the frame happened to own."""
    from crivo.detect import _resolve_renames

    frame = pd.DataFrame({"x": [1], "column_1": [2], "y": [3]})

    assert _resolve_renames(frame, ["   "]) == {}
    assert _resolve_renames(frame, [""]) == {}


def test_detect_one_resolves_the_ordinary_multi_unnamed_export() -> None:
    """The headline case of triage 16, which the packet left refused: an
    export with two 'Unnamed: N' columns produced two 'column_<i>' candidates
    at matching positions, so the single-candidate rule refused and the
    resolution recovered only the padded-name shape - the one shape whose
    resolution was unsafe. Matching each target to its own number resolves
    both."""
    from crivo.detect import _resolve_renames

    frame = pd.DataFrame({"a": [1], "column_1": [2], "column_2": [3]})

    assert _resolve_renames(frame, ["Unnamed: 1", "Unnamed: 2"]) == {
        "Unnamed: 1": "column_1",
        "Unnamed: 2": "column_2",
    }


def test_d01_a_rate_column_written_two_ways_is_one_unit() -> None:
    """The over-refusal the item 5 repair created on the axis it opened.
    UNIT_TOKEN added the slash denominator to the unit token but no
    denominator spelling was ever folded, so a uniform speed column written
    '80 km/h' beside '75 km/hr' graded HUMAN and the evidence asserted the
    column mixes units - which is triage 4's exact defect, reproduced one
    packet later."""
    values = ["80 km/h", "75 km/hr", "90 km/h", "65 km / h"] * 5

    ((found),) = [
        f
        for f in detect_all(pd.DataFrame({"speed": values}))["findings"]
        if f["disease"] == 1
    ]

    assert found["stats"]["units"] == ["km/h"], found["stats"]
    assert found["grade"] == "AUTO", found["evidence"]
