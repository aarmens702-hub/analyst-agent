# The master plan: one roadmap, every wave

2026-09-06. **Status: the single authoritative plan.** Everything previously
scattered across eight specs is consolidated here in build order. Where an
item has a design elsewhere, this plan points at it rather than restating it.

**This supersedes, as a sequencing authority:**
`2026-08-15-master-roadmap.md` (phases 1 to 8),
`2026-08-31-phase6-plus-roadmap.md` (launch gate, phases 6 to 8),
`2026-09-04-agent-system-roadmap.md` (A0 to A4),
`2026-09-04-capability-roadmap.md` (B0 to B5),
`2026-09-06-review-triage-and-fix-plan.md` (P0 to P7 findings).
Those remain the detail of record for their own items. Read them for how, read
this for when.

**Owner decision that shapes everything below (2026-09-06): no users yet.**
Correctness over adoption, even if it takes months. Distribution therefore
sits at the end, not the start.

## Where we are

| | |
|---|---|
| Detection micro-F1 | **0.754**, unchanged for three days |
| Repair F1 | 0.913 (was 0.980 before fixers learned to refuse) |
| Survived verification | 0.673 (was a misleading 1.000) |
| Bench corpus | 29 synthetic + 4 external. Phase 6 asks for thousands |
| Test suite | 860 passing |
| Taxonomy | 26 disease ids, 8 with registered deterministic fixers |
| Public API | 17 exports |
| Review findings | 56 confirmed, 20 closed, 36 open, plus 2 surfaced during repair |
| Installable | No. PyPI unpublished |

Honest summary: crivo is a careful data-quality tool whose verification is now
truthful. It is not yet an analyst, and its accuracy has not moved because the
instrument that would move it was never built.

## The two rules that order this plan

1. **No new capability ships ahead of the instrument that measures it.**
   Inherited from the master roadmap, and we broke it: Phase 7 detectors were
   built while Phase 6 sat at 33 datasets. Waves 3 and 6 repair that ordering.
2. **Core changes go through owner-reviewed diffs.** `loop.py`, `prompts.py`,
   `skills.py`, `provenance.py` and `detect.py` are drafted as small reviewed
   packets, never landed unattended.

A third rule earned during the code review, now standing practice: **every
packet gets an adversarial pass whose job is to break the fix.** It got through
on all five P0 fixes and all four P2 fixes. A fix nobody attacked is not done.

## Wave 0. Autonomy mode (in flight)

Five packets from `2026-09-05-autonomy-default-build-packets.md`: governance
key, default policy factory, loop routing, provenance, bench arm. Autonomous
becomes the default posture; `careful` becomes opt-in; `report-only` names the
look-but-do-not-touch level.

Acceptance: a GATE or HUMAN finding is never applied unattended, skill
admission still requires a person, and the smoke numbers do not move, because
this changes who approves a fix and not which fixes are correct.

## Wave 1. Fix the scorer (P5, 6 items)

First, because until the instrument is honest nothing measured after it can be
trusted, and because it is cheap.

- `score.py:229` recall is inflated: true positives counted over findings,
  false negatives over truth corruptions, so the two do not sum.
- `run.py:79` survival drops datasets where crivo attempted nothing and
  publishes no denominator.
- `score_fixes.py:39` rounds to 12 significant digits, so wrong digits in a
  long id score as a perfect repair.
- `agent_run.py:299` arms collide on disk; a batched arm resumes from the
  baseline's files and reports its numbers.
- `agent_run.py:151` a paid run reports zero tokens when no span is found.
- `score.py:63` the bool guard misses `np.bool_`.

Acceptance: every metric recomputed on the same corpus, with each movement
explained. Expect the numbers to move; they were wrong.

## Wave 2. Honesty and safety (P1 + P3 + P4, 15 items)

Independent of any corpus, so they can be done now.

