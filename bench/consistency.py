"""pass^k consistency scoring for the agent bench (A4, research gap 6).

The model-path cases vary run to run: the M1 exit report's tx-dates-mixed
scored a perfect 1.0 one run and produced no cleaned frame the next, purely
on model variance at a GATE finding. A single run therefore cannot separate a
real regression from luck. This module scores repeated runs of the same case
and reports two numbers from the tau-bench literature: pass^k (solved on
every one of k runs, the reliability metric) and pass@1 (solved on average),
plus the flaky set, the cases solved sometimes and not others.

It runs no model: it is pure analysis over the result dicts the bench already
writes. Firing k real runs per case is the caller's paid choice.
"""

from __future__ import annotations

_OK = ("ok", "ok_unchanged")


def solved(run: dict, threshold: float = 1.0) -> bool:
    """One run counts as solved when it finished cleanly and, where repair was
    measurable, met the threshold. A held case that correctly changed nothing
    (ok_unchanged with an unmeasurable repair F1) counts as solved: deferring a
    judgement call is the right outcome, not a failure."""
    if run.get("status") not in _OK:
        return False
    f1 = (run.get("scores") or {}).get("repair", {}).get("f1")
    if f1 is None:
        return True  # unmeasurable repair (held / drop-fix); clean finish stands
    return f1 >= threshold


def score_pass_k(runs: list[dict], threshold: float = 1.0) -> dict:
    """pass^k and pass@1 over repeated runs of ONE case (research gap 6)."""
    k = len(runs)
    hits = sum(1 for r in runs if solved(r, threshold))
    return {
        "k": k,
        "solved": hits,
        "pass_k": 1.0 if k and hits == k else 0.0,
        "pass_at_1": (hits / k) if k else 0.0,
    }


def group_by_case(rows: list[dict]) -> dict[str, list[dict]]:
    """Bucket flat result rows by their case name for aggregation."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["name"], []).append(row)
    return grouped


def aggregate_consistency(by_case: dict[str, list[dict]], threshold: float = 1.0):
    """Per-case pass^k / pass@1 plus the honest splits: `flaky` is the set the
    bench cannot trust from one run (0 < pass@1 < 1), `stable_solved` always
    passes, and the means summarize the corpus."""
    per_case = {name: score_pass_k(runs, threshold) for name, runs in by_case.items()}
    flaky = sorted(n for n, s in per_case.items() if 0.0 < s["pass_at_1"] < 1.0)
    stable_solved = sorted(n for n, s in per_case.items() if s["pass_at_1"] == 1.0)
    n = len(per_case) or 1
    return {
        "cases": len(per_case),
        "per_case": per_case,
        "flaky": flaky,
        "stable_solved": stable_solved,
        "mean_pass_k": sum(s["pass_k"] for s in per_case.values()) / n,
        "mean_pass_at_1": sum(s["pass_at_1"] for s in per_case.values()) / n,
    }
