"""Governance ablation harness (P4) — governed vs. ungoverned skill libraries.

A simulation, not a live agent run: no model calls, no kernel, no network. It
drives `crivo.library.Library` directly — the real promotion/
retirement/active-cap rules, R10-R14 — against a small local
`UngovernedLibrary` that mimics EvoDS's admission rule (arXiv 2606.03841,
KDD 2026): a skill is admitted after `EVODS_ADMIT_AFTER` uses regardless of
outcome, and after that nothing ever retires it and nothing bounds how many
accumulate. EvoDS's paper verifiably lacks outcome-gated retirement and a
cap; this harness asks what that costs on a plausible synthetic stream of
skill applications. See CLAUDE.md and
specs/2026-08-12-p2-skill-harness-design.md (R10-R14) for the rules being
ablated.

Population model
-----------------
`--skills` skills arrive in a steady trickle across the run (new fix
proposals keep showing up, as they would after real /clean sessions), split
roughly into thirds:
  - reliable   ~0.95 hidden success rate, constant
  - mediocre   ~0.60 hidden success rate, constant
  - rotting    starts ~0.92, then steps down to ~0.20 partway through its own
               remaining lifetime — standing in for a fix whose assumption
               (a format, a unit, a sentinel convention) stops holding once
               the input data changes

Both regimes see the exact same pre-generated outcome stream (same seed,
same draws) for every skill/round pair; only which of those outcomes each
policy lets through differs. That is what makes the comparison a comparison
of *rules*, not of luck.

What this does and does not show
---------------------------------
This shows how the two RULES behave on a controlled, synthetic input shape —
evidence that retirement and the active cap behave as designed, and a
concrete number for what an EvoDS-style ungoverned rule would have cost on
that same input. It is NOT a measurement of real skills on real data: the
success rates, the rot shape (a single step down), and the arrival schedule
are all assumed, not fit to anything observed. If the governed regime is not
better on some metric below, that is reported too — the point of the harness
is an honest check, not a foregone conclusion.

Usage
-----
    uv run python scripts/ablate_governance.py [--rounds N] [--skills N]
        [--seed N] [--json]
"""

import argparse
import itertools
import json
import random
from dataclasses import dataclass, field

from crivo import library

# Hidden, assumed rates for the synthetic population (see the honesty note
# above). Kept together, like library.py's own governance constants, so
# revising the story is one edit.
RELIABLE_RATE = 0.95
MEDIOCRE_RATE = 0.60
ROTTING_GOOD_RATE = 0.92
ROTTING_BAD_RATE = 0.20
RATE_JITTER = 0.03  # each skill's rate wobbles a little so none are identical

# Small dataset pool so a skill can rack up successes on more than one source
# (library.py's PROMOTE_DATASETS), the way real fixes see more than one file.
DATASETS = ("d0", "d1", "d2", "d3")

# EvoDS (arXiv 2606.03841, KDD 2026): a skill is admitted once it has been used
# this many times, regardless of outcome — "ran once + frequency>=3".
EVODS_ADMIT_AFTER = 3

# A skill at or below this hidden rate counts as "bad" for reporting purposes
# (compute_metrics's bad_fraction/bad_failures).
BAD_RATE_THRESHOLD = 0.5

# Defaults sized to exceed library.ACTIVE_CAP (50) so a default run actually
# demonstrates cap eviction, not just retirement.
DEFAULT_ROUNDS = 200
DEFAULT_SKILLS = 80

_HONESTY_NOTE = (
    "This is a simulation with assumed, synthetic success rates on a "
    "synthetic arrival schedule -- evidence that the governed rules behave "
    "as designed on a plausible input shape, not a measurement of real "
    "skills on real data. See the module docstring for the full note."
)


def _clip(rate: float) -> float:
    return min(0.99, max(0.01, rate))


@dataclass(frozen=True)
class Skill:
    """One synthetic skill's identity and hidden behavior. `disease` is just a
    unique retrieval key here — the ablation doesn't exercise candidate
    ranking between skills that share a disease, only promotion/retirement."""

    name: str
    disease: int
    archetype: str  # "reliable" | "mediocre" | "rotting"
    rate_before: float
    rate_after: float  # equals rate_before unless archetype == "rotting"
    arrival_round: int
    rot_round: int | None  # round rate_after takes over; None if it never rots

    def rate_at(self, round_idx: int) -> float:
        if self.rot_round is not None and round_idx >= self.rot_round:
            return self.rate_after
        return self.rate_before


