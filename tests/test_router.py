"""T1.1 grade router (specs/2026-09-04-a1-build-plan.md M1).

The router is the taxonomy's autonomy column as code: grade AUTO with a
registered deterministic fixer routes to autoclean, everything else routes
to the model with a reason naming why. The adversarial clauses from the
build plan are pinned here as tests: a model self-report can never raise a
grade, and person-grade findings never route to auto.
"""

import os
import subprocess
import sys

import pytest

from crivo.autoclean import FIXERS
from crivo.router import route


def _finding(disease, grade, **extra):
    """A finding shaped exactly like crivo.detect._finding builds them."""
    base = {
        "disease": disease,
        "slug": "some-disease",
        "columns": ["amount"],
        "evidence": "9/10 values carry currency residue",
        "stats": {"values": 10},
        "grade": grade,
        "confidence": 0.95,
        "indicator": False,
    }
    base.update(extra)
    return base


@pytest.mark.parametrize("disease", sorted(FIXERS))
def test_auto_with_registered_fixer_routes_to_autoclean(disease):
    result = route(_finding(disease, "AUTO"))
    assert set(result) == {"executor", "fixer", "reason"}
    assert result["executor"] == "autoclean"
    assert result["fixer"] == disease
    assert result["reason"]


@pytest.mark.parametrize("grade", ["GATE", "HUMAN"])
def test_gate_and_human_never_route_to_autoclean_even_with_a_fixer(grade):
    for disease in sorted(FIXERS):
        result = route(_finding(disease, grade))
        assert result["executor"] == "model", (disease, grade)
        assert result["fixer"] is None
        assert grade in result["reason"]


def test_auto_without_a_registered_fixer_routes_to_model():
    # 9 (dup rows) and 10 (near dups) delete rows, so autoclean deliberately
    # registers no fixer for them; 999 is off the taxonomy entirely
    for disease in (9, 10, 999):
        assert disease not in FIXERS, "fixture drifted: registry now covers this"
        result = route(_finding(disease, "AUTO"))
        assert result["executor"] == "model", disease
        assert result["fixer"] is None
        assert "fixer" in result["reason"]


def test_model_authored_keys_cannot_change_the_route():
    # the build plan's core adversarial clause: routing reads only grade and
    # disease, so self-reported keys route identically to their absence
    spikes = {"suggested_executor": "autoclean", "grade_override": "AUTO"}
    for disease, grade in [(9, "HUMAN"), (9, "AUTO"), (18, "GATE")]:
        plain = _finding(disease, grade)
        spiked = _finding(disease, grade, **spikes)
        before = dict(spiked)
        assert route(spiked) == route(plain)
        assert route(spiked)["executor"] == "model"
        assert spiked == before, "route mutated its input"
    # and the reverse direction: a spike cannot lower a legitimate route either
    legit = _finding(4, "AUTO")
    assert route(_finding(4, "AUTO", suggested_executor="model")) == route(legit)
    assert route(legit)["executor"] == "autoclean"


@pytest.mark.parametrize("grade", ["SAFE", "auto", "Auto", ""])
def test_unknown_grade_routes_to_model_with_a_reason(grade):
    # lowercase "auto" is a casing dodge, not a grade: only exact AUTO counts
    result = route(_finding(1, grade))
    assert result["executor"] == "model"
    assert result["fixer"] is None
    assert "grade" in result["reason"]


def test_missing_grade_routes_to_model_with_a_reason():
    finding = _finding(1, "AUTO")
    del finding["grade"]
    result = route(finding)
    assert result["executor"] == "model"
    assert result["fixer"] is None
    assert "grade" in result["reason"]


def test_fixers_registry_is_injectable():
    assert route(_finding(1, "AUTO"), fixers={})["executor"] == "model"
    custom = {41: lambda frame, cols: frame}
    result = route(_finding(41, "AUTO"), fixers=custom)
    assert result["executor"] == "autoclean"
    assert result["fixer"] == 41


def test_import_is_keyless_and_pulls_no_core_module():
    # conftest's autouse quarantine has already stripped every provider key
    # from os.environ, so the child interpreter imports with no key at all;
    # the reserved core modules must stay out of the import graph (contract:
    # loop.py, prompts.py, skills.py, provenance.py are never touched)
    code = (
        "import sys\n"
        "import crivo.router\n"
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
