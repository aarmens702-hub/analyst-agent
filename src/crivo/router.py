"""Grade router (specs/2026-09-04-a1-build-plan.md T1.1): which executor
handles a finding.

Pure and deterministic: the decision reads only the finding's "grade" and
"disease", so a model-authored key on a finding (a suggested executor, a
grade override) cannot change the route, and the person grades GATE and
HUMAN never route to auto. A finding routes to the autoclean executor only
when its grade is AUTO and a deterministic fixer is registered for its
disease; everything else routes to the model with a reason naming why.
"""

from crivo.autoclean import FIXERS


def _model(reason: str) -> dict:
    return {"executor": "model", "fixer": None, "reason": reason}


def route(finding: dict, fixers: dict | None = None) -> dict:
    """Decide which executor handles `finding`; the input is never mutated.

    Returns {"executor": "autoclean" or "model", "fixer": the disease id
    when the executor is autoclean else None, "reason": short string}.
    `fixers` is the deterministic-fixer registry keyed by disease id and
    defaults to crivo.autoclean.FIXERS, which deliberately omits the
    row-deleting diseases.
    """
    if fixers is None:
        fixers = FIXERS
    grade = finding.get("grade")
    disease = finding.get("disease")
    if grade == "AUTO":
        if disease in fixers:
            return {
                "executor": "autoclean",
                "fixer": disease,
                "reason": f"grade AUTO with a registered fixer for disease {disease}",
            }
        return _model(f"grade AUTO but no deterministic fixer for disease {disease}")
    if grade == "GATE":
        return _model("grade GATE: fix with a human check, never auto")
    if grade == "HUMAN":
        return _model("grade HUMAN: needs a judgement call, never auto")
    return _model(f"unknown grade {grade!r}: only AUTO can route to auto")
