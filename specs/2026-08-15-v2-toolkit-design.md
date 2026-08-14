# v2 toolkit — verified hands for data, for any agent (design)

Design only; nothing here is landed. v1 wraps the whole agent: the inner model
writes fixes, so an orchestrating agent pays for two models and trusts a
pipeline it cannot see into. v2 exposes the verification machinery itself as
MCP primitives: the **outer** model — whatever agent is connected — authors
the fixes, and analyst-agent becomes the thing that keeps any agent honest:
sandboxed execution, detector re-runs, hash invariants, revert-on-failure,
reference repairs, governed skills. One model instead of two, and the part of
this project nobody else ships becomes the product.

## What (WRAP)

A second tier of MCP tools on the **same server** as v1 (recommendation over
a separate server: one config entry, one process, shared session machinery,
and the P5 rejection of daemons stays intact — a client discovers the tier
via tools/list, not a second install).

## Tool inventory (contracts)

- R1. `open_frame(path) -> {toolkit_id, variable, profile}` — loads via the
  one loader, takes the baseline snapshot (rows, per-column hashes, revert
  copy) immediately. The profile is the same schema/stats/truncated-samples
  block the inner model sees. Idle eviction as v1.
- R2. `get_findings(toolkit_id) -> {findings, indicators, clear, broken}` —
  `detect_all`, with stable ids (`f1`, `f2`, …) added so fixes can cite their
  target. Absence stays a checked claim: `clear` and `broken` ship every time.
- R3. `submit_fix(toolkit_id, finding_id, fix_code, human_approved=False) ->
  verdict` — the centerpiece. Order: (a) the AST screen from R3/preview —
  now load-bearing, not advisory: imports beyond the dataframe tier,
  open/exec/eval, dunders are refused before anything runs; (b) grade check:
  GATE/HUMAN findings are refused unless `human_approved=True`, whose
  docstring carries the same contract as `policy="all"` — the calling agent
  passes it only when relaying explicit human consent; (c) execute in the
  kernel; (d) full layer-1 verification — detector re-run (a crash returns
  verdict `uncheckable`, never `failed`: attribution survives), row
  invariant, untouched-column hashes, the d06 reference-repair anchor;
  (e) revert on any failure. Returns `{verdict: verified|failed|uncheckable,
  evidence, reverted}` with the failing check verbatim, so the outer model
  can retry with information. The model's own asserts live inside
  `fix_code`, authored by the outer model — same contract the inner model
  has always had.
- R4. `get_report(toolkit_id)` — the same CleanReport, fix records carrying
  `origin: "external"` (vs `"model"` / `"skill:…"`), so provenance and the
  per-column rollup never blur who authored what.
- R5. `save_cleaned(toolkit_id) -> {parquet, lineage}` — persistence is an
  explicit act by the caller, not a side effect of the last fix.
- R6. Snapshots (R8 machinery) run automatically after each verified fix;
  they are not tools — the outer model has no business driving durability.

## The samples fork **[decision]**

Does the outer model ever see raw rows? Option 1: profile-only — findings
evidence already carries the truncated samples the inner model fixes from,
and the inner model is the existence proof that fixes are writable from that
information alone. Option 2: a capped `get_sample(column, n<=10)` — more
capable outer fixes, but it breaks the one discipline this project has never
broken, and for local clients it protects nothing (they can read the file
themselves) while creating a real leak surface for `load_url` data.
**Recommendation: Option 1.** Reopen only on dogfooding evidence that outer
models actually fail to author fixes from evidence strings.

## Skills from external fixes **[decision]**

Governance is never unattended, and over MCP the human is on the far side of
a client we do not control. Option A: external fixes never become skills
(simple, sterile — the compounding claim stops at the boundary). Option B: a
**pending-admission queue**: a verified external fix can be generalised and
frozen exactly as today, but lands in `skills/pending/` and is admitted only
by a human in the REPL (`/skills pending`), asynchronously. Option C: MCP
elicitation at admission time (blocks on client support; v2.1 at earliest).
**Recommendation: B** — the frozen-case gate and human yes survive intact,
only the *when* moves. This touches the skill lifecycle, so it is Aarmen's
call before any of it is built.

## Deliberately rejected

- A "just apply it" tool that skips verification — the entire point inverted.
- Raw-row access in any tool (see the samples fork).
- Parallel fix submission — one kernel, order-sensitive; a queue would fake
  a concurrency the substrate does not have.
- Unattended skill admission, daemons, multi-tenancy, hosting: unchanged
  from P5's rejections.

## Acceptance criteria

- AC1. `submit_fix` on a planted d04 verifies; the report carries
  `origin: "external"` and the per-column rollup counts it.
- AC2. The word-splitting d06 repair (`Bud<ZWSP>weiser` → `Bud weiser`)
  submitted externally is **reverted** by the reference anchor — our rails
  catching an outer model's corruption is the demo of the whole thesis.
- AC3. A detector crash during verification returns `uncheckable`, not
  `failed`, and the frame is reverted either way.
- AC4. A GATE-grade finding is refused without `human_approved=True`.
- AC5. No toolkit tool returns more than SAMPLE_LIMIT values of any column.
- AC6. Every v1 tool behaves identically with the toolkit tier present.

## Estimate, honestly

About a week: a ToolkitSession that reuses Session's kernel/baseline/verify
seams minus the inner-model paths (~2 days), six tools plus registration
(~1 day), tests to the AC list including real-kernel runs (~2 days), pending
queue plumbing if approved (~1 day).

## What v1 evidence would change this design

Collect from dogfooding before building: (1) do connected models write
working fixes from evidence-only prompts, or reach for the file? — reopens
the samples fork; (2) do real sessions use the stateful tools at all, or
only one-shots? — if one-shots dominate, v2 shrinks to `verify_fix(path,
finding, code)` stateless and gets cheaper; (3) does the client population
support elicitation? — moves the gate from consent-by-parameter to
consent-by-protocol.

## Ownership

This spec is a proposal. The two **[decision]** forks (samples, pending
admissions) plus the `human_approved` gate semantics touch the trust model
and skill lifecycle: Aarmen's, propose-diffs only. The ToolkitSession, tools,
and tests are Claude plumbing over seams that already exist.
