# Roadmap update: launch gate + Phases 6-8 (draft, awaiting sign-off)

Status: DRAFT. Supplements `2026-08-15-master-roadmap.md` (Phases 1-5). Folds in
the 2026-08-31 landscape research (three reports: chat-with-data products,
data-quality tooling, agent infrastructure) and Aarmen's direction from the
brutal-honesty review: users after cleanup, a mass-eval phase, testing on other
devices, agent memory, harder data, and a properly planned test.

## What the research settled

1. **The moat is confirmed and specific.** Every "verified answers" scheme in
   the market (Databricks Genie trusted assets, Snowflake Verified Query
   Repository, Vanna golden SQLs, Hex endorsed models) verifies *inputs* before
   generation. No mainstream or academic tool applies a data fix, re-runs the
   detector that found the problem, and reverts on failure. Closest prior art
   to cite honestly: dbt (re-test on rebuild), Monte Carlo troubleshooting
   agent (executes queries to verify diagnoses, not fixes), AlphaClean 2019
   (quality-objective search, no assertion-gated revert). Cleanlab TLM referees
   agents via uncertainty scoring, not executed assertions.
2. **The benchmark has an exact protocol to follow.** The Raha/Baran line:
   datasets Hospital, Flights, Beers, Rayyan (plus Movies, IT, Tax), cell-level
   precision/recall/F1, stated labeling budget, mean of 10+ runs, end-to-end
   detect-then-repair numbers reported separately from per-stage numbers.
   Reference bars at 20 labeled tuples: Raha detection F1 0.72-0.99 by
   dataset; end-to-end Raha+Baran F1 0.35-0.90. Our headline claim: end-to-end
   F1 at **0 labels**, plus the number nobody else can report - the fraction
   of applied fixes that survived verification vs. reverted.
