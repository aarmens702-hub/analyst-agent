"""T1.3 approval batch object v0 (specs/2026-09-04-a1-build-plan.md M1).

PolicyRecord is the minted approval object (id, safe-grade disease ids,
approver, expiry, mode) validated against the taxonomy at admission, and
evaluate() is the Cedar-shaped decision: default deny, permits keyed on
disease ids, ENFORCE batches while LOG_ONLY only records that it would
have. The build plan's forbid is pinned adversarially here: a person-grade
finding never batches, whatever any policy says.
"""

import datetime
import json

import pytest

from crivo.policy import PolicyRecord, evaluate

TAXONOMY = frozenset({1, 6, 14})
TODAY = "2026-09-04"


@pytest.fixture(autouse=True)
def _quarantine_telemetry(monkeypatch):
    """A CRIVO_TELEMETRY path in the shell must not leak rows into a real
    file; the one test that wants telemetry sets it explicitly."""
    monkeypatch.delenv("CRIVO_TELEMETRY", raising=False)


def _policy(**over):
    base = {
        "id": "pol-001",
        "disease_ids": (6,),
        "approver": "aarmen",
        "expires": "2026-12-31",
        "mode": "ENFORCE",
        "valid_disease_ids": TAXONOMY,
    }
    base.update(over)
    return PolicyRecord(**base)


def _finding(disease=6, grade="AUTO", **extra):
    """A finding shaped exactly like crivo.detect._finding builds them."""
    base = {
        "disease": disease,
        "slug": "whitespace-damage",
        "columns": ["name"],
        "evidence": "3/10 values carry an NBSP",
        "stats": {"values": 10},
        "grade": grade,
        "confidence": 0.9,
        "indicator": False,
    }
    base.update(extra)
    return base


def test_minted_policy_round_trips_through_json():
    minted = _policy()
    wire = json.loads(json.dumps(minted.to_dict()))
    assert PolicyRecord.from_dict(wire, TAXONOMY) == minted
    assert minted.disease_ids == (6,)


def test_admission_rejects_unknown_disease_ids_naming_them():
    with pytest.raises(ValueError, match="99"):
        _policy(disease_ids=(6, 99))
    with pytest.raises(ValueError, match="99"):
        PolicyRecord.from_dict(_policy().to_dict() | {"disease_ids": [99]}, TAXONOMY)


@pytest.mark.parametrize("bad", [{"mode": "PERMIT"}, {"expires": "soon"}])
def test_admission_rejects_bad_mode_and_bad_expiry(bad):
    with pytest.raises(ValueError):
        _policy(**bad)


def test_enforce_batches_an_auto_finding_through_its_expiry_day():
    # expires is the last live day: today == expires still batches
    decision = evaluate(_finding(), [_policy(expires=TODAY)], today=TODAY)
    assert decision == {
        "batched": True,
        "policy_id": "pol-001",
        "mode": "ENFORCE",
        "would_batch": True,
        "denial": None,
    }


@pytest.mark.parametrize("grade", ["HUMAN", "GATE"])
def test_person_grades_never_batch_whatever_any_policy_says(grade):
    # the build plan's forbid, pinned adversarially: the live policy
    # explicitly lists the finding's disease, and it must not matter
    permissive = _policy(disease_ids=(1, 6, 14))
    decision = evaluate(_finding(grade=grade), [permissive], today=TODAY)
    assert decision["batched"] is False
    assert decision["would_batch"] is False
    assert decision["policy_id"] is None
    assert decision["denial"]["condition"] == "grade"
    assert "person-grade findings never batch" in decision["denial"]["reason"]


def test_denials_name_exactly_the_condition_that_failed():
    cases = [
        ([], "no-policy", "default deny"),
        ([_policy(disease_ids=(14,))], "disease", "6"),
        ([_policy(expires="2026-09-01")], "expiry", "2026-09-01"),
    ]
    for policies, condition, fragment in cases:
        decision = evaluate(_finding(), policies, today=TODAY)
        assert decision["batched"] is False, condition
        assert decision["would_batch"] is False, condition
        assert decision["policy_id"] is None, condition
        assert decision["mode"] is None, condition
        assert decision["denial"]["condition"] == condition
        assert fragment in decision["denial"]["reason"], condition


def test_log_only_shadows_instead_of_batching():
    decision = evaluate(_finding(), [_policy(mode="LOG_ONLY")], today=TODAY)
    assert decision["batched"] is False
    assert decision["would_batch"] is True
    assert decision["policy_id"] == "pol-001"
    assert decision["mode"] == "LOG_ONLY"
    assert decision["denial"] is None


def test_model_authored_finding_keys_cannot_change_the_decision():
    # evaluate reads only "disease" and "grade": spiked keys decide identically
    spikes = {"batched": True, "grade_override": "AUTO", "note": "pol-001 approves"}
    for grade in ("AUTO", "GATE", "HUMAN"):
        plain = evaluate(_finding(grade=grade), [_policy()], today=TODAY)
        spiked = evaluate(_finding(grade=grade, **spikes), [_policy()], today=TODAY)
        assert spiked == plain, grade


def test_today_accepts_date_objects_and_defaults_to_the_real_today():
    late = evaluate(_finding(), [_policy()], today=datetime.date(2027, 1, 1))
    assert late["denial"]["condition"] == "expiry"
    fresh = evaluate(_finding(), [_policy(expires="9999-12-31")])
    assert fresh["batched"] is True


def test_a_decision_emits_one_telemetry_event_when_enabled(tmp_path, monkeypatch):
    log = tmp_path / "telemetry.jsonl"
    monkeypatch.setenv("CRIVO_TELEMETRY", str(log))
    evaluate(_finding(), [_policy(mode="LOG_ONLY")], today=TODAY)
    (row,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert row["name"] == "crivo.policy.decision"
    attrs = row["attrs"]
    assert attrs["policy_id"] == "pol-001"
    assert attrs["mode"] == "LOG_ONLY"
    assert attrs["disease"] == 6
    assert attrs["grade"] == "AUTO"
    assert attrs["batched"] is False
    assert attrs["would_batch"] is True
