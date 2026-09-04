# A1 proposal: plan-first execution (WRAP)

2026-09-04. **Status: PROPOSAL.** This touches the hand-written core (loop.py,
prompts.py), so nothing here lands until Aarmen approves or edits it; Claude
supplies tests, telemetry hooks, and bench arms either way. Inputs: the
roadmap (A1), research gaps 4 and 7, the flash experiment
(docs/research/2026-09-04-flash-experiment.md), and today's code audit.

## What

CLEAN today goes finding-by-finding: every fix is its own model roundtrip,
measured at ~107s (pro) to ~131s (flash) per call, and both models pay it, so
the cost is prompt-shaped, not model-shaped. Plan-first replaces N roundtrips
with: one planning call that proposes an ordered fix plan over the findings
the keyless diagnose already produced; one human approval of the plan as a
coherent unit; then step execution where safe mechanical steps skip the model
entirely and verification runs per step exactly as today; with replanning by
small approved diffs when reality diverges.

The observation that pays for the feature: crivo already computes the full
findings list keylessly, and autoclean already owns deterministic fixers for
safe-grade diseases. The agent currently spends model calls re-deriving fixes
the library half knows. A plan can route those steps to autoclean at zero
model cost and reserve the model for check- and person-grade steps.

## Requirements

- R1 **The plan is an artifact.** Ordered steps, each carrying: finding id,
  disease, grade, proposed approach, executor (autoclean fixer | model),
  and the check expected to go quiet. The plan is versioned, recorded in the
  transcript and the provenance DAG, and rendered at one gate for approval
  with grades visible.
- R2 **Execution respects grades.** Safe-grade steps with passing re-checks
  execute under the plan approval (the coherent-unit batch from the approval
  policy layer). Check-grade steps still gate individually unless a policy
  object covers them. Person-grade steps always gate individually. Skill
  admissions unchanged: human, always.
- R3 **Replan by diff, never by rewrite.** Triggers: a step's fix fails
  verification twice; a later finding stops firing (fixed en passant); a new
  finding appears post-fix. The model proposes a plan diff (add, remove,
  mark-obsolete); the diff is approved at the next gate; step statuses flip,
  scope is never silently rewritten. An approved plan doubles as a
  control-flow-integrity boundary.
- R4 **Call economics.** The planning call's prompt = stable cached prefix +
  profile + findings list (all precomputed). Deterministic-executor steps
  make no model call. Expected shape on the bench sample: one plan call plus
  model calls only for the judgment steps, instead of one call per finding.
- R5 **Cache discipline.** Append-only history: the plan and its diffs append;
  nothing earlier is rewritten (A0 R5 constraint, keeps prefixes cacheable).
- R6 **Failure containment unchanged.** Failed fixes revert as today; caps
  unchanged; two failures on a step trigger replan rather than blind retry.
- R7 **Measured.** Plan span + per-step spans via crivo.telemetry, so
  plan-first vs legacy is a bench comparison, not an opinion.

## Acceptance

- On the 12-case sample (same seed): model calls per CLEAN case down at least
  half vs the 2026-09-04 baseline; median wall-clock halved; repair and
  targeting scores not worse; person-grade gate count unchanged.
- notes-truncation completes governed inside 600s.
- Every plan, diff, and approval legible in the transcript and /why.
- Suite green; new behavior test-first; a legacy-mode flag remains until the
  bench says plan-first wins.

## Non-goals

Tree or beam search over plans; auto-approving person-grade anything;
parallel step execution (A3's business); rewriting prompt history.

## Open questions for Aarmen

1. Plan-approval UX in the REPL: full plan render, or summary with expand?
2. Safe-grade steps: strictly post-approval (recommended: simpler trust
   story), or pre-executed on scratch with revert-on-rejection?
3. Plan persistence across session resume: replay from transcript, or
   serialize the plan object?
4. Who implements the loop changes: Aarmen with Claude tests alongside, or
   Claude behind a reviewed diff? (Either honors the contract; the second is
   faster, the first keeps the core fully hand-written.)
