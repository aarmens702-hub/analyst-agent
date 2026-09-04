# Research: agent-system gaps, September 2026

Produced by a web-research agent on 2026-09-04 as input to
`specs/2026-09-04-agent-system-roadmap.md`. Ranked gap report on how crivo's
agent SYSTEM (planning, latency, memory, approval, observability) compares to
the 2026 state of the art. Every claim carries a source.

Framing: crivo's verification loop and gate discipline are ahead of most of
the field (Anthropic's January 2026 evals guidance explicitly prefers
deterministic outcome checks, which crivo already has; Databricks shipped
"Inspect" in 2026 to retrofit exactly the verify-the-generated-query behavior
crivo was built on). The gaps are almost entirely in the layer around the
loop: latency and cost engineering, run visibility, approval ergonomics, and
external anchoring. The measured facts (95% of wall time is model latency, the
ceiling arm bought zero extra repairs) point the roadmap at human time and
wall-clock time, not at loosening the loop.

## 1. Streaming output plus mid-run interrupt and steer [NET-NEW]

Stream model tokens as they generate, let the user stop a call in flight
(keeping completed work), and let them type a steer note picked up at the next
step boundary. Table stakes in 2026: Claude Code has single-Esc
stop-and-redirect and queued mid-run steering; LangGraph's `interrupt()` and
the OpenAI Agents SDK's serializable RunState treat pause/steer/resume as
first-class; AWS's Agentic AI Lens lists streaming and time-to-first-token as
a named best practice. For crivo, at ~107s per model call, a non-streaming UI
is up to two minutes of dead air per step. Steer-at-next-gate is a natural
fit: crivo already stops at every cell. Size: days. Tension: none, it
strengthens the gates.
Sources: code.claude.com/docs/en/how-claude-code-works,
openai.github.io/openai-agents-python/human_in_the_loop/,
AWS Agentic AI Lens AGENTPERF02-BP04.

## 2. Cache-aware prompt architecture and call-count discipline [NET-NEW]

Restructure prompts so the provider's prefix cache hits (stable system prompt
and tool list first, volatile schema/stats after the last stable byte, no
timestamps early) and instrument cache-hit tokens per call. DeepSeek caches
prefixes automatically; off-peak v4-pro cache hits are $0.022/M vs $0.66/M on
a miss, roughly 30x. Deriv reported an 85.8% hit rate cutting agent input
costs 77% purely by ordering prompt components by variability. Second lever:
collapse fix + verification-cell proposal into one call where possible.
Constrains plan and memory features to append-only history; write that into
their specs now. Size: days; the discipline is permanent.
Sources: deepseek-usa.ai/docs/deepseek-context-caching/, Deriv case study
(derivai.substack.com), "Don't Break the Cache" arXiv 2601.06007, Anthropic
prompt-caching docs.

## 3. Verified small-model routing per finding [NET-NEW]

Route easy findings to a small fast model (deepseek-v4-flash class), hard ones
to the big one, with a cheap deterministic router (by check type and finding
grade), never an LLM router. crivo's deterministic re-check converts
cheap-model risk into one bounded retry: if the check still fires, escalate
that finding to the big model. RouteLLM-style results (85%+ cost cut at 95%
quality) never had a hard verifier; crivo does. The only lever that attacks
the 107s/call number itself. Size: about a week including a bench arm.
Tension: splits the cache namespace across two models; measure both arms.
Sources: neuraltrust.ai/blog/llm-model-routing, arXiv 2605.06350 (cascade
decision theory), cometapi routing guide 2026.

## 4. Approval policy layer and fatigue instrumentation [REFINES PLAN]

