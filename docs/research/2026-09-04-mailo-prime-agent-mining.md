# Research: patterns mined from mailo and prime-agent

2026-09-04. Two read-only agents explored the owner's other repos for
agent-architecture ideas transferable to crivo. This file preserves both
reports condensed with their file-path evidence; the fusion into Roadmap A is
in `2026-09-04-mining-synthesis.md`. Companion inputs to
`specs/2026-09-04-agent-system-roadmap.md`.

## Part 1: prime-agent (fork of PrimeIntellect-ai/prime-agent v0.9.1)

The repo is the owner's fork of the Prime Agent RLM harness (arXiv
2608.23552): model's only built-in tool is a persistent IPython kernel;
subagents, skills, goals are Python calls bridged to a TypeScript host owning
all state and policy. The owner's delta is two upstream-PR-shaped commits:
c5df2044a fixes the subagent roster to include passivated children (with a
92-line fail-first regression test) and 80e50d4f8 adds a CI script deriving
the credential-var list from source and failing on drift. Both are
verification instincts.

Ranked steals:

1. **Workspace-fingerprint gating of re-checks** (core/autonomous.ts,
   captureGitWorktreeSnapshot): refuse to rerun a failed gate when nothing
   changed since the last failure; count the attempt; tell the model to edit
   something first. crivo: skip the deterministic re-check when a "fix"
   touched no state. Low effort, perfect fit.
2. **Gate loop with tri-state outcomes and structured failure feedback**
   (passed / failed / retry_exhausted; "attempt N/max" with truncated output
   in the continuation; budgets on four axes; token accounting excludes cache
   reads so budget measures new work). crivo: the plan-first execution shape;
   cache-read exclusion belongs in bench cost columns.
3. **expectedOutcome on every learned entry** (refinement.ts): every
   prompt/memory/skill/subagent edit carries summary, rationale,
   expectedOutcome ("what should improve and how to validate it"), per-edit
   before/after snapshots, rollbackOf, JSONL history; base system prompt
   unwritable. crivo: the write format for the three-memory design, and the
   principled replacement for 3-wins/2-failures: a skill declares its own
   validation criterion at admission. Medium effort, very high fit.
4. **Two-stage write gate** (AUTO_REFINE_REVIEW prompt): a cheap reviewer
   rejects "one-off noise, unsupported hypotheses, transient tool outputs"
   before anything is proposed. crivo: small-model pre-filter before the
   human gate on memory writes. Low effort, high fit.
5. **Verifiers environment packaging** (skills/prime-intellect/references/
   environments.md): load_environment(config) bundling dataset + rollout +
   rubric; smoke at n=5; fixed-seed shuffles; "confirm reward diversity
   exists at baseline" before trusting scores. crivo: one loader contract per
   bench case, taskset split from harness so both arms share cases, and
   promotion statistics only over cases that discriminate. Medium effort,
   high fit.
6. **Fire-and-forget admission handles** (rlm-runtime): spawn returns a
   handle, never the answer; results arrive as messages/files; cost
   attributed to parent without inflating parent context; depth capped;
   unavailable model fails the spawn, never silently substitutes. crivo:
   fan-out contract + the no-silent-fallback rule for routing.
7. **Read-only enforced by API surface** (agent-observe skill): observation
   exposes no mutation commands and bounds every read. crivo: diagnosis
   workers get exactly this interface.
8. **Commit-gated event-sourced provenance** (semantic-edges.ts): append-only
   JSONL written before effects; edges derived by pure fold; materialize only
   on commit; idempotency keys as content hashes. crivo: the provenance DAG
   can never cite unfinished work.
9. **Typed host-request bridge** (rlm-runtime): privileged operations are
   typed requests the host validates; credentials never enter Python.
10. **Hook decomposition of a hand-written loop** (agent-loop.ts):
    beforeToolCall blocks (gate insertion point), afterToolCall rewrites
    (verdict stamping), ordered post-turn message sources (steering,
    follow-up, continuation). Validates the no-framework stance; a clean seam
    list for approval policies.
11. **Drift-check scripts** (the owner's own 80e50d4f8): wherever two places
    hand-maintain one list, derive and fail CI on drift. crivo: skill
    registries, gate lists, bench indexes.

Anti-patterns: process isolation passed off as sandboxing (crivo's is
stronger); autonomous self-modification of memory/skills with only an LLM
reviewer (take the schema, keep the human gate); ungated
continue-until-budget as default; brace-slicing JSON from prose for durable
state; multi-session daemon scope sprawl.

## Part 2: mailo (Bedrock AgentCore multi-agent campaign planner)

Verified with two corrections: the Cedar gate and judge are declarative
config, not code (policies/permit_send.cedar; agentcore.json evaluators
blocks), and the orchestrator-to-specialist "brief" is unstructured LLM prose
over a2a_send_message.

Ranked steals:

1. **Cedar's shape for the approval-policy layer** (permit_send.cedar;
   ARCHITECTURE.md s7): default-deny, permit conditions written over
   STRUCTURED tool input, action vocabulary generated from registered schemas
   so unknown actions fail at authoring time ("targets before policies"),
   ENFORCE vs LOG_ONLY engine modes, machine-readable denial before the
   backend runs. crivo: policy = permit keyed on disease ids from the
   22-check taxonomy (grade, row-delta in the when-clause, dataset
   fingerprint as resource); validate at admission; ship every new policy in
   LOG_ONLY shadow for a week ("would have auto-batched these 14 fixes") as
   the arming UX; encode person-grade-never-auto as a forbid no permit can
   override. Medium effort (small hand-written evaluator, no Cedar dep),
   exact fit.
