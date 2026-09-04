# Attack roadmap A: the agent system

2026-09-04. How crivo thinks and acts. Synthesizes the prior plan (plan-first
execution, subagents, Phase 8 memory, MCP port) with
`docs/research/2026-09-04-agent-system-gaps.md`. Companion:
`specs/2026-09-04-capability-roadmap.md` (what the agent can do).

Standing rules: the core loop, prompts, skill lifecycle, and provenance DAG
are Aarmen's (specs and diffs are proposed, never landed unasked). No agent
frameworks. Verification over trust. Person-grade calls and skill admissions
are never auto-decided, written as code, not convention. A 12-case sampled
bench run (same seed) after every phase below; the blessed full run happens
once at the end.

Research verdict worth keeping: the loop itself is ahead of the field; every
gap is in the layer around it. Spend the roadmap on wall-clock time and human
attention, not on loosening the loop.

## A0. Feel and visibility (days each, mostly plumbing lane)

- A0.1 **Streaming + interrupt/steer.** Stream tokens through the generate()
  seam; cancel an in-flight call keeping completed work; queued steer note
  consumed at the next gate. [research gap 1]
- A0.2 **Run telemetry.** JSONL spans on the OTel GenAI attribute names: per
  call, cell, check, gate; tokens, cost, latency, cache-hit tokens, model,
  outcome. Shares IDs with the provenance DAG. Everything later needs these
  numbers, so it lands before them. [gap 5]
- A0.3 **Cache-aware prompts.** Stable prefix (system, checks, schema) before
  volatile content; no timestamps early; verify hit rates via A0.2. Locks in
  append-only history, which A1 and A3 must obey. Collapse fix + verify-cell
  proposal into one call where possible. [gap 2] (prompts are core: proposal)

Exit: cache-hit rate visible and above 70%; time-to-first-token under 2s.

## A1. The planning core (core: spec proposal first)

- A1.1 **Plan-first execution, with repair.** One planning call proposes an
  ordered fix plan; the human approves the plan; steps execute with per-step
  verification. Built-in from day one: replan triggers (step fails, or a fix
  invalidates a later finding, CLEAN's normal case), plan versioning, replans
  presented as small diffs at the next gate, the model may flip step status
  but never rewrite scope silently. The approved plan doubles as a
  control-flow-integrity boundary. No tree search. Reserve: verifier-gated
  best-of-N on a single finding after a failed fix. [prior plan + gap 7]
- A1.2 **Approval policy layer + fatigue metrics.** Persistent policy object:
  safe-grade findings of named check types with passing re-checks may batch
  under one approval; plan approval is the coherent-unit tier. Instrument
  per-gate decision latency, edit rate, reversal rate (rides A0.2). Ceiling
  as code: policies can never cover person-grade or admissions. [gap 4]

Exit: calls per CLEAN run cut by half or better on the bench sample;
gate count per run down without any person-grade auto-decision.

## A2. The latency attack

- A2.1 **CRIVO_MODEL experiment** (carryover): rerun notes-truncation on a
  faster model; publish the per-call numbers from A0.2.
- A2.2 **Verified small-model routing.** Static router by check type and
  grade: mechanical fixes to a flash-class model, hard and multi-column to
  pro; a still-firing check escalates that one finding to pro. Bench arm
  before default-on. [gap 3]
- A2.3 **Speculative drafting during gate waits** (experimental, only if A1.2
  metrics show high approval rates): draft the next step while the human
  reads; discard on reject; drafts never touch the kernel. [gap 10]

Exit: median wall-clock per case halved vs the 2026-09-04 baseline table.

## A3. Scale-out and memory (core-adjacent: proposals)

- A3.1 **Read-only fan-out.** Parallel per-column profiling and diagnosis
  only. Workers return findings, never cells; the single main loop proposes
  cells; gates stay central; concurrency capped; token multiplier measured
  in a bench arm. Fixes are never parallel. [prior plan + gap 9]
- A3.2 **Phase 8 memory, hardened.** Episodic/semantic/procedural over one
  ledger, with: writes gated and verified like fixes; memories typed with
  source session, dataset fingerprint, timestamp; retrieval filtered by
  fingerprint validity; person-grade findings and raw-row-adjacent content
  unwritable; human-legible markdown storage. [prior plan + gap 8]
- A3.3 **Verified answers memory** (bridge item, shared with roadmap B):
  recurring questions pin to approved named code via the skills mechanism;
  the card says when a saved recipe answered.

Exit: fan-out shows wall-clock win at acceptable token cost on the bench;
memory demo: second session on the same dataset reuses valid memories and
rejects fingerprint-stale ones.

## A4. Interop and proof

- A4.1 **MCP port on the 2026-07-28 spec**, expressing gates as MRTR
  elicitation (input_required carrying the proposed cell and grade) and CLEAN
  runs as Tasks. Unblocks registry listings (capability roadmap). [gap 11]
- A4.2 **Workspace versioning** delivered with scoped file ops: version the
  workspace per gate so revert stays true once fixes write files. [gap 12]
- A4.3 **Bench upgrades:** pass^k consistency column, cost-per-task and
  wall-clock columns (from A0.2), abstention cases graded on declining, and
  a DABstep-subset adapter as the external anchor. [gap 6]
- A4.4 **The blessed full-corpus run.** Both arms, external sets, agent
  columns into RESULTS.md and README. Only after A1-A2 land.

Explicitly not doing: tree/beam search over plans, LLM-judge as primary
grader, durable-execution platforms, per-call LLM routers, distributed gates,
earned-trust auto-approval of person-grade calls.
