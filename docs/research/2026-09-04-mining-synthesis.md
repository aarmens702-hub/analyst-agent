# Synthesis: what the mailo + prime-agent mining changes in Roadmap A

2026-09-04. Amendments to `specs/2026-09-04-agent-system-roadmap.md` implied
by `2026-09-04-mailo-prime-agent-mining.md`. Nothing here is built; this is
planning input for the owner's decisions.

## Amendments by roadmap phase

**A0 (telemetry, landed + remaining):**
- Thread one run id through every JSONL record, workers and judge verdicts
  included; record the policy decision on each fix event; cap batch and
  field sizes now. [mailo 11]
- Bench cost columns exclude cache-read tokens so budget measures new work.
  [prime 2]

**A1a (safe-step batching):**
- Re-check skip when a fix changed no state: fingerprint the frame before
  and after; unchanged means failed attempt without a rerun. [prime 1]
- Structured failure feedback into the next attempt ("attempt N of M", the
  check's output), tri-state step outcomes. [prime 2]

**A1b (plan artifact + approval policies):**
- Plan steps are STRUCTURED objects (finding id, disease id, grade,
  executor, expected check), never prose: mailo's free-text briefs are the
  cautionary tale. [mailo anti-1]
- The policy layer copies Cedar's shape: default deny; permits keyed on
  disease ids (the taxonomy's AUTO/GATE/HUMAN column is the ready-made
  vocabulary and validation schema); conditions over structured finding
  fields; person-grade-never-auto as an unoverridable forbid; every new
  policy ships in LOG_ONLY shadow mode first and arms only after the human
  reviews what it would have batched. [mailo 1, coop]
- An approved policy is a minted scoped object (id, matched types, expiry,
  approver) verified per batched action. [mailo 10]
- A denial is a structured, replannable event naming the failed condition.
  [mailo anti-4]
- Each gate rule lives in three places on purpose: planner prompt (advise),
  gate (enforce), judge rubric (measure drift). [mailo 2]
- Trajectory diff: executed steps vs approved plan recorded even when all
  checks pass. [mailo 6]
- No reviewer/critic agent inside the plan loop (measured harm in the
  owner's own research). [coop]
- Grade assignment and policy evaluation as pure, dependency-light modules
  with adversarial regression tests ("a model self-report can never raise a
  grade"). [mailo 9]

**A2 (routing):**
- No silent model substitution ever; escalation allowed only before partial
  output exists, otherwise fail loudly and replan. [prime 6, mailo 8]

**A3 (fan-out + memory):**
- Workers: read-only enforced by API surface; flat depth-1, no worker spawns
  workers; capability manifests the planner selects against; results as one
  final artifact after progress lines; fire-and-forget handles with cost
  attributed to the parent. [prime 6-7, mailo 3+7, coop]
- Memory: fingerprint-scoped namespaces with no-prefix-collision keys; hard
  top-k retrieval budget; expiry backstop; writes carry expectedOutcome and
  rollback (per-edit snapshots, JSONL history); a cheap pre-filter rejects
  noise before the human gate; and the short-circuit: a promoted skill
  matching fingerprint + finding routes straight to the deterministic fixer
  with no planning call. [prime 3-4, mailo 5]

**A4 (bench + interop):**
- Bench cases behind one loader contract (data + rubric + scorer), taskset
  split from harness so both arms share cases, reward-diversity check before
  promotion statistics count. [prime 5]
- Judge for answer-card prose: config-declared rubric with score bands,
  10-25% adaptive sampling, verdicts into telemetry, CI gate over replayed
  sessions, monitor only, never blocking. [mailo 4]
- Drift-check scripts wherever two places maintain one list. [prime 11]
- Provenance backed by a commit-gated event log derived by pure fold; the
  same log doubles as kernel crash recovery. [prime 8, coop]

## New roadmap candidates (not previously on any list)

1. **The intent gate** (from the owner's own brief): one call restating what
   the executed code computed, diffed against the question, blocking on
   mismatch. Catches "correct code, wrong question." Candidate: lands with
   answer-card work, measured like everything else.
2. **Flat governance config**: policies, judge rubric, sampling, memory
   namespaces, routing thresholds in one schema-validated file so the
   governance posture reviews in a single diff.

## Standing constraints reaffirmed by both repos

Keep the sandbox posture (prime-agent disclaims real isolation; crivo's is
stronger). Keep human-gated memory and skill writes (both repos autonomize
this; the owner's design is the disciplined version). Keep the hand-written
loop (mailo's framework dependence is its own master plan's listed
weakness).
