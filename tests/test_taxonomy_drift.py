"""Taxonomy drift check (prime-agent steal 11, the owner's own CI pattern):
wherever two places hand-maintain the same list, derive the truth and fail on
drift. Here: every deterministic fixer and every family-only disease must name
a disease id the taxonomy (detect.SLUGS) actually defines. A fixer for a
disease id that no longer exists is dead code or a typo; this catches it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_taxonomy_drift as drift


def test_no_drift_in_the_live_taxonomy():
    assert drift.find_drift() == []


def test_a_fixer_for_an_unknown_disease_is_reported():
    problems = drift.find_drift(slugs={1: "a"}, fixer_ids={1, 999}, family_ids=set())
    assert any("999" in p for p in problems)


def test_a_family_id_outside_the_taxonomy_is_reported():
    problems = drift.find_drift(slugs={1: "a"}, fixer_ids={1}, family_ids={42})
    assert any("42" in p for p in problems)


def test_a_consistent_set_is_clean():
    assert drift.find_drift(slugs={1: "a", 2: "b"}, fixer_ids={1}, family_ids={2}) == []


def test_main_exits_zero_on_the_live_taxonomy():
    assert drift.main([]) == 0
