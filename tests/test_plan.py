"""T2.1 plan object (specs/2026-09-04-a1-build-plan.md M2).

The plan is the plan-first artifact: an ordered, versioned, diffable list of
steps over the findings crivo.detect already produced, never prose. build_plan
routes each finding and orders the cheap deterministic steps (grade AUTO handed
to autoclean) first, then the rest in findings order. diff_plan is the
replan-by-diff boundary from research gap 7: it flips a step's status and
nothing else, so an approved plan doubles as a control-flow-integrity boundary.
The scope-immutability clause is pinned adversarially here.
"""

import json
import os
import subprocess
import sys

import pytest

from crivo.autoclean import FIXERS
from crivo.plan import Plan, PlanStep, build_plan, diff_plan
from crivo.router import route


def _finding(disease, grade, slug="whitespace-damage", **extra):
    """A finding shaped like crivo.detect._finding builds them."""
    base = {
        "disease": disease,
        "slug": slug,
        "columns": ["name"],
        "evidence": "3/10 values carry an NBSP",
        "stats": {"values": 10},
        "grade": grade,
        "confidence": 0.9,
        "indicator": False,
    }
    base.update(extra)
    return base


def _fake_router(finding):
    """Routes AUTO findings for diseases 1 and 2 to autoclean, everything else
    to the model, so the ordering test stands free of the real FIXERS set."""
    if finding["grade"] == "AUTO" and finding["disease"] in {1, 2}:
        return {"executor": "autoclean", "fixer": finding["disease"], "reason": "x"}
    return {"executor": "model", "fixer": None, "reason": "x"}


def test_planstep_round_trips_through_json():
    step = PlanStep(
        finding_id="f0",
        disease=6,
        grade="AUTO",
        executor="autoclean",
        expected_check="whitespace-damage",
    )
    assert step.status == "pending"  # the documented default
    wire = json.loads(json.dumps(step.to_dict()))
    assert PlanStep.from_dict(wire) == step


def test_plan_round_trips_through_json():
    plan = build_plan(
        [_finding(1, "AUTO"), _finding(5, "GATE")], router_fn=_fake_router
    )
    wire = json.loads(json.dumps(plan.to_dict()))
    assert Plan.from_dict(wire) == plan


def test_build_plan_orders_auto_autoclean_first_then_findings_order():
    findings = [
        _finding(1, "AUTO"),  # autoclean -> first group
        _finding(5, "GATE"),  # model -> rest
        _finding(2, "AUTO"),  # autoclean -> first group
        _finding(9, "AUTO"),  # AUTO but no fixer, so model -> rest
    ]
    plan = build_plan(findings, router_fn=_fake_router)
    assert [s.finding_id for s in plan.steps] == ["f0", "f2", "f1", "f3"]
    assert [s.executor for s in plan.steps] == [
        "autoclean",
        "autoclean",
        "model",
        "model",
    ]


def test_build_plan_reads_executor_from_router_and_expected_from_slug():
    finding = _finding(1, "AUTO", slug="currency-residue")
    (step,) = build_plan([finding], router_fn=_fake_router).steps
    assert step.executor == "autoclean"  # from the router
    assert step.expected_check == "currency-residue"  # from the finding's slug
    assert step.disease == 1
    assert step.grade == "AUTO"


def test_build_plan_finding_id_is_own_id_else_positional():
    findings = [_finding(1, "AUTO", id="dup-rows-amount"), _finding(5, "GATE")]
    plan = build_plan(findings, router_fn=_fake_router)
    # the positional fallback uses the findings index, not the post-sort index
    assert [s.finding_id for s in plan.steps] == ["dup-rows-amount", "f1"]


def test_build_plan_defaults_to_the_real_router():
    disease = min(FIXERS)
    findings = [_finding(disease, "AUTO"), _finding(disease, "GATE")]
    plan = build_plan(findings)  # no router_fn -> crivo.router.route
    got = {s.finding_id: s.executor for s in plan.steps}
    want = {f"f{i}": route(f)["executor"] for i, f in enumerate(findings)}
    assert got == want
    assert got["f0"] == "autoclean"  # AUTO with a registered fixer really routed
    assert got["f1"] == "model"


def test_summary_reports_version_and_executor_counts():
    findings = [_finding(1, "AUTO"), _finding(2, "AUTO"), _finding(5, "GATE")]
    plan = build_plan(findings, router_fn=_fake_router)
    assert plan.summary() == "plan v1: 3 steps (2 autoclean, 1 model)"


def test_plan_starts_at_version_1_with_pending_steps():
    plan = build_plan([_finding(1, "AUTO")], router_fn=_fake_router)
    assert plan.version == 1
    assert all(s.status == "pending" for s in plan.steps)


def test_build_plan_handles_no_findings():
    plan = build_plan([], router_fn=_fake_router)
    assert plan.steps == ()
    assert plan.summary() == "plan v1: 0 steps (0 autoclean, 0 model)"


def test_diff_plan_flips_status_and_bumps_version():
    plan = build_plan(
        [_finding(1, "AUTO"), _finding(5, "GATE")], router_fn=_fake_router
    )
    new = diff_plan(plan, {"f0": "fixed", "f1": "failed"})
    assert new.version == 2
    assert {s.finding_id: s.status for s in new.steps} == {
        "f0": "fixed",
        "f1": "failed",
    }
    # a partial diff leaves the untouched step pending
    partial = diff_plan(plan, {"f0": "skipped"})
    assert {s.finding_id: s.status for s in partial.steps} == {
        "f0": "skipped",
        "f1": "pending",
    }


def test_diff_plan_unknown_finding_id_raises_naming_it():
    plan = build_plan([_finding(1, "AUTO")], router_fn=_fake_router)
    with pytest.raises(ValueError, match="ghost"):
        diff_plan(plan, {"ghost": "fixed"})


def test_diff_plan_cannot_change_scope_only_status():
    """Research gap 7: a diff flips status and nothing else. It cannot add,
    remove, or reorder steps, nor change any step's grade, executor, disease,
    or expected_check, and it never mutates the plan it was handed."""
    findings = [_finding(1, "AUTO"), _finding(5, "GATE"), _finding(2, "AUTO")]
    plan = build_plan(findings, router_fn=_fake_router)
    scope = [
        (s.finding_id, s.disease, s.grade, s.executor, s.expected_check)
        for s in plan.steps
    ]

    new = diff_plan(plan, {"f0": "fixed"})

    new_scope = [
        (s.finding_id, s.disease, s.grade, s.executor, s.expected_check)
        for s in new.steps
    ]
    assert new_scope == scope  # same step set, same order, every field but status
    assert len(new.steps) == len(plan.steps)  # nothing added or removed
    assert all(s.status == "pending" for s in plan.steps)  # old plan untouched


def test_diff_plan_rejects_an_invalid_status():
    plan = build_plan([_finding(1, "AUTO")], router_fn=_fake_router)
    with pytest.raises(ValueError, match="bogus"):
        diff_plan(plan, {"f0": "bogus"})


def test_planstep_rejects_an_unknown_status():
    with pytest.raises(ValueError, match="bogus"):
        PlanStep("f0", 1, "AUTO", "model", "whitespace-damage", status="bogus")


def test_import_is_keyless_and_pulls_no_core_module():
    # conftest's autouse quarantine has already stripped every provider key, so
    # the child interpreter imports with no key; the reserved core modules must
    # stay out of the import graph (contract: loop, prompts, skills, provenance
    # are never touched)
    code = (
        "import sys\n"
        "import crivo.plan\n"
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
