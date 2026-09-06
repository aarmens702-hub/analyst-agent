"""One flat, schema-validated governance config (A1 build plan T2.5).

specs/2026-09-04-a1-build-plan.md T2.5 closes the policy layer with "one flat
schema-validated governance config"; the mailo governance-config pattern
(docs/research/2026-09-04-mining-synthesis.md, candidate 2) is the reason:
policies, the judge rubric, and routing thresholds live in one file so the
whole governance posture reviews in a single diff. load_governance reads that
file into live objects (PolicyRecords plus flat judge and routing dicts) and
validates fail-closed (mailo anti-pattern 6, no escape hatches): an unknown
top-level key, a policy naming a disease id outside the taxonomy, a judge
sample_rate outside [0, 1], or an autonomy level outside AUTONOMY_LEVELS all
raise ValueError, and the load is all-or-nothing so an error yields no partial
Governance.

The module stays import-light and keyless: only crivo.policy and the standard
library at import time. The disease taxonomy (crivo.detect.SLUGS) is imported
lazily inside load_governance, so callers and tests pass their own valid-id set
and never pay for detect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from crivo.policy import PolicyRecord

_TOP_LEVEL_KEYS = frozenset({"policies", "judge", "routing", "autonomy"})
_DEFAULT_JUDGE = {"enabled": False, "sample_rate": 0.0, "rubric": ""}
_DEFAULT_ROUTING = {"escalate_after_fail": True}

# The three named autonomy postures (specs/2026-09-05-autonomy-default-*.md):
# "autonomous" decides the AUTO findings that carry a registered deterministic
# fixer without showing a gate, "careful" gates them for a person, "report-only"
# diagnoses and stops. The list is closed: an unrecognised level raises rather
# than falling back, so a typo cannot quietly pick a posture.
AUTONOMY_LEVELS = ("autonomous", "careful", "report-only")
_DEFAULT_AUTONOMY = "autonomous"


@dataclass(frozen=True)
class Governance:
    """The whole governance posture as one reviewable object (T2.5).

    .policies are minted PolicyRecords, each already validated against the
    taxonomy at admission; .judge and .routing are the config's flat dicts.
    .autonomy is one of AUTONOMY_LEVELS and is, today, recorded intent only:
    nothing in the runtime reads it yet (see load_governance).
    """

    policies: list[PolicyRecord]
    judge: dict
    routing: dict
    autonomy: str


DEFAULT_GOVERNANCE = Governance(
    policies=[],
    judge=dict(_DEFAULT_JUDGE),
    routing=dict(_DEFAULT_ROUTING),
    autonomy=_DEFAULT_AUTONOMY,
)


def load_governance(path, valid_disease_ids=None) -> Governance:
    """Load one flat governance file into a Governance (T2.5).

    Reads the flat shape {"policies": [...], "judge": {...},
    "routing": {...}}. Policies are rebuilt via PolicyRecord.from_dict and so
    revalidated against `valid_disease_ids`, which defaults to the disease
    taxonomy set(crivo.detect.SLUGS) via a lazy import. Fail-closed: an unknown
    top-level key, a policy naming a disease id outside the taxonomy, or a judge
    sample_rate outside [0, 1], or an "autonomy" outside AUTONOMY_LEVELS raises
    ValueError with the file path for context; a missing "policies" key is an
    empty, valid governance, a missing "judge"/"routing" falls back to the safe
    defaults, and a missing "autonomy" defaults to "autonomous". The build is
    all-or-nothing: on any error no Governance is returned.

    Scope of the "autonomy" key, stated plainly: it does not take effect. No
    runtime code reads gov.autonomy, and no runtime code calls this function at
    all (load_governance has zero callers outside its own module and the tests).
    This is forward config, landed so the file schema and its round trip exist
    and are tested; the intended live control for this phase is the --autonomy
    command-line flag, which the loop-threading packet adds, not this one.
    Wiring a governance file into the runtime (including which of the two would
    win) is a separate, unscheduled task. Setting "autonomy" in a governance
    file today changes nothing about how a session behaves.
    """
    if valid_disease_ids is None:
        from crivo.detect import SLUGS

        valid_disease_ids = set(SLUGS)

    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))

    # fail closed with a message a hand-editor can act on, not a raw
    # AttributeError deep in the parse (integration tightening, T2.5 note 3)
    if not isinstance(data, dict):
        raise ValueError(  # noqa: TRY004 — the config contract is uniform ValueError
            f"{path}: governance must be a JSON object, got {type(data).__name__}"
        )

    unknown = sorted(set(data) - _TOP_LEVEL_KEYS)
    if unknown:
        raise ValueError(f"{path}: unknown governance keys {unknown}")

    try:
        policies = [
            PolicyRecord.from_dict(p, valid_disease_ids)
            for p in data.get("policies", [])
        ]
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc

    judge = {**_DEFAULT_JUDGE, **data.get("judge", {})}
    rate = judge["sample_rate"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise ValueError(  # noqa: TRY004 — the config contract is uniform ValueError
            f"{path}: judge sample_rate must be a number, got {rate!r}"
        )
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"{path}: judge sample_rate {rate} outside [0, 1]")

    routing = {**_DEFAULT_ROUTING, **data.get("routing", {})}

    autonomy = data.get("autonomy", _DEFAULT_AUTONOMY)
    if autonomy not in AUTONOMY_LEVELS:
        raise ValueError(f"{path}: autonomy {autonomy!r} not one of {AUTONOMY_LEVELS}")

    return Governance(
        policies=policies, judge=judge, routing=routing, autonomy=autonomy
    )


def save_governance(gov: Governance, path) -> None:
    """Write `gov` back to a flat governance file (T2.5).

    Inverts load_governance for the data, so load_governance(save_governance)
    round-trips a governance built by load or the DEFAULT. Policies serialize
    through PolicyRecord.to_dict.
    """
    path = Path(path)
    data = {
        "policies": [p.to_dict() for p in gov.policies],
        "judge": gov.judge,
        "routing": gov.routing,
        "autonomy": gov.autonomy,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
