# M2 core packet: planner call + replan-by-diff (T2.3/T2.4)

2026-09-04. **Status: proposal for owner review.** Touches the core
(`loop.py` `_clean`, `prompts.py`), so nothing lands until you choose the
option and approve the hunks. The modules it builds on are merged and green:
`plan.py` (PlanStep/Plan/build_plan/diff_plan), `governance.py`, `router.py`,
`policy.py`. 641 tests pass without any of this wired in.

## What M2 adds on top of M1

M1 already gives the per-finding loop a deterministic rung and policy
batching. M2 lifts the decision from per-finding to **per-plan**: one
planning pass proposes an ordered plan over all findings, you approve the
plan as one coherent unit, then execution walks the plan; when reality
diverges, a small approved diff repairs the plan rather than a fresh one.

The value beyond M1: one approval instead of N gates, an auditable plan
artifact in the transcript and /why, and the ordering intelligence that lets
"fix #2 obsoletes finding #5" be handled deliberately instead of by luck.

## The choice: how much of `_clean` to restructure

### Option M2-min (recommended first): plan as an approval wrapper

Keep today's per-finding execution loop exactly. Add a planning pass in
front of it:

1. `build_plan(fixable)` from the already-computed findings (no model call:
   build_plan uses the router).
2. Render the plan once and gate it as the coherent unit (the approval
   policy layer's batch tier): approving the plan arms an in-session ENFORCE
   policy over its AUTO+autoclean steps, so those steps then run through M1's
   existing batched path with no further gates. GATE/HUMAN steps still gate
   per-finding as they do today.
3. Execute the existing loop, now in plan order, recording each step's
   status onto the Plan (fixed/skipped/failed) for the transcript and /why.
4. Replan-min: if re-diagnosis after a fix shows a planned finding already
   cleared, `diff_plan` marks it obsolete and the loop skips it. No new
   model call, no new gate (an obsolete step is a removal, and the plan
   approval already covered "these findings").

This is days, not the full restructure; it captures the one-approval win and
the plan artifact; it defers model-authored replanning. The planner "call"
is deterministic (build_plan), so there is no new prompt yet.

### Option M2-full: model-authored plan + replan

Everything in M2-min, plus a real planning model call (a new
`prompts.PLAN_PROMPT`) that can reorder steps, group related findings, and
propose an approach per step; and model-authored replan diffs when a step
fails twice or a new finding appears post-fix. This is the research-backed
endgame but the larger core surgery, and its value over M2-min is unproven
until M2-min's telemetry shows where the model's planning would beat the
deterministic order. Recommend deferring it behind M2-min's numbers.

## M2-min hunks (for review, if you pick it)

### Hunk 1: `_build_and_approve_plan` (new method, loop.py)

```python
    def _build_and_approve_plan(self, fixable: list[dict]):
        """Build a plan from the findings and gate it as one unit (T2.4,
        M2-min). Approving arms an in-session ENFORCE policy over the plan's
        AUTO+autoclean steps so they run through M1's batched path; GATE and
        HUMAN steps still gate per finding. Returns (Plan, approved: bool)."""
        plan = plan_mod.build_plan(fixable)
        yield StreamText("stdout", "\n" + plan.summary() + "\n" + _plan_table(plan))
        auto_ids = [
            s.disease for s in plan.steps
            if s.executor == "autoclean" and s.grade == "AUTO"
        ]
        if not auto_ids:
            return plan, True  # nothing batchable; per-finding gates as today
        decision = yield GateRequest(
            _plan_table(plan), 1, title=f"approve {plan.summary()}", grade="PLAN"
        )
        if not isinstance(decision, GateDecision):
            decision = GateDecision("run")
        self.transcript.append("gate", action=decision.action, note="plan")
        if decision.action == "run":
            self.policies = [*self.policies, policy.PolicyRecord(
                id=f"plan-v{plan.version}", disease_ids=tuple(sorted(set(auto_ids))),
                approver="plan-approval", expires=_today_plus(1),
                mode="ENFORCE", valid_disease_ids=set(detect_slugs()),
            )]
        return plan, decision.action != "skip"
```

### Hunk 2: `_clean` calls it and records step status

Between the diagnosis block and the per-finding loop, after `fixable` is
set: `plan, proceed = yield from self._build_and_approve_plan(fixable)` and
skip the loop when not `proceed`. Inside the loop, after each `rec`, set the
matching step's status via `diff_plan` and re-emit nothing (the plan lives
in `state["plan"]` for `_save_report`). Report gains a `plan` block.

### Hunk 3: replan-min on obsolete findings

After a verified fix refreshes the baseline, a light re-diagnosis (the
diagnosis cell already exists) checks whether any not-yet-attempted planned
finding is now in `clear`; those steps get `diff_plan(plan, {fid:
"obsolete"})` and are skipped. Bounded: one re-diagnosis per verified fix,
which the profile print already pays for.

## Open questions

1. Option M2-min or M2-full? (Recommend M2-min now, M2-full behind its
   telemetry.)
2. A new `grade="PLAN"` gate tier for the plan approval, or reuse an
   existing verb? The events.GateRequest.grade field is free-form today.
3. Plan approval arms a 1-day in-session policy (hunk 1). Prefer that, or a
   session-lifetime policy, or no persistence (approve-once, re-plan each
   `/clean`)?
4. `_plan_table` rendering: reuse the diagnosis-text style, or a compact
   one-line-per-step form? (Rendering is T2.2, delegable once this shape is
   fixed.)

## What is delegable after you choose

T2.2 (plan rendering `_plan_table` + REPL display) and T2.6 (trajectory
telemetry: executed-steps vs approved-plan diff) become new-module/additive
tasks once the plan-execution shape above is fixed, so they can run as an
agent wave while the core hunks go through your review.
