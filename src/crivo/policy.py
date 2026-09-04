"""Approval policy objects and their evaluator (A1 build plan T1.3).

Cedar's shape without the dependency: default deny, permits keyed on
disease ids that are validated against the taxonomy at admission, ENFORCE
and LOG_ONLY engine modes, and one unoverridable forbid: a finding whose
grade is not AUTO is never batched, whatever any policy says. Denials are
structured, naming exactly which condition failed (grade, disease, expiry,
no-policy). The module stays pure and dependency-light: the taxonomy
arrives as a parameter, findings are read only through "disease" and
"grade", and import-safe crivo.telemetry is the only crivo import.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from datetime import UTC, date, datetime

from crivo import telemetry

MODES = ("ENFORCE", "LOG_ONLY")


@dataclass(frozen=True)
class PolicyRecord:
    """A minted approval policy: who approved batching which safe-grade
    diseases until when (T1.3: id, disease ids, expiry, approver, mode).

    Admission is the validation gate: construction takes the taxonomy's
    valid disease ids and raises ValueError naming any unknown id, so a
    record that exists is a record that passed. Frozen so a minted record
    cannot be edited past that gate. `expires` is an ISO date, the last
    day the policy is live.
    """

    id: str
    disease_ids: tuple[int, ...]
    approver: str
    expires: str
    mode: str
    valid_disease_ids: InitVar[frozenset[int] | set[int]]

    def __post_init__(self, valid_disease_ids) -> None:
        object.__setattr__(self, "disease_ids", tuple(self.disease_ids))
        unknown = sorted(set(self.disease_ids) - set(valid_disease_ids))
        if unknown:
            raise ValueError(f"unknown disease ids {unknown}: not in the taxonomy")
        if self.mode not in MODES:
            raise ValueError(f"mode {self.mode!r} is not one of {MODES}")
        date.fromisoformat(self.expires)  # non-ISO expiry raises ValueError here

    def to_dict(self) -> dict:
        """JSON-ready dict (disease_ids as a list); from_dict inverts it."""
        return {
            "id": self.id,
            "disease_ids": list(self.disease_ids),
            "approver": self.approver,
            "expires": self.expires,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, d: dict, valid_disease_ids) -> PolicyRecord:
        """Rebuild a record from to_dict output, revalidating at admission."""
        return cls(
            id=d["id"],
            disease_ids=tuple(d["disease_ids"]),
            approver=d["approver"],
            expires=d["expires"],
            mode=d["mode"],
            valid_disease_ids=valid_disease_ids,
        )


def _deny(condition: str, reason: str) -> dict:
    return {
        "batched": False,
        "policy_id": None,
        "mode": None,
        "would_batch": False,
        "denial": {"reason": reason, "condition": condition},
    }


def _as_date(today: date | str | None) -> date:
    if today is None:
        return datetime.now(tz=UTC).date()
    if isinstance(today, str):
        return date.fromisoformat(today)
    return today


def evaluate(
    finding: dict, policies: list[PolicyRecord], today: date | str | None = None
) -> dict:
    """Decide whether `finding` may join an approval batch. Default deny.

    Reads only the finding's "disease" and "grade" so a model-authored key
    can never change the decision, and checks the forbid before any policy
    is even read, so no policy shape can override it. Returns {"batched",
    "policy_id", "mode", "would_batch", "denial"}; a denial names exactly
    which condition failed: "grade", "no-policy" (nothing admitted),
    "disease" (no policy permits it), or "expiry" (naming the date). A live
    match batches under ENFORCE; under LOG_ONLY it only sets would_batch,
    the shadow-week arming UX. Every call emits one crivo.policy.decision
    telemetry event when CRIVO_TELEMETRY is set. `today` (date or ISO
    string) exists for tests and defaults to the real today.
    """
    disease = finding["disease"]
    grade = finding["grade"]
    if grade != "AUTO":
        decision = _deny("grade", f"grade {grade}: person-grade findings never batch")
    elif not policies:
        decision = _deny("no-policy", "no approval policy is admitted: default deny")
    else:
        named = [p for p in policies if disease in p.disease_ids]
        if not named:
            decision = _deny("disease", f"no admitted policy permits disease {disease}")
        else:
            when = _as_date(today)
            live = [p for p in named if when <= date.fromisoformat(p.expires)]
            if not live:
                gone = named[0]
                decision = _deny("expiry", f"policy {gone.id} expired {gone.expires}")
            else:
                winner = live[0]
                decision = {
                    "batched": winner.mode == "ENFORCE",
                    "policy_id": winner.id,
                    "mode": winner.mode,
                    "would_batch": True,
                    "denial": None,
                }
    denial = decision["denial"]
    telemetry.emit(
        "crivo.policy.decision",
        policy_id=decision["policy_id"],
        mode=decision["mode"],
        disease=disease,
        grade=grade,
        batched=decision["batched"],
        would_batch=decision["would_batch"],
        condition=None if denial is None else denial["condition"],
    )
    return decision
