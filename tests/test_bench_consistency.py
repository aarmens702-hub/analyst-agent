"""pass^k consistency scoring (A4, docs/research/2026-09-04-agent-system-gaps.md
gap 6): the model-path bench cases vary run to run (the M1 exit report's
tx-dates-mixed / tx-out-of-domain case), so a single run cannot tell a real
regression from luck. This scores repeated runs of the same case: pass^k
(solved on every run) and pass@1 (solved on average), and names the flaky
cases. Pure analysis over result JSONs; it runs no model."""

from bench import consistency


def _run(status="ok", f1=1.0):
    scores = {"repair": {"f1": f1}} if f1 is not None else {"repair": {"f1": None}}
    return {"status": status, "scores": scores}


def test_solved_predicate_default():
    assert consistency.solved(_run("ok", 1.0))
    assert consistency.solved(_run("ok_unchanged", None))  # held, changed nothing
    assert not consistency.solved(_run("ok", 0.6))  # scored but below threshold
    assert not consistency.solved(_run("no_cleaned_output", None))
    assert not consistency.solved({"status": "aborted: wall cap", "scores": {}})


def test_pass_k_all_runs_solved_is_one():
    runs = [_run("ok", 1.0), _run("ok", 1.0), _run("ok", 1.0)]
    out = consistency.score_pass_k(runs)
    assert out["k"] == 3
    assert out["pass_k"] == 1.0
    assert out["pass_at_1"] == 1.0
    assert out["solved"] == 3


def test_pass_k_one_miss_drops_pass_k_to_zero_but_not_pass_at_1():
    runs = [_run("ok", 1.0), _run("no_cleaned_output", None), _run("ok", 1.0)]
    out = consistency.score_pass_k(runs)
    assert out["pass_k"] == 0.0  # not solved on every run
    assert round(out["pass_at_1"], 3) == 0.667  # solved 2 of 3
    assert out["solved"] == 2


def test_aggregate_flags_flaky_cases_only():
    by_case = {
        "tx-dates-frozen": [_run("ok", 1.0), _run("ok", 1.0)],  # always solved
        "tx-dates-mixed": [_run("ok", 1.0), _run("no_cleaned_output", None)],  # flaky
        "pairs-contradictions": [
            _run("no_cleaned_output", None),
            _run("no_cleaned_output", None),
        ],  # never solved (not flaky, just unsolved)
    }
    agg = consistency.aggregate_consistency(by_case)
    assert agg["cases"] == 3
    assert agg["flaky"] == ["tx-dates-mixed"]  # 0 < pass_at_1 < 1 only
    assert agg["stable_solved"] == ["tx-dates-frozen"]
    assert round(agg["mean_pass_k"], 3) == round(1 / 3, 3)  # only frozen is pass^k
    assert round(agg["mean_pass_at_1"], 3) == 0.5  # (1 + 0.5 + 0) / 3


def test_custom_threshold_and_predicate():
    runs = [_run("ok", 0.8), _run("ok", 0.9)]
    assert consistency.score_pass_k(runs, threshold=0.75)["pass_k"] == 1.0
    assert consistency.score_pass_k(runs, threshold=0.95)["pass_k"] == 0.0


def test_group_runs_by_case_name():
    rows = [
        {"name": "a", "run": 1, "status": "ok", "scores": {"repair": {"f1": 1.0}}},
        {"name": "a", "run": 2, "status": "ok", "scores": {"repair": {"f1": 1.0}}},
        {"name": "b", "run": 1, "status": "ok", "scores": {"repair": {"f1": 1.0}}},
    ]
    grouped = consistency.group_by_case(rows)
    assert set(grouped) == {"a", "b"}
    assert len(grouped["a"]) == 2 and len(grouped["b"]) == 1
