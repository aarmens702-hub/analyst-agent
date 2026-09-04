# A1 build plan: staged plan-first execution, delegated

2026-09-04. The executable plan for Roadmap A's core phase. Inputs:
`2026-09-04-a1-plan-first-proposal.md` (design),
`docs/research/2026-09-04-mining-synthesis.md` (amendments),
`docs/research/2026-09-04-agent-system-gaps.md` (field research),
the flash experiment, and the owner's decisions below.

## Decisions encoded

- D1 **Architecture: staged 2 into 3** (owner call). M1 delivers Option 2
  (batching + deterministic executors), M2 delivers the plan artifact and
  policy layer (Option 1), M3 adds the ambitious pieces (Option 3) only
  where M1/M2 telemetry justifies them.
- D2 **Safe steps run strictly post-approval** (default; owner may flip to
  scratch-pre-execution later, nothing in M1/M2 blocks that).
- D3 **Core diffs: Claude drafts behind owner review** (default). Every
  change to loop.py/prompts.py ships as a reviewed diff packet; the owner
  lands it or edits it. Owner may take any task back at will.
- D4 **Delegation: hybrid.** Parallel-safe tasks go to worktree agents under
  the brief contract below; shared-file integration and all core diff
  packets stay with the orchestrating session; owner approves core.

## Milestones, each exiting through the same gate

Exit gate for every milestone: suite green, ruff clean, 12-case same-seed
governed bench run; scores not worse; calls per case and wall-clock
reported with cache-read tokens excluded from cost columns; results stay
terminal-only (R6).

### M1 - batching and deterministic executors (Option 2, days)

- T1.1 **Grade router.** Pure, dependency-light module mapping a finding to
  an executor (autoclean fixer id or model) keyed on the 22-disease
  taxonomy's autonomy column. Adversarial tests: a model self-report can
  never raise a grade; person-grade never routes to auto. [agent, worktree]
- T1.2 **Re-check fingerprint skip.** Frame fingerprint before/after a fix;
  unchanged state means a counted failed attempt with no rerun and
  structured feedback ("attempt N of M", the check's output). [agent]
- T1.3 **Approval batch object v0.** A minted policy record (id, matched
  safe-grade disease ids, expiry, approver) with ENFORCE and LOG_ONLY
  modes; denials are structured events naming the failed condition.
  [agent]
- T1.4 **Loop integration diff.** Wire T1.1-T1.3 into the CLEAN flow as the
  smallest possible diff packet for owner review. [orchestrator -> owner]
- T1.5 **Bench arm + telemetry columns.** Calls per case, wall clock, cache
  hit rate, cost excluding cache reads; comparison table against the
  2026-09-04 baseline. [agent]

Pre-work for T1.1 (orchestrator, hours): inventory which of the 22 diseases
have deterministic autoclean fixers and their coverage; that inventory is
the router's fixture.

### M2 - plan artifact and policy layer (Option 1 core, about a week)

- T2.1 **Plan object.** Structured steps (finding id, disease id, grade,
  executor, expected check), versioned, diffable; never prose. [agent]
- T2.2 **Plan rendering.** REPL gate render (summary with expand), transcript
  and provenance recording. [agent]
- T2.3 **Planner prompt and call.** Cache-safe (stable prefix, append-only
  history), reuses the keyless findings. Prompts are core: drafted as a
  proposal packet. [orchestrator -> owner]
- T2.4 **Replan by diff.** Triggers (step fails twice, finding fixed en
  passant, new finding post-fix), diffs approved at the next gate, step
  status flips only. Core diff packet. [orchestrator -> owner]
- T2.5 **Policy layer full.** Cedar-shaped evaluator over policy objects:
  default deny, permits keyed on disease ids validated against the
  taxonomy at admission, person-grade-never-auto as an unoverridable
  forbid, LOG_ONLY shadow week before arming, one flat schema-validated
  governance config. [agent]
- T2.6 **Trajectory telemetry.** Executed-steps vs approved-plan diff
  recorded even when all checks pass; judge rubric stub for answer-card
  prose as a sampled monitor, never a gate. [agent]

### M3 - earned additions (gated on M1/M2 telemetry)

- T3.1 Verifier-gated best-of-N on a step that failed once (checks pick the
  winner; generation cost capped at N=3). Requires M2 step machinery.
- T3.2 Speculative drafting during gate waits. Requires approval-rate and
  edit-rate data from T2.6 showing high approve rates.
- T3.3 Skill short-circuit: a promoted skill matching dataset fingerprint
  and finding routes straight to its fixer with no planning call. Requires
  M2; pairs with the A3 memory work.

## The delegation contract (every agent brief)

- Goal: one paragraph, one outcome.
- Files owned: exclusive list; touching anything else is a failed task.
  Core files (loop.py, prompts.py, skills.py, provenance.py) are never in
  an agent's list.
- Constraints: test-first, ruff clean, repo idiom, no new dependencies, no
  em dashes in docs, keyless import-safety preserved.
- Definition of done: new tests green, full suite green, ruff clean, a
  summary naming files changed, test names, and open questions.
- Return format: summary + diffstat; no transcripts.

Execution mechanics: each parallel task runs in its own worktree; the
orchestrating session verifies (suite, ruff), merges sequentially, then
assembles core diff packets for the owner. M1's T1.1/T1.2/T1.3/T1.5 can run
as one four-agent workflow wave; M2's T2.1/T2.2/T2.5/T2.6 as a second wave
after M1 lands.

## Risks and mitigations

- Plan staleness churn in M2: replan-by-diff is designed in; M1 lands the
  batching win first so slippage in M2 never strands the latency gain.
- Bench resolution at n=12 is about 8 points: treat small deltas as noise;
  pass^k and the bigger sample arrive with A4.
- Guard friction: tdd-guard is disabled in the orchestrating session for
  the build; re-enable both configs when A1 lands, or run future crivo
  sessions from the analyst-agent root where the guard works.
- Core review is the throughput bottleneck by design: diff packets are kept
  small and single-purpose so review stays minutes, not hours.

## Standing constraints (unchanged, from the mining and the contract)

Sandbox posture untouched. Person-grade and admissions human forever,
enforced as code. No critic/debate agents in the loop. No frameworks. Every
milestone measured before the next begins.
