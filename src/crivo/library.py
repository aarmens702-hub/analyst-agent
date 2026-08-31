"""The skill library and its governance (P2 R10-R14).

Two Ratchet mechanisms, no more: outcome-driven retirement and a bounded active
set. A skill earns the right to run unattended by working on data it wasn't born
from, and loses it by failing verification twice in a row.

The ledger lives beside the skills as JSON rather than inside SKILL.md, because
`metadata` is string->string by spec and a score that moves on every use would
rewrite the skill file constantly. It doubles as the governance audit trail.
"""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LEDGER_NAME = "ledger.json"
RETIRED_DIR = "retired"

# Governance thresholds. Placeholders until real usage exists — kept together so
# revising them is one edit, not a search.
PROMOTE_SUCCESSES = 3
PROMOTE_DATASETS = 2  # successes must span this many distinct sources
PROMOTE_CLEAN_WINDOW = 5  # ... with no failure in the last this-many uses
RETIRE_CONSECUTIVE_FAILURES = 2
ACTIVE_CAP = 50


def _score(entry: dict) -> int:
    """Ratchet's contribution score, kept deliberately legible."""
    return entry["successes"] - entry["failures"]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def unattended(entry: dict, grade: str) -> bool:
    """Whether this skill may fix this finding without asking (R12).

    Both halves are load-bearing. The grade says the disease is safe to fix
    without judgement; the state says this skill has earned the benefit of the
    doubt. Verification runs either way — this decides who watches, not whether
    the fix is checked.
    """
    return entry.get("state") == "proven" and grade == "AUTO"


@dataclass
class Library:
    """The skills directory and its ledger, which move together."""

    root: Path
    entries: dict = field(default_factory=dict)

    @classmethod
    def load(cls, root) -> "Library":
        """Open the library at `root`. A missing ledger is an empty library, not
        an error — that is simply the state before the first skill exists."""
        root = Path(root)
        path = root / LEDGER_NAME
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(root=root, entries=entries)

    def save(self) -> Path:
        path = Path(self.root) / LEDGER_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.entries, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def register(self, name: str, disease: int) -> dict:
        """Enter a newly admitted skill on probation: retrievable, but always
        gated until it has a track record (R14)."""
        entry = {
            "name": name,
            "disease": int(disease),
            "state": "probation",
            "uses": 0,
            "successes": 0,
            "failures": 0,
            "datasets": [],
            "recent": [],
            "created": _now(),
            "last_used": "",
            "events": [],
        }
        self.entries[name] = entry
        self._enforce_cap()
        return entry

    def _enforce_cap(self) -> None:
        """Keep the active set bounded (R14). A library that only ever grows
        stops being a library — the cap is what forces the worst skill out."""
        active = [e for e in self.entries.values() if e["state"] != "retired"]
        while len(active) > ACTIVE_CAP:
            worst = min(active, key=lambda e: (_score(e), e["last_used"], e["created"]))
            self.retire(
                worst["name"],
                reason=f"evicted at the active cap of {ACTIVE_CAP} as the weakest skill",
            )
            active.remove(worst)

    def record(self, name: str, *, success: bool, dataset: str, events=()) -> dict:
        """Log one application. Sources are deduplicated because promotion asks
        whether a skill has worked on *different* data, not how often it has
        worked on the same file (R14)."""
        entry = self.entries[name]
        entry["uses"] += 1
        entry["last_used"] = _now()
        if success:
            entry["successes"] += 1
            if dataset and dataset not in entry["datasets"]:
                entry["datasets"].append(dataset)
        else:
            entry["failures"] += 1
        entry["recent"] = (entry["recent"] + ["success" if success else "failure"])[
            -PROMOTE_CLEAN_WINDOW:
        ]
        entry["events"].append(
            {
                "t": entry["last_used"],
                "outcome": "success" if success else "failure",
                "dataset": dataset,
                "events": list(events),
            }
        )
        self._reassess(entry)
        return entry

    def candidates(self, disease: int) -> list:
        """Skills that could fix this finding, best first (R11).

        An exact key, not a similarity search: a finding already carries the
        disease id a skill was born from. Ranking is by score then recency, and
        a `match` block on each entry leaves room for a semantic tiebreak when
        two proven skills first collide on one disease.
        """
        live = [
            entry
            for entry in self.entries.values()
            if entry["disease"] == int(disease) and entry["state"] != "retired"
        ]
        return sorted(live, key=lambda e: (-_score(e), e["last_used"]))

    def _reassess(self, entry: dict) -> None:
        """Apply the transitions this outcome may have triggered (R14)."""
        if entry["state"] == "retired":
            return
        tail = entry["recent"][-RETIRE_CONSECUTIVE_FAILURES:]
        if len(tail) == RETIRE_CONSECUTIVE_FAILURES and set(tail) == {"failure"}:
            self.retire(entry["name"], reason="failed verification twice in a row")
            return
        earned = (
            entry["successes"] >= PROMOTE_SUCCESSES
            and len(entry["datasets"]) >= PROMOTE_DATASETS
            and "failure" not in entry["recent"]
        )
        if earned and entry["state"] == "probation":
            entry["state"] = "proven"
            entry["events"].append({"t": _now(), "outcome": "promoted", "events": []})

    def retire(self, name: str, reason: str) -> dict:
        """Retire a skill: out of retrieval, folder moved aside, ledger intact.
        Reversible on purpose — the graveyard is evidence, not garbage."""
        entry = self.entries[name]
        entry["state"] = "retired"
        entry["retired_reason"] = reason
        entry["events"].append({"t": _now(), "outcome": "retired", "reason": reason})
        live = Path(self.root) / name
        if live.is_dir():
            grave = Path(self.root) / RETIRED_DIR / name
            grave.parent.mkdir(parents=True, exist_ok=True)
            if grave.exists():
                shutil.rmtree(grave)
            shutil.move(str(live), str(grave))
        return entry