2026 HITL findings: per-action gates decay into rubber stamps (flat ~2s
approval latency, near-zero rejection are the tells); attackers engineer
approval fatigue; remedies are risk-tiered gating, coherent-unit batching
(approve a plan, not each atom), mechanical filtering before human review,
and instrumented oversight (decision latency, edit rate, reversal rate as
product metrics). crivo already has risk tiers and mechanical filtering.
Missing: (a) a persistent policy object ("safe-grade findings of check types
X,Y with passing re-checks may batch under one approval"), (b) gate
instrumentation, (c) plan approval folded in as the coherent-unit tier.
Hard rule to write as code: auto-approval may only ever cover safe-grade
actions with passing deterministic re-checks; person-grade calls and skill
admissions stay human forever. Size: about a week.
Sources: tianpan.co approval-fatigue post 2026-06-25, WorkOS
approval-fatigue-agent-governance, Claude Code permissions model, OpenAI
Agents SDK needs_approval predicates.

## 5. Run telemetry on the OpenTelemetry GenAI conventions [NET-NEW]

Structured traces per run: spans per model call, cell, check, gate, with
tokens, cost, latency, cache-hit tokens, model used, outcome. OTel GenAI
semantic conventions became the de facto standard in 2026. No framework
needed: a JSONL span writer using the conventions' attribute names keeps the
no-frameworks policy and stays exportable. Every other gap needs its numbers
(cache verification, routing arbitration, fatigue metrics, cost-per-task).
Shares IDs with the provenance DAG so /why answers in time as well as
lineage. Size: about a week. Tension: none.
Sources: opentelemetry.io/blog/2026/genai-observability/, Greptime OTel GenAI
conventions post, Fiddler OTel guide.

## 6. External benchmark anchoring, pass^k, cost-per-task [REFINES PLAN]

Add: an external benchmark for validity (DABstep, 450+ real multi-step data
analysis tasks, frontier agents ~16% at publication; DSBench 540 tasks), a
consistency metric (pass^k: solves on all k runs, from tau-bench), and money
(per-task dollars and wall-clock from telemetry). Adopt abstention cases
(correct answer is "the data cannot answer this", graded on declining).
n=12 cannot catch regressions under ~8 points and is self-authored. Size:
days for pass^k and cost columns; a week for a DABstep adapter.
Sources: arXiv 2506.23719 (DABstep), Adyen writeup, DSBench, prefactor.tech
pass^k explainer, Anthropic "Demystifying evals for AI agents".

## 7. Plan repair inside the plan-first spec [REFINES PLAN]

2026 consensus is plan-execute-replan. Add to the plan-first spec: explicit
replan triggers (a step fails, or a fix invalidates a later finding, which is
CLEAN's normal case), plan versioning with diffs, the model may only flip
step status rather than rewrite scope, and replans present as small diffs at
the next gate. A fixed approved plan is also a control-flow-integrity
boundary (a security bonus). Avoid tree/beam search over fix sequences: the
cost multiplier is ruinous at 107s/call and the ceiling arm's zero-extra-
repairs result says there is no headroom. Keep in reserve: verifier-gated
best-of-N on a single hard finding after a first failed fix. Size: days, if
specced before implementation.
Sources: LangChain planning-agents lineage, futureagi 2026 pattern survey,
arXiv 2509.08646 (secure plan-then-execute), Anthropic effective-harnesses
post.

## 8. Memory hardening for "three memories, one ledger" [REFINES PLAN]

The planned taxonomy maps onto the 2026 standard (episodic/semantic/
procedural; skills are already a well-governed procedural memory). Absorb:
(a) most production memory failures happen at write time, so gate and verify
writes like fixes (Mem0's ADD/UPDATE/DELETE/NOOP step); (b) typed memories
with provenance: source session, dataset fingerprint, timestamp; retrieval
filtered by fingerprint validity (Zep's bi-temporal model; arXiv 2605.25869
on provenance-role collapse); (c) contamination controls: person-grade
findings and raw-row-adjacent content are unwritable to memory (MemGuard
threat model); (d) human-legible storage: a plain-markdown index plus topic
files, conservative writes, user-editable. Size: about a week of deltas on
the planned work.
Sources: mem0.ai state-of-memory 2026, MemGuard arXiv 2605.28009, arXiv
2605.25869, Claude Code auto-memory mechanics.

## 9. Fan-out guardrails [REFINES PLAN, mostly critique]

Anthropic's multi-agent research system beat single-agent by 90.2% on
parallelizable research at ~15x tokens; "The Illusion of Multi-Agent
Advantage" (arXiv 2606.13003) showed matched-compute single agents often
equal multi-agent, with real wins only on decomposable tasks with explicit
error checking. Consensus: one orchestrator, isolated workers, workers return
summaries not transcripts, workers never act on shared state. For crivo:
fan out per-column profiling and diagnosis (read-only, genuinely
decomposable, wall-clock win because model latency dominates); never fan out
fixes; workers propose findings, only the main loop proposes cells; cap
concurrency; put the token multiplier in the bench. Size: spec discipline
plus one bench arm.
Sources: Anthropic multi-agent numbers, LangChain how-and-when multi-agent,
arXiv 2606.13003.

## 10. Speculative drafting during gate waits [NET-NEW, experimental]

While the human reads a proposed cell, the model drafts the next step under
the assumption of approval; on approve the next proposal appears instantly;
on reject the draft is discarded. Nothing executes speculatively, so no gate
is bypassed. Only worth building if fatigue metrics show high approval rates;
route drafts to the small model to cap discard cost. Size: about a week,
after gaps 1 and 5. Sources: PASTE arXiv 2603.18897, IdleSpec arXiv
2605.22154, arXiv 2509.01920.

## 11. MCP port alignment: Tasks extension and MRTR elicitation [REFINES PLAN]

The 2026-07-28 spec's two mechanisms are crivo's shape: human gates over MCP
become MRTR elicitation (server returns input_required carrying the proposed
cell and grade; client retries with answers), and a CLEAN run is a textbook
Task (long-running, progress, resumable). Building the port on those two
extensions makes crivo's gates legible to any 2026 MCP client. Size: days,
folded into the planned port.
Sources: blog.modelcontextprotocol.io 2026-07-28 post, stacktr.ee breaking
changes rundown, AWS AgentCore Gateway notes.

## 12. Checkpoint scope beyond the kernel [REFINES PLAN, small]

Kernel snapshots plus per-fix revert cover today. The hole opens when scoped
workspace file ops land: written files sit outside snapshot coverage. Version
the scoped workspace per gate (content-addressed copy or git-in-workspace).
Do not adopt a durable-execution platform. Size: days, with the file-ops
milestone. Sources: Claude Code checkpoints docs, durable-execution survey
2026.

## Explicitly not recommended

Tree/beam search over fix plans; LLM-as-judge as primary grader (keep a
calibrated judge only for answer-card prose); durable-execution platforms and
agent frameworks; LLM-based cascade routers invoked per call.

## Top-5 shortlist

1. Streaming plus interrupt/steer (days, transforms the product's feel)
2. Run telemetry on OTel GenAI conventions (everything else needs its numbers)
3. Cache-aware prompt architecture (near-free 30x on cached input)
4. Approval policy layer with fatigue instrumentation (ceiling written as code)
5. Verified small-model routing (the only direct attack on the 107s/call)

Plan repair (7) and fan-out guardrails (9) are spec-time refinements to
already-planned work: fold them in now at near-zero cost.