2. **Same rule in three places**: prompt advises, boundary enforces, judge
   measures (recipients rule appears in both agents' prompts, the Cedar
   policy, and the judge rubric). crivo: gate rules also worded into the
   planner prompt and mirrored into a judge rubric, yielding a "model
   proposed what the gate would deny" drift rate.
3. **Remove the capability instead of instructing against it**
   (identity.py select_a2a_tools drops discovery so fabricated agent URLs
   are impossible, with a regression test). crivo: fan-out workers get a
   kernel surface with no write primitives; fixers get exactly one fixer.
4. **Declarative judge with bands, sampling, CI gate, never blocking**
   (MailoPolicyEvaluator: rubric with explicit score bands, 25% session
   sampling, CI fails under 0.7 aggregate, judge blocks nothing at runtime).
   crivo: judge answer-card prose into telemetry at 10-25%, CI over replayed
   sessions, monitor only (the owner's own brief cites judge-gated curation
   failing silently).
5. **Memory namespacing + retrieval budget + short-circuit**
   (/users/{actor}/preferences|facts from validated JWT only; top_k=3,
   relevance 0.2; "if user_context answers, respond immediately, do not
   delegate"; trailing-slash namespaces against prefix collisions; 30-day
   expiry backstop). crivo: /datasets/{fingerprint}/episodic|semantic|
   procedural; hard top-k budget; and the money move: a promoted skill
   matching fingerprint+finding routes straight to the deterministic fixer
   with NO planning call.
6. **Trajectory-vs-assertions as a named failure mode** (output quality and
   tool trajectory measured independently). crivo: diff executed steps
   against the approved plan; a right answer off-plan is a telemetry flag.
7. **Capability card + one owned dangerous tool per specialist; progress
   stream + one final named artifact; cancel flag checked between events.**
   crivo: worker manifests the planner selects against; answer-card emission
   shape.
8. **Degraded-mode rule: fall back only if nothing emitted yet** (the
   `streamed` flag; born from a real MCP client-scoping bug fixed by running
   the turn inside `with client:`). crivo: routing escalation fine before
   output exists; after partial output, fail loudly and replan; scope kernel
   sessions with context managers.
9. **Security decisions as pure, dependency-light, regression-tested
   modules** (identity.py + the IDOR test proving the request body cannot
   influence identity). crivo: grade assignment and policy evaluation as pure
   functions with tests like "a model self-report can never raise a grade"
   and "person-grade never matches any batch policy."
10. **Scope minting**: a standing policy approval mints a scoped object (id,
    matched types, expiry, approver) the gate verifies per batched action;
    scope always from a verified artifact, never the model's claim.
11. **Telemetry hardening**: one run id on every hop including workers and
    judge verdicts; the policy decision recorded on the fix event; cap batch
    and field sizes (GenAI spans blow 1MiB OTLP limits silently).
12. **One flat validated governance config** so the whole posture is
    reviewable in a single diff.

Anti-patterns: free-text inter-agent task briefs (no schema, no acceptance
criteria: crivo's plan steps must be structured objects); framework-supplied
orchestration doing the interesting parts (the owner's own master plan flags
this as mailo's weakness); un-gated async memory extraction; bare denial
errors (denials should be structured, replannable events); fresh-everything
per request (scope freshness to identity-like state, never the kernel);
validation escape hatches left on; 25% judge sampling as default cost.

## Part 3: resurfaced from the owner's own coop-project crivo briefs

- The 22-disease taxonomy already carries an AUTO/GATE/HUMAN autonomy column:
  the approval-policy layer's action vocabulary and validation schema already
  exist (build-research Part 2).
- **The intent gate** (brief item 5): one extra call restating what executed
  code actually computed, diffed against the question, blocking on mismatch.
  Catches "correct code, wrong question," which assertions cannot see. Not
  yet on any roadmap: candidate addition beside the answer card.
- Provenance log doubles as crash recovery: rehydrate a restarted kernel by
  replaying the log (gotcha 3). Trust layer = durability layer.
- Variable registry piggybacked on user_expressions; iopub must be filtered
  by parent msg_id or outputs mis-attribute.
- Recursion hard-capped at depth 1, sub-calls opt-in (two 2026 reproductions
  show depth 2+ degrades simple tasks): fan-out stays flat, no worker spawns
  workers.
- No debate/critic loops on fixes (measured harm -1.6 to -15.5pp): no
  reviewer-agent step inside plan-first execution.
- Skill retrieval settled: model2vec potion-base-8M + numpy dot product;
  exact search beats vector DBs under 1,000 skills. SKILL.md portability
  needs exactly the six spec frontmatter fields.
- Demo assets chosen: Raha pairs for fix precision/recall; Vancouver property
  tax 21 yearly slices as the skill-compounding demo; BC GWELLS lithology
  150k strings as the showpiece; governance-off ablation planned.
- Positioning: contrast with EvoDS ("the governed, test-gated,
  provenance-backed version of skill compounding"); never lead with
  benchmarks.
- Open seam: a future voice agent connecting to crivo over MCP (master plan
  q10, deferred).