def build_population(n_skills: int, rounds: int, rng: random.Random) -> list[Skill]:
    """The hidden population: roughly equal thirds of reliable, mediocre, and
    rotting skills, arriving in a steady trickle across the run rather than
    all at once — new fix proposals keep showing up, the way they would after
    real /clean sessions. A rotting skill behaves well for the first quarter
    of its own remaining lifetime, then its true rate steps down for good —
    standing in for a fix whose assumption (a format, a unit, a sentinel
    convention) stops holding once the input data changes.
    """
    third = n_skills // 3
    archetypes = ["reliable"] * third + ["mediocre"] * third
    archetypes += ["rotting"] * (n_skills - len(archetypes))

    population = []
    for i, archetype in enumerate(archetypes):
        arrival = max(1, i * rounds // n_skills) if n_skills else 1
        jitter = rng.uniform(-RATE_JITTER, RATE_JITTER)
        if archetype == "reliable":
            before = after = _clip(RELIABLE_RATE + jitter)
            rot_round = None
        elif archetype == "mediocre":
            before = after = _clip(MEDIOCRE_RATE + jitter)
            rot_round = None
        else:
            before = _clip(ROTTING_GOOD_RATE + jitter)
            after = _clip(ROTTING_BAD_RATE + jitter)
            lifetime = rounds - arrival
            rot_round = arrival + max(3, lifetime // 4)
        population.append(
            Skill(
                name=f"fix-sim-{i:03d}",
                disease=i,
                archetype=archetype,
                rate_before=before,
                rate_after=after,
                arrival_round=arrival,
                rot_round=rot_round,
            )
        )
    return population


def generate_world(population: list[Skill], rounds: int, rng: random.Random) -> dict:
    """Every outcome each skill WOULD produce if invoked, for every round from
    its arrival through the end of the run — drawn once, up front, so both
    regimes face the exact same world and differ only in which of these
    outcomes their governance policy lets through.
    """
    world = {}
    for skill in population:
        per_round = {}
        for round_idx in range(skill.arrival_round, rounds + 1):
            dataset = rng.choice(DATASETS)
            success = rng.random() < skill.rate_at(round_idx)
            per_round[round_idx] = (dataset, success)
        world[skill.name] = per_round
    return world


@dataclass
class RegimeRun:
    log: list
    size_series: list
    retired_rounds: dict


def _run_with_deterministic_clock(fn):
    """Run `fn()` with `library._now` replaced by a monotonic counter.

    `_enforce_cap` breaks ties on `last_used`/`created`, both stamped by
    `library._now()`. Left alone, that ties the ablation's reproducibility to
    wall-clock timing: two runs of the same seed could evict different
    skills on an exact score tie (common right when a skill registers, since
    it starts at 0-0). Swapped out for the run and restored after, so it
    never leaks into other code sharing the process.
    """
    original = library._now
    counter = itertools.count()
    library._now = lambda: f"sim-{next(counter):012d}"
    try:
        return fn()
    finally:
        library._now = original


def run_governed(population: list[Skill], world: dict, rounds: int) -> RegimeRun:
    """Drive the real `library.Library` with the pre-generated world. Gating is
    exactly the library's own state: a retired skill — by either the two-
    consecutive-failure rule or active-cap eviction — simply stops being
    offered applications from here on.
    """

    def _play() -> RegimeRun:
        lib = library.Library(root="ablate-governance-inmemory")
        log = []
        size_series = []
        retired_rounds = {}
        for round_idx in range(1, rounds + 1):
            for skill in population:
                if skill.arrival_round > round_idx:
                    continue
                if skill.arrival_round == round_idx and skill.name not in lib.entries:
                    lib.register(skill.name, disease=skill.disease)
                entry = lib.entries.get(skill.name)
                if entry is None or entry["state"] == "retired":
                    continue
                dataset, success = world[skill.name][round_idx]
                lib.record(skill.name, success=success, dataset=dataset)
                log.append(
                    {
                        "round": round_idx,
                        "skill": skill.name,
                        "archetype": skill.archetype,
                        "true_rate": skill.rate_at(round_idx),
                        "success": success,
                        "dataset": dataset,
                    }
                )
            for name, entry in lib.entries.items():
                if entry["state"] == "retired" and name not in retired_rounds:
                    retired_rounds[name] = round_idx
            size_series.append(
                sum(1 for e in lib.entries.values() if e["state"] != "retired")
            )
        return RegimeRun(
            log=log, size_series=size_series, retired_rounds=retired_rounds
        )

    return _run_with_deterministic_clock(_play)


@dataclass
class UngovernedLibrary:
    """EvoDS-style admission (arXiv 2606.03841, KDD 2026): a skill is admitted
    once it has been used `admit_after` times, regardless of how those uses
    went, and after that it is never re-examined — no outcome-gated
    retirement, no active cap. `admitted` is a status label only; unlike
    `library.Library`'s retirement, nothing here ever blocks an application.
    Kept local to this script — library.py is not touched.
    """

    admit_after: int = EVODS_ADMIT_AFTER
    entries: dict = field(default_factory=dict)

    def register(self, name: str, disease: int) -> dict:
        entry = {
            "name": name,
            "disease": disease,
            "uses": 0,
            "successes": 0,
            "failures": 0,
            "admitted": False,
        }
        self.entries[name] = entry
        return entry

    def record(self, name: str, *, success: bool, dataset: str) -> dict:
        entry = self.entries[name]
        entry["uses"] += 1
        if success:
            entry["successes"] += 1
        else:
            entry["failures"] += 1
        if entry["uses"] >= self.admit_after:
            entry["admitted"] = True
        return entry


def run_ungoverned(population: list[Skill], world: dict, rounds: int) -> RegimeRun:
    """Nothing here ever removes a skill from service, so gating is only ever
    about whether it has arrived yet."""
    lib = UngovernedLibrary()
    log = []
    size_series = []
    for round_idx in range(1, rounds + 1):
        for skill in population:
            if skill.arrival_round > round_idx:
                continue
            if skill.name not in lib.entries:
                lib.register(skill.name, disease=skill.disease)
            dataset, success = world[skill.name][round_idx]
            lib.record(skill.name, success=success, dataset=dataset)
            log.append(
                {
                    "round": round_idx,
                    "skill": skill.name,
                    "archetype": skill.archetype,
                    "true_rate": skill.rate_at(round_idx),
                    "success": success,
                    "dataset": dataset,
                }
            )
        size_series.append(len(lib.entries))
    return RegimeRun(log=log, size_series=size_series, retired_rounds={})


def compute_metrics(
    log: list[dict],
    *,
    threshold: float,
    total_rounds: int,
    rot_rounds: dict | None = None,
    end_rounds: dict | None = None,
) -> dict:
    """Summarize one regime's accepted-application log.

    `log` entries are the applications a regime's policy actually let
    through: `{"round", "skill", "true_rate", "success", ...}`. A "bad"
    application is one whose skill's hidden true rate was already below
    `threshold` at the time it ran.

    `rot_rounds` maps a rotted skill's name to the round its hidden rate
    stepped down. `end_rounds` maps that same name to the last round it was
    still in play in this regime (its retirement round, or `total_rounds` if
    it rode out the whole run). The gap between them — clamped at 0, since a
    skill can be evicted before it ever rots — is how long this regime kept
    using a skill that had already gone bad: the harness's headline number.
    """
    applications = len(log)
    bad = [a for a in log if a["true_rate"] < threshold]
    bad_fraction = len(bad) / applications if applications else 0.0
    bad_failures = sum(1 for a in bad if not a["success"])

    rot_rounds = rot_rounds or {}
    end_rounds = end_rounds or {}
    rot_lag_by_skill = {
        name: max(0, end_rounds.get(name, total_rounds) - rot_round)
        for name, rot_round in rot_rounds.items()
    }
    lags = list(rot_lag_by_skill.values())
    rot_lag_mean = sum(lags) / len(lags) if lags else None

    return {
        "applications": applications,
        "bad_fraction": bad_fraction,
        "bad_failures": bad_failures,
        "rot_lag_by_skill": rot_lag_by_skill,
        "rot_lag_mean": rot_lag_mean,
    }


def _rot_and_end_rounds(
    population: list[Skill], retired_rounds: dict, total_rounds: int
) -> tuple[dict, dict]:
    """`compute_metrics`'s `rot_rounds`/`end_rounds` inputs for one regime's
    run: only skills that actually rotted within the simulated window count,
    since a skill whose rot point falls past `total_rounds` never showed its
    bad side here."""
    rot_rounds = {
        s.name: s.rot_round
        for s in population
        if s.rot_round is not None and s.rot_round <= total_rounds
    }
    end_rounds = {name: retired_rounds.get(name, total_rounds) for name in rot_rounds}
    return rot_rounds, end_rounds


def simulate(rounds: int, skills: int, seed: int) -> dict:
    """Run both regimes on the same seeded world and summarize each. The
    returned dict is plain JSON-safe types throughout, so the same seed
    reproduces it exactly and `--json` can dump it as-is.
    """
    rng = random.Random(seed)
    population = build_population(skills, rounds, rng)
    world = generate_world(population, rounds, rng)

    governed = run_governed(population, world, rounds)
    ungoverned = run_ungoverned(population, world, rounds)

    gov_rot, gov_end = _rot_and_end_rounds(population, governed.retired_rounds, rounds)
    ung_rot, ung_end = _rot_and_end_rounds(
        population, ungoverned.retired_rounds, rounds
    )

    gov_metrics = compute_metrics(
        governed.log,
        threshold=BAD_RATE_THRESHOLD,
        total_rounds=rounds,
        rot_rounds=gov_rot,
        end_rounds=gov_end,
    )
    ung_metrics = compute_metrics(
        ungoverned.log,
        threshold=BAD_RATE_THRESHOLD,
        total_rounds=rounds,
        rot_rounds=ung_rot,
        end_rounds=ung_end,
    )

    archetype_counts = {
        a: sum(1 for s in population if s.archetype == a)
        for a in ("reliable", "mediocre", "rotting")
    }

    return {
        "seed": seed,
        "rounds": rounds,
        "skills": skills,
        "active_cap": library.ACTIVE_CAP,
        "bad_rate_threshold": BAD_RATE_THRESHOLD,
        "archetype_counts": archetype_counts,
        "governed": {
            **gov_metrics,
            "size_series": governed.size_series,
            "final_active_size": governed.size_series[-1]
            if governed.size_series
            else 0,
            "retired_count": len(governed.retired_rounds),
        },
        "ungoverned": {
            **ung_metrics,
            "size_series": ungoverned.size_series,
            "final_active_size": ungoverned.size_series[-1]
            if ungoverned.size_series
            else 0,
            "retired_count": 0,
        },
    }


def _checkpoints(series: list[int], k: int = 10) -> list[tuple[int, int]]:
    """Up to `k` evenly spaced (round, value) samples of a size series, always
    including the last round — for a human-readable table without printing
    one row per round."""
    if not series:
        return []
    step = max(1, len(series) // k)
    idx = list(range(0, len(series), step))
    if idx[-1] != len(series) - 1:
        idx.append(len(series) - 1)
    return [(i + 1, series[i]) for i in idx]  # +1: series[0] is round 1


def print_report(results: dict) -> None:
    print(
        f"governance ablation -- seed={results['seed']} rounds={results['rounds']} "
        f"skills={results['skills']} active_cap={results['active_cap']} "
        f"bad_rate_threshold={results['bad_rate_threshold']}"
    )
    print(f"population by archetype: {results['archetype_counts']}")
    print()

    g, u = results["governed"], results["ungoverned"]
    header = f"{'metric':<38}{'governed':>12}{'ungoverned':>12}"
    print(header)
    print("-" * len(header))
    rows = [
        ("applications accepted", g["applications"], u["applications"]),
        (
            "fraction served by a bad skill",
            round(g["bad_fraction"], 3),
            round(u["bad_fraction"], 3),
        ),
        ("failures accepted from a bad skill", g["bad_failures"], u["bad_failures"]),
        ("final library size", g["final_active_size"], u["final_active_size"]),
        ("skills retired", g["retired_count"], u["retired_count"]),
        (
            "mean rounds used after rot (lag)",
            round(g["rot_lag_mean"], 2) if g["rot_lag_mean"] is not None else "n/a",
            round(u["rot_lag_mean"], 2) if u["rot_lag_mean"] is not None else "n/a",
        ),
    ]
    for label, gv, uv in rows:
        print(f"{label:<38}{gv!s:>12}{uv!s:>12}")
    print()

    print("library size over time (round: governed / ungoverned)")
    u_series = u["size_series"]
    for round_idx, gv in _checkpoints(g["size_series"]):
        print(f"  round {round_idx:4d}: {gv:4d} / {u_series[round_idx - 1]:4d}")
    print()
    print(_HONESTY_NOTE)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Simulate governed vs. ungoverned (EvoDS-style) skill "
        "libraries over a synthetic outcome stream. See module docstring."
    )
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    ap.add_argument("--skills", type=int, default=DEFAULT_SKILLS)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--json", action="store_true", help="machine-readable output for the writeup"
    )
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    results = simulate(rounds=args.rounds, skills=args.skills, seed=args.seed)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_report(results)


if __name__ == "__main__":
    main()
