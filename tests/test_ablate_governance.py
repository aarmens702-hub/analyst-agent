"""Tests for the governance ablation harness (P4) — scripts/ablate_governance.py.

A simulation, not a live agent run: these tests hand-build outcome streams and
small populations rather than exercising real datasets or model calls. The
questions under test are the ones the harness exists to answer: does the
governed regime actually retire a rotted skill while the ungoverned one does
not, does the active cap actually bound the governed library while the
ungoverned one keeps growing, and do the summary metrics compute correctly on
a known stream.
"""

import json
import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))


from ablate_governance import (
    Skill,
    build_population,
    compute_metrics,
    generate_world,
    main,
    run_governed,
    run_ungoverned,
    simulate,
)

from crivo import library


def test_main_json_output_reports_both_regimes(capsys) -> None:
    main(["--json", "--rounds", "10", "--skills", "6", "--seed", "1"])
    payload = json.loads(capsys.readouterr().out)

    assert set(payload) >= {"governed", "ungoverned", "seed", "rounds", "skills"}
    assert payload["governed"]["applications"] > 0
    assert payload["ungoverned"]["applications"] > 0


def test_same_seed_produces_identical_results(monkeypatch) -> None:
    monkeypatch.setattr(library, "ACTIVE_CAP", 6)
    first = simulate(rounds=40, skills=12, seed=7)
    second = simulate(rounds=40, skills=12, seed=7)

    assert first == second
    assert first["governed"]["retired_count"] > 0  # cap eviction actually happened


def test_ungoverned_regime_never_retires_a_rotted_skill() -> None:
    skill = Skill(
        name="fix-x",
        disease=1,
        archetype="rotting",
        rate_before=1.0,  # always succeeds pre-rot
        rate_after=0.0,  # always fails post-rot
        arrival_round=1,
        rot_round=6,
    )
    world = generate_world([skill], rounds=10, rng=random.Random(1))
    run = run_ungoverned([skill], world, rounds=10)

    assert run.retired_rounds == {}
    fails = [a for a in run.log if a["skill"] == "fix-x" and not a["success"]]
    assert len(fails) == 5  # rounds 6-10: still applied every round despite rotting


def test_governed_regime_retires_a_skill_that_rots(monkeypatch) -> None:
    monkeypatch.setattr(library, "ACTIVE_CAP", 100)  # keep the cap out of the way
    skill = Skill(
        name="fix-x",
        disease=1,
        archetype="rotting",
        rate_before=1.0,  # always succeeds pre-rot
        rate_after=0.0,  # always fails post-rot
        arrival_round=1,
        rot_round=6,
    )
    world = generate_world([skill], rounds=10, rng=random.Random(1))
    run = run_governed([skill], world, rounds=10)

    # rounds 1-5 succeed, 6 and 7 fail back to back -> retires right at 7
    assert run.retired_rounds == {"fix-x": 7}


def test_build_population_splits_into_three_archetypes_with_staggered_arrivals() -> (
    None
):
    population = build_population(n_skills=9, rounds=90, rng=random.Random(1))

    archetypes = [s.archetype for s in population]
    assert archetypes.count("reliable") == 3
    assert archetypes.count("mediocre") == 3
    assert archetypes.count("rotting") == 3

    arrivals = [s.arrival_round for s in population]
    assert arrivals == sorted(arrivals), "arrivals trickle in, not all at once"
    assert arrivals[0] == 1
    assert arrivals[-1] > arrivals[0]

    for s in population:
        if s.archetype == "rotting":
            assert s.rate_before != s.rate_after
            assert s.rot_round is not None
            assert s.rot_round > s.arrival_round
        else:
            assert s.rate_before == s.rate_after
            assert s.rot_round is None


def test_compute_metrics_counts_applications_and_bad_outcomes() -> None:
    log = [
        {"round": 1, "skill": "a", "true_rate": 0.9, "success": True},
        {"round": 2, "skill": "a", "true_rate": 0.9, "success": True},
        {"round": 3, "skill": "b", "true_rate": 0.2, "success": False},
        {"round": 4, "skill": "b", "true_rate": 0.2, "success": False},
    ]
    m = compute_metrics(log, threshold=0.5, total_rounds=10)

    assert m["applications"] == 4
    assert m["bad_fraction"] == 0.5  # 2 of 4 applications went through skill b
    assert m["bad_failures"] == 2


def test_compute_metrics_reports_rounds_used_after_a_skill_rots() -> None:
    log = [
        {"round": 1, "skill": "b", "true_rate": 0.9, "success": True},
        {"round": 2, "skill": "b", "true_rate": 0.9, "success": True},
        {"round": 3, "skill": "b", "true_rate": 0.2, "success": False},
        {"round": 4, "skill": "b", "true_rate": 0.2, "success": False},
    ]
    m = compute_metrics(
        log,
        threshold=0.5,
        total_rounds=10,
        rot_rounds={"b": 3, "c": 5},
        end_rounds={"b": 4, "c": 2},  # c was retired at round 2, before it ever rotted
    )
    assert m["rot_lag_by_skill"] == {
        "b": 1,
        "c": 0,
    }  # never used again -> lag clamps to 0
    assert m["rot_lag_mean"] == 0.5


def test_governed_caps_active_size_while_ungoverned_grows_past_it(monkeypatch) -> None:
    monkeypatch.setattr(library, "ACTIVE_CAP", 5)
    population = build_population(n_skills=10, rounds=20, rng=random.Random(2))
    world = generate_world(population, rounds=20, rng=random.Random(3))
    governed = run_governed(population, world, rounds=20)
    ungoverned = run_ungoverned(population, world, rounds=20)

    assert max(governed.size_series) <= 5
    assert governed.size_series[-1] <= 5
    assert ungoverned.size_series[-1] == 10
    assert ungoverned.size_series[-1] > max(governed.size_series)
