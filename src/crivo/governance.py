"""One flat, schema-validated governance config (A1 build plan T2.5).

specs/2026-09-04-a1-build-plan.md T2.5 closes the policy layer with "one flat
schema-validated governance config"; the mailo governance-config pattern
(docs/research/2026-09-04-mining-synthesis.md, candidate 2) is the reason:
policies, the judge rubric, and routing thresholds live in one file so the
whole governance posture reviews in a single diff. load_governance reads that
file into live objects (PolicyRecords plus flat judge and routing dicts) and
validates fail-closed (mailo anti-pattern 6, no escape hatches): an unknown
top-level key, a policy naming a disease id outside the taxonomy, or a judge
sample_rate outside [0, 1] all raise ValueError, and the load is all-or-nothing
so an error yields no partial Governance.

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

_TOP_LEVEL_KEYS = frozenset({"policies", "judge", "routing"})
_DEFAULT_JUDGE = {"enabled": False, "sample_rate": 0.0, "rubric": ""}
_DEFAULT_ROUTING = {"escalate_after_fail": True}


@dataclass(frozen=True)
class Governance:
    """The whole governance posture as one reviewable object (T2.5).

    .policies are minted PolicyRecords, each already validated against the
    taxonomy at admission; .judge and .routing are the config's flat dicts.
    """

    policies: list[PolicyRecord]
    judge: dict
    routing: dict


DEFAULT_GOVERNANCE = Governance(
    policies=[],
    judge=dict(_DEFAULT_JUDGE),
    routing=dict(_DEFAULT_ROUTING),
)


def load_governance(path, valid_disease_ids=None) -> Governance:
    """Load one flat governance file into a Governance (T2.5).

    Reads the flat shape {"policies": [...], "judge": {...},
    "routing": {...}}. Policies are rebuilt via PolicyRecord.from_dict and so
    revalidated against `valid_disease_ids`, which defaults to the disease
    taxonomy set(crivo.detect.SLUGS) via a lazy import. Fail-closed: an unknown
    top-level key, a policy naming a disease id outside the taxonomy, or a judge
    sample_rate outside [0, 1] raises ValueError with the file path for
    context; a missing "policies" key is an empty, valid governance and a
    missing "judge"/"routing" falls back to the safe defaults. The build is
    all-or-nothing: on any error no Governance is returned.
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
    return Governance(policies=policies, judge=judge, routing=routing)


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
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
