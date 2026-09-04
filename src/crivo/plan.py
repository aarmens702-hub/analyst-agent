"""T2.1 plan object (specs/2026-09-04-a1-build-plan.md M2): the plan-first
artifact.

A plan is an ordered, versioned, diffable list of steps over the findings
crivo.detect already produced, never prose. build_plan routes each finding
through crivo.router.route and orders the cheap deterministic steps (grade
AUTO handed to autoclean) first, then the rest in findings order. diff_plan is
the replan-by-diff boundary (research gap 7): it flips step statuses and
nothing else, so an approved plan doubles as a control-flow-integrity boundary
that scope can never be silently rewritten past.

Import-safe and keyless: the only crivo import is crivo.router (itself pure),
and none of the reserved core modules (loop, prompts, skills, provenance) are
touched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from crivo.router import route

STATUSES = ("pending", "fixed", "skipped", "failed", "obsolete")


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass(frozen=True)
class PlanStep:
    """One ordered fix step (T2.1): the finding it addresses, its disease and
    grade, the executor that runs it ("autoclean" or "model"), the check whose
    signal must go quiet (expected_check), and its status. Frozen so a step in
    an approved plan cannot be edited in place. status defaults to "pending"
    and must be one of STATUSES.
    """

    finding_id: str
    disease: int
    grade: str
    executor: str
    expected_check: str
    status: str = "pending"

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status {self.status!r} is not one of {STATUSES}")

    def to_dict(self) -> dict:
        """JSON-ready dict; from_dict inverts it."""
        return {
            "finding_id": self.finding_id,
            "disease": self.disease,
            "grade": self.grade,
            "executor": self.executor,
            "expected_check": self.expected_check,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlanStep:
        """Rebuild a step from to_dict output."""
        return cls(
            finding_id=d["finding_id"],
            disease=d["disease"],
            grade=d["grade"],
            executor=d["executor"],
            expected_check=d["expected_check"],
            status=d["status"],
        )


@dataclass(frozen=True)
class Plan:
    """A versioned, ordered set of steps (T2.1). version starts at 1 and every
    diff bumps it; steps is an immutable tuple; created is the ISO timestamp of
    this version. summary() renders one human line, never prose.
    """

    version: int
    steps: tuple[PlanStep, ...]
    created: str

    def summary(self) -> str:
        """One line like "plan v1: 3 steps (2 autoclean, 1 model)"."""
        autoclean = sum(1 for s in self.steps if s.executor == "autoclean")
        model = len(self.steps) - autoclean
        return (
            f"plan v{self.version}: {len(self.steps)} steps "
            f"({autoclean} autoclean, {model} model)"
        )

    def to_dict(self) -> dict:
        """JSON-ready dict; from_dict inverts it."""
        return {
            "version": self.version,
            "steps": [s.to_dict() for s in self.steps],
            "created": self.created,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Plan:
        """Rebuild a plan from to_dict output."""
        return cls(
            version=d["version"],
            steps=tuple(PlanStep.from_dict(s) for s in d["steps"]),
            created=d["created"],
        )


def step_id(finding: dict, index: int) -> str:
    """The finding's own "id" when it carries one, else "f{index}" by input
    position. One function so build_plan and any later status-recording match
    on the same id (avoids the two-places-derive-the-same-key drift, T2.1)."""
    return finding.get("id", f"f{index}")


def build_plan(
    findings: list[dict], router_fn: Callable[[dict], dict] | None = None
) -> Plan:
    """Turn a findings list into an ordered version-1 plan (T2.1).

    Each finding is routed through router_fn (default crivo.router.route) to
    pick its executor. A step's finding_id is the finding's own "id" when it
    carries one, else "f{index}" by position; its expected_check is the
    finding's "slug". Ordering: grade-AUTO steps routed to autoclean come first
    (cheap and deterministic), then every other step. The sort is stable, so
    order within each group stays the findings' order.
    """
    if router_fn is None:
        router_fn = route
    steps = []
    for index, finding in enumerate(findings):
        routed = router_fn(finding)
        steps.append(
            PlanStep(
                finding_id=step_id(finding, index),
                disease=finding["disease"],
                grade=finding["grade"],
                executor=routed["executor"],
                expected_check=finding["slug"],
            )
        )
    steps.sort(
        key=lambda s: 0 if s.grade == "AUTO" and s.executor == "autoclean" else 1
    )
    return Plan(version=1, steps=tuple(steps), created=_now_iso())


def diff_plan(old: Plan, new_steps_status: dict) -> Plan:
    """Apply a status-only change map to `old`, returning a new plan (T2.1).

    CRITICAL, research gap 7 (replan by diff, never by rewrite): scope is
    immutable here. The map is finding_id -> new status, and that is the only
    lever; the new plan carries version + 1 and the identical step set, order,
    and every other field, with only the named statuses flipped. A finding_id
    the plan does not contain raises ValueError (nothing is ever added), and a
    status outside STATUSES is rejected by PlanStep, so a diff can never rewrite
    a step's grade, executor, disease, or the step set.
    """
    known = {s.finding_id for s in old.steps}
    unknown = sorted(set(new_steps_status) - known)
    if unknown:
        raise ValueError(f"unknown finding ids {unknown}: a diff cannot add steps")
    steps = tuple(
        replace(s, status=new_steps_status[s.finding_id])
        if s.finding_id in new_steps_status
        else s
        for s in old.steps
    )
    return Plan(version=old.version + 1, steps=steps, created=_now_iso())
