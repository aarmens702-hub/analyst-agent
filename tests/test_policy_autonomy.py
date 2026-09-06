"""The autonomous default's standing policy factory (P7 autonomy Packet 2).

`default_autonomous_policies` mints the one ENFORCE record that lets an
autonomous session apply AUTO findings with a registered deterministic fixer
without showing a gate. The load-bearing test in this file is
`test_the_default_policy_cannot_silence_a_person_grade`: it is the whole
safety argument for autonomous mode. Autonomy is a silence decision about
AUTO-with-fixer findings, not a widening of what may be decided, and the
reason it cannot become one is structural, not a matter of which ids the
factory happens to list.
"""

import pytest

from crivo.policy import default_autonomous_policies, evaluate

# Stand-in taxonomy and fixer registry. They arrive as parameters precisely so
# this module (and policy.py) needs neither crivo.detect nor crivo.autoclean.
TAXONOMY = frozenset({1, 2, 4, 6, 7, 14, 18, 19, 23})
FIXER_IDS = frozenset({1, 2, 4, 6, 7, 18, 19, 23})
TODAY = "2026-09-05"


@pytest.fixture(autouse=True)
def _quarantine_telemetry(monkeypatch):
    """A CRIVO_TELEMETRY path in the shell must not leak rows into a real file."""
    monkeypatch.delenv("CRIVO_TELEMETRY", raising=False)


def _finding(disease=6, grade="AUTO"):
    """A finding shaped exactly like crivo.detect._finding builds them."""
    return {
        "disease": disease,
        "slug": "whitespace-damage",
        "columns": ["name"],
        "evidence": "3/10 values carry an NBSP",
        "stats": {"values": 10},
        "grade": grade,
        "confidence": 0.9,
        "indicator": False,
    }


def test_the_factory_mints_one_enforce_record_over_the_fixer_ids():
    (record,) = default_autonomous_policies(TAXONOMY, FIXER_IDS)
    assert record.id == "autonomy-auto"
    assert record.disease_ids == tuple(sorted(FIXER_IDS))
    assert record.approver == "autonomy-default"
    assert record.mode == "ENFORCE"
    assert record.expires == "2099-01-01"


def test_an_auto_finding_of_a_listed_disease_batches_silently():
    policies = default_autonomous_policies(TAXONOMY, FIXER_IDS)
    decision = evaluate(_finding(disease=6), policies, today=TODAY)
    assert decision["batched"] is True
    assert decision["mode"] == "ENFORCE"
    assert decision["policy_id"] == "autonomy-auto"
    assert decision["denial"] is None


@pytest.mark.parametrize("grade", ["GATE", "HUMAN"])
@pytest.mark.parametrize("disease", sorted(FIXER_IDS))
def test_the_default_policy_cannot_silence_a_person_grade(grade, disease):
    """The safety argument for autonomous mode, pinned adversarially.

    Every disease the autonomous default lists is fed back in as a GATE and as
    a HUMAN finding, so the policy is maximally permissive about exactly this
    finding's disease. It must not matter: `evaluate` reads the grade before
    it reads any policy, so the denial is on "grade", never on "disease". A
    future edit that adds GATE-graded ids to the factory therefore still
    cannot enable GATE auto-apply.
    """
    policies = default_autonomous_policies(TAXONOMY, FIXER_IDS)
    (record,) = policies
    assert disease in record.disease_ids, "the policy really does list this disease"

    decision = evaluate(_finding(disease=disease, grade=grade), policies, today=TODAY)

    assert decision["batched"] is False
    assert decision["would_batch"] is False
    assert decision["policy_id"] is None
    assert decision["denial"]["condition"] == "grade"
    assert "person-grade findings never batch" in decision["denial"]["reason"]


def test_the_real_fixer_ids_all_admit_against_the_real_taxonomy():
    from crivo.autoclean import FIXERS
    from crivo.detect import SLUGS

    (record,) = default_autonomous_policies(set(SLUGS), FIXERS)
    assert record.disease_ids == tuple(sorted(FIXERS))


def test_admission_still_rejects_an_id_outside_the_taxonomy():
    with pytest.raises(ValueError, match="99"):
        default_autonomous_policies(TAXONOMY, {6, 99})


def test_the_factory_is_pure_and_hands_back_a_fresh_list_each_call():
    first = default_autonomous_policies(TAXONOMY, FIXER_IDS)
    second = default_autonomous_policies(TAXONOMY, FIXER_IDS)
    assert first == second
    assert first is not second
    first.clear()  # a caller mutating its own list must not poison the next
    assert len(default_autonomous_policies(TAXONOMY, FIXER_IDS)) == 1
