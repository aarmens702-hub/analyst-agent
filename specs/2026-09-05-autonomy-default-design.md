# Autonomy-default mode: design (for owner review)

2026-09-05. **Status: design proposal. Owner approved the direction
(2026-09-05); this spec exists to be reviewed and edited before any code.**
This touches core (`policy.py` default and `loop.py` routing), so it runs
spec-first as reviewed diff packets, the way M1/M2/P7 did. No code until
reviewed.

Inputs: the owner's framing ("most people want autonomous agents, make human
approval the secondary option"), the M2 measurement, and the ceiling-arm
experiment already on disk (`bench/results/agent/*.ceiling.json`).

## Why

Two things point the same way.

- **The market.** Users want an agent they can leave alone. A gate on every
  action is friction, and friction loses adoption. crivo already auto-applies
  the safe (AUTO) fixes, so autonomy is not a new idea here; the only question
  is where the default line sits.
- **The measured evidence.** The ceiling arm (let the agent auto-decide the
  judgement calls it normally gates) repaired *fewer* cells than the governed
  arm (250 vs 500 over its case set) while acting on more gates, and the calls
  it was forced to make on person-grade findings, it got wrong. So autonomy
  pays on the *verifiable* majority (AUTO and GATE, which revert if the
  re-check fails) and stops paying, then starts harming, exactly at the
  *judgement* calls.

The reframe that follows: crivo can be autonomous *because* it verifies. The
receipt is not friction, it is the thing that makes unattended action safe. A
GATE fix that passes its re-check is safe to apply with no human in the loop;
if it does not verify, it reverts. That is a story no keyless competitor can
tell, because they cannot verify, so they cannot be safely autonomous. The
pitch becomes: "the data agent you can actually leave alone. It fixes what it
can prove, and stops only for the genuine judgement calls."

## What

An explicit autonomy setting. The default flips to autonomous for the
verifiable grades; human approval becomes an opt-in mode.

Proposed levels:

- **`autonomous` (new default).** AUTO and GATE findings apply unattended.
  Every applied fix is verified and reverted if its originating check still
  fires (verify-then-revert, unchanged). HUMAN / person-grade findings are
  *reported, not decided*. Skill admission stays human.
- **`careful` (opt-in, today's behavior).** Every GATE and HUMAN finding waits
  for approval, batched through the M2 plan-first gate. For regulated or
  high-stakes runs.
- **`report-only`.** Decide nothing, diagnose and report. Close to today's
  `diagnose()`; included so the three levels name the full spectrum.

## The hard line (stays in code, not config)

- The unbypassable forbid on **person-grade decisions and skill admission is
  not loosened.** Autonomous mode auto-applies AUTO and GATE (verifiable,
  revertible); it never auto-decides a person-grade / HUMAN finding and never
  admits a new skill. This is principled (a wrong fix reverts, a wrong
  judgement has no verifier to catch it) and it is what the ceiling arm
  measured.
- **Verification stays mandatory in every mode.** A fix counts only when its
  originating check re-runs clean. Autonomy does not relax this.

## How it maps onto existing machinery (a small change, not a rebuild)

The parts already exist:

- Grades (AUTO / GATE / HUMAN) already tag every finding.
- The policy layer already has ENFORCE / LOG_ONLY and a person-grade forbid
  checked before any policy (`policy.py`).
- The M1 autoclean rung already auto-applies AUTO findings that have a
  registered fixer, with the fingerprint re-check.

So `autonomous` is a default policy that ENFORCEs AUTO and GATE where the
re-check passes, with no gate, while the forbid still blocks person-grade and
admission. `careful` keeps the gate. The surface is one setting:
`--autonomy {autonomous,careful,report-only}` on the CLI and an `autonomy`
key in the governance config (`governance.py`), default `autonomous`.

## Requirements

- **R1 Forbid unchanged in code.** Person-grade and skill admission are never
  auto-decided in any mode. The existing forbid test still passes.
- **R2 Verification unchanged.** Verify-then-revert is mandatory in every mode.
- **R3 GATE auto-apply is conditional on a passing re-check.** A GATE fix
  whose re-check fails is reverted and surfaced, never left applied.
- **R4 Truthful recording in every mode.** The provenance transcript records
  the autonomy level and every auto-applied GATE with its verification, so an
  autonomous run is at least as auditable as a careful one.
- **R5 The mode is explicit and reversible.** It rides the existing flag /
  governance machinery, so the default can be flipped back with one setting.

## Acceptance

- A run at `autonomous` applies AUTO and GATE unattended, reverts any GATE fix
  whose re-check fails, and stops (reports) at every HUMAN / person-grade
  finding.
- A run at `careful` reproduces today's gated behavior (M2 plan-first).
- The person-grade forbid test passes in all modes.
- A bench arm compares `autonomous` vs `careful` (reusing the ceiling-arm
  harness where it fits) so the change is measured, not asserted.
- Suite green; the full CI pipeline green; no regression on the existing 22.

## Build plan (reviewed packets, as M1/M2)

1. This spec, reviewed by the owner.
2. The `autonomy` setting in `governance.py` + the default policy in
   `policy.py` (small reviewed diff).
3. Loop routing by autonomy level, including GATE auto-apply-with-revert
   (`loop.py`, reviewed packet).
4. Provenance records the level and each auto-applied GATE + its verification.
5. Bench arm + the numbers.

## Open questions (owner decides)

1. **Default reach.** Is `autonomous` the shipped default outright, or default-
   on with a first-run notice the first time it acts unattended?
2. **GATE scope.** Auto-apply all GATE findings, or start with the
   GATE-with-registered-fixer subset (mirroring M1's AUTO rung) and widen
   later?
3. **Third level.** Keep `report-only` as a named level, or leave that to
   `diagnose()`?
4. **Careful ergonomics.** Does `careful` always batch through M2 plan-first,
   or offer per-finding gates as an option?

## Non-goals (this phase)

Not loosening the forbid. Not auto-deciding person-grade findings. Not removing
or weakening verification. Not a new approval UI; this reuses the existing gate
and policy surfaces.