- **P1 gates (4):** rejecting a plan leaves earlier batching armed; the bench
  driver auto-runs GATE judgement calls; a HUMAN finding is dropped when an
  AUTO one shares its column; verification reads kernel state a model cell can
  rewrite.
- **P3 false receipts (6):** asserts stamped passed without executing;
  receipts that cannot be false; vacuous bench invariants stamped "ok".
- **P4 PII leaks (5):** raw personal data in the shareable HTML, the notebook
  card and the reconcile report, on pages claiming it never travels; the PII
  scan silently skips duplicate-named columns.

Acceptance: no output surface carries a raw cell value; no receipt can report
success without a check that could have failed.

## Wave 3. Phase 6, the Proving Ground

**The instrument. The most important wave in this plan.**

- Property-based synthetic corpus: thousands of generated messy datasets with
  injected, therefore known, corruption, via hypothesis so invariants hold
  across the space and not just examples.
- External corpora kept and extended beyond the current four Raha sets.
- Per-detector precision, recall and F1 published, not just the aggregate.
- Survived-verification reported with its denominator.

Deferred within Phase 6 as the expensive half: mass question suites and
Inspect AI agent evals. The corpus alone unlocks waves 4 and 5.

Acceptance: a headless suite scoring thousands of cases, per-detector numbers
in the README, and a failure triage path that turns a miss into a fixture.

## Wave 4. Detector honesty (P6 + P7 + the 2 from P2, 17 items)

Deliberately after the corpus, because most of these change grades, and
changing grades without measuring is how AUTO became over-assigned.

- Silent caps reported as checked absence (d12 and siblings).
- AUTO grades on genuinely ambiguous values (sentinel dates, the word "none",
  near-empty columns called empty).
- NaN counted as out of range on coordinate columns.
- The two from P2: a model-authored fix can still corrupt its own target
  column and verify; `detect.py` grades AUTO on findings `autoclean.py` now
  always refuses.
- P7 correctness: plan expiry compares a local date against a UTC one, so a
  test fails only after 5pm Pacific; plus reader, skill and CLI bugs.

## Wave 5. Detector accuracy

The first wave whose goal is the number itself. Only possible after 3 and 4.

Target: move detection micro-F1 off 0.754 and raise survived-verification
honestly, by fixing misses the corpus exposes rather than by tuning thresholds.

## Wave 6. Phase 7 detectors (8 packets)

From `2026-09-05-p7-substrate-and-detectors-packets.md`, now correctly ordered
after the instrument that scores them.

- Substrate: column-role model, time-axis selector, table-relation model.
- Temporal detector (new disease id 27), evidence-only.
- Foreign-key integrity detector (new disease id 28), family-only.
- Accessors and family wiring.

## Wave 7. Cross-cutting (4 packets)

From `2026-09-05-p7-cross-cutting-packets.md`: a severity tier on every
finding, orthogonal to the AUTO/GATE/HUMAN grade, so reports sort by impact;
and the reconcile render surfacing its excluded key sets.

## Wave 8. B1.1, the analyst's brain

**The identity bet.** A curated deterministic statistics toolkit the model
calls: t-test, chi-squared, Mann-Whitney, ANOVA, confidence intervals, effect
sizes, correlation with significance. Automatic test selection justified by
assumption checks that ship on the card. Never model-written statistics,
because subtle statistical errors pass code review.

Placed here, not earlier, so it arrives measurable. Without it there is no
analyst in "AI analyst first".

## Wave 9. Data in, at scale

- **B2.2** DuckDB backend for the checks, larger-than-memory data, and
  s3/gcs/http paths via fsspec. Headline: keyless checks over a 5GB parquet
  folder on a laptop, and the first step toward warehouse pushdown.
- **B2.3** Google Sheets ingestion.

## Wave 10. Analytics depth

- **B4.4** Time-series analytics: STL decomposition, seasonal-naive and
  statsforecast baselines, forecasts carrying rolling-backtest receipts.