3. **The MCP ground moved under us.** Spec 2026-07-28 is stateless, deprecates
   sampling, and replaces server-initiated elicitation with Multi Round-Trip
   Requests (`input_required` / `inputRequests`). Long jobs belong in the
   `io.modelcontextprotocol/tasks` extension. Our server targets the older
   pattern and needs porting. Two open spec discussions (#2964 verification
   metadata on tool results, #2574 verification capability) ask for exactly
   the shape our answer cards already have - the referee positioning is
   unclaimed but the window is open.
4. **Skills have an open standard now.** Anthropic's Agent Skills format
   (agentskills.io, ~35 adopters incl. Codex, Cursor, Copilot). Our SKILL.md
   folders are close; conformance makes every verified fix portable and is a
   distribution channel. Our test-gated, human-admitted governance is a
   differentiator there (skill supply chains are a live security worry).
5. **Table stakes we lack** in the chat-with-data category: charts on answers,
   suggested questions after load, any-OpenAI-compatible/Ollama model support,
   an exportable session report.

## Launch gate (before any users; finite by design)

Done means these four, not a feeling:

- [ ] Repo hygiene: hero/terminal SVGs retired, `.DS_Store` ignored and
      untracked, `pandera` dependency dropped, merged branches pruned.
- [ ] License chosen (recommendation: MIT) and committed.
- [ ] CI: GitHub Actions running the keyless suite on Linux/macOS/Windows,
      Python 3.12/3.13; the README badge becomes real. This is the cheap form
      of "testing on other devices."
- [ ] Name decided (verified free on PyPI + GitHub before adoption).

Plus one loud sentence in the MCP listing about the `--network=none` execution
story - security posture is now an adoption gate for MCP servers.

## Phase 3 completion (unchanged, plus table stakes)

`aa.ask` (wrapper over the hand-written loop; loop semantics are Aarmen's),
charts on answers, provider seam (OpenAI-compatible endpoints incl. Ollama and
OpenRouter), and two cheap adds from research: 3-5 suggested questions after
`diagnose()` (the profile is already in hand), and a plain-English paragraph on
the answer card explaining what the code and assertions did (Genie does this to
build non-technical trust).

## Phase 6 - Proving Ground (the mass-prompt phase)

Not model fine-tuning. A harness that trains the *system* by measured
iteration: detectors, prompts, and skills against mass cases.

- Synthetic corpus: thousands of generated messy datasets with injected,
  therefore known, corruption. Property-based generation (hypothesis) so
  invariants are asserted across the space, not just examples.
- External corpora: the Raha datasets above, so we are scored against dirt we
  did not design.
- Mass question suites over cleaned data (Genie ships per-space suites of up
  to 500 gold-standard questions; same idea, ours replayable in CI).
- Agent-half evals in Inspect AI (the open standard; no data-cleaning eval
  exists in it yet - publishing one is both validation and distribution).
- Published numbers per the protocol in "What the research settled" #2.

Acceptance: a `bench/` (or `evals/`) suite runnable headlessly; per-detector
P/R/F1 and end-to-end F1 published in the README; survived-verification rate
reported; failures triaged into detector/prompt/skill fixes with the suite as
the regression net. Gets a full WRAP spec before code.

## Phase 7 - Harder Data (scored by Phase 6, never before it)

- Cross-column: functional dependencies (zip determines city), unit mixing,
  temporal consistency (end before start).
- Multi-table: foreign-key orphans, join-aware dedupe. Real (finance) data is
  relational; today's checks are single-table except drift.
- Raha-style ensemble detection as the ML-assisted upgrade path: many cheap
  detector configs vote per cell, ~20 user labels propagate. Composes with
  the 22-check list as the strategy pool; graded GATE, never AUTO.
- Severity tiers on every check (warn/fail thresholds, pointblank-style)
  instead of binary firing.
- `aa.compare(df_then, df_now)`: drift with auto-selected statistics
  (Evidently's recipe: KS/chi-squared small-N, Wasserstein/PSI large-N).
- `aa.reconcile(df_a, df_b)`: keyed value-level diff with mismatch report.
  OSS data-diff was archived May 2024; the gap is real and exactly our shape
  (diff + assertions + provenance).

## Phase 8 - Agent Memory (three memories, one ledger)

- **Durable provenance ledger**: receipts written to disk, surviving the
  session; auditable later. Emit OpenLineage RunEvents with the
  ColumnLineage and DataQualityAssertions facets so any lineage backend can
  consume our trail without us running a server.
- **Per-dataset memory**: a contract/semantics file emitted after CLEAN
  (ODCS-style YAML: schema + meanings + quality expectations). Future runs
  verify against it and flag breaking changes; QUERY mode reads it for
  context. Prior art: PandasAI semantic layer, Snowflake distilling verified
  queries into general model improvements. Mergeable whylogs-style column
  sketches stored per run give drift-over-time with no SaaS.
- **Skills as the third memory** (exists): add Agent Skills conformance,
  one-click promote-from-answer-card (Vanna's loop), a visible
  "answered via verified skill X" badge (Genie's trusted label), and session
  export as a replayable script whose assertions make the re-run safe
  (Julius has replay; nobody has safe replay).

## Near-term maintenance workstream (parallel, small)

- Port `mcp_server.py` to MCP 2026-07-28: stateless, MRTR-style input
  requests for gates, tasks extension for long cleans, deterministic
  `tools/list` with cache hints. Gate *policy* is unchanged and stays
  change-controlled; this is transport. Proposed as diffs.
- Official MCP Registry listing (`server.json`, namespace verification);
  aggregators federate from it.
- Draft a position on MCP discussions #2964/#2574: our answer card as
  verification metadata. First-mover claim on the correctness-referee slot.

## Ordering and the one rule

Launch gate -> finish Phase 3 -> Phase 6 -> Phase 7 -> Phase 8, with the MCP
workstream running alongside early. The rule that orders everything: no new
capability ships ahead of the instrument that measures it. Phase 6 before
Phase 7 is the whole point.