- **B4.3** Record linkage: optional Splink-backed keyless linkage with match
  probabilities as receipts, on top of the keyed reconcile already shipped.
- **B4.5** Free-text column categorization: cluster-then-label with a
  human-gated taxonomy and row-cited evidence.

## Wave 11. Output polish

- **B3.1** Interactive embedded graphs (vega-lite or plotly JSON,
  self-contained, no server), and PNG/SVG export for answer-card charts.
- **B3.2** Marimo `.py` export beside the existing `.ipynb`.
- **B3.3** Metric-history anomaly detection (PSI, JS divergence) folded into
  the compare report.

## Wave 12. Contracts and interop

- **B5.1** ODCS v3.1 read and emit; promote findings to a declarative
  contract, bridging keyless discovery to CI enforcement.
- **B5.2** OpenLineage column-level events for applied fixes.
- **B5.3** Scheduled-runs recipe: docs plus a webhook-on-failure flag, not a
  scheduler.
- **A4.1** MCP port on the MRTR spec, expressing gates as elicitation carrying
  the proposed cell and grade, and CLEAN runs as Tasks. Unblocks registry
  listings.
- **A4.2** Workspace versioning per gate, so revert stays true once fixes
  write files.
- **A4.3** Remaining bench columns: cost per task, wall-clock, and abstention
  cases graded on declining.
- **A3.3** Verified-answers memory: recurring questions pin to approved named
  code through the existing skills mechanism, with the card saying when a
  saved recipe answered. The one memory item that needs no ledger.

## Wave 13. Latency

- **A2.2** Verified small-model routing: mechanical fixes to a flash-class
  model, hard and multi-column to pro, a still-firing check escalating that
  finding to pro. Bench arm before default-on.
- **A2.3** Speculative drafting during gate waits, experimental, and only if
  approval rates justify it. Drafts never touch the kernel.

Exit: median wall-clock per case halved against the 2026-09-04 baseline.

## Wave 14. Ship it

- Launch gate remainder: CI on Windows and Python 3.13 alongside the current
  Linux and macOS on 3.12; name verified free on PyPI and GitHub.
- Publish 0.1.0. Requires the owner's token and the repo rename.
- The loud sentence about the execution sandbox in the MCP listing.

Everything before this wave assumes no external users. This is the wave that
changes that assumption, and it should not start until the owner wants it.

## Explicitly excluded

- **Phase 8 agent memory (A3.2).** Durable ledger, per-dataset contracts,
  three memories. Weeks of work, hard to verify, and the value is unproven
  until the agent is accurate. Revisit after wave 8.
- **A3.1 read-only fan-out.** Parallel profiling and diagnosis. Dropped
  earlier because the slow part of a run cannot be parallelised, so the
  measured payoff was near zero.
- Slide export, dashboard servers, imputation except as a person-graded
  suggestion with holdout receipts, and LLM-written statistics. All ruled out
  in the capability roadmap and still ruled out.

## Reading order for detail

| Topic | Spec |
|---|---|
| The 56 findings, triaged | `2026-09-06-review-triage-and-fix-plan.md` |
| Autonomy packets | `2026-09-05-autonomy-default-build-packets.md` |
| Phase 7 substrate and detectors | `2026-09-05-p7-substrate-and-detectors-packets.md` |
| Severity and reconcile render | `2026-09-05-p7-cross-cutting-packets.md` |
| Capability rationale (B items) | `2026-09-04-capability-roadmap.md` |
| Agent-system rationale (A items) | `2026-09-04-agent-system-roadmap.md` |
| Phase 6 to 8 definitions | `2026-08-31-phase6-plus-roadmap.md` |

## The shape of it

Waves 1 to 5 make crivo **correct and measurable**. Waves 6 to 13 make it
**capable**. Wave 14 makes it **available**. The ordering is not negotiable in
one place only: the instrument comes before the capability it measures, which
is the rule we already broke once.
