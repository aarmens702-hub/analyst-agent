# Attack roadmap B: analyst capabilities

2026-09-04. What crivo can do: data in, analyses, outputs. Synthesizes the
prior plan (Phase 7 harder data, HTML report, expectations, model breadth)
with `docs/research/2026-09-04-capability-gaps.md`. Companion:
`specs/2026-09-04-agent-system-roadmap.md`.

Market timing on record: GX Cloud shuts down June 2026 (validation users need
a home); the hosted analysts' most-cited weaknesses are ~50-100 MB file
ceilings and unverifiable answers; the Pebblous 2026 evaluation found
deterministic detection beats LLM reasoning and nobody owns value-level
lineage. All three point at a local-first receipts tool.

Standing rules: every new analysis ships with its receipt or it does not
ship; anything judgement-shaped grades as person; new checks join the same
safe/check/person grading; keyless paths stay keyless.

## B0. Ship-safety prerequisite

- B0.1 **PII scan and mask.** Column and cell detection (emails, phones,
  names, SSNs, cards, IBANs, addresses), exposure grading, masking as a safe
  fix. Own recognizers on the keyless path; Presidio as optional extra.
  Hard rule: lands with or before the shareable HTML report, and the report
  pipeline runs the scan before anything is written. [research gap 4]

## B1. The analyst's brain (the identity bets)

- B1.1 **Statistical inference with assumption receipts.** A curated
  deterministic stats toolkit the model calls (t, chi-squared, Mann-Whitney,
  ANOVA, CIs, effect sizes, correlation with significance); automatic test
  selection justified by assumption checks that ship on the card. Not
  model-written scipy: subtle statistical errors pass code checks. [gap 1]
- B1.2 **Driver decomposition.** "Why did this metric change": mix-vs-rate
  and by-dimension contributions, ranked, with the receipt that
  contributions sum exactly to the observed delta. [gap 2]

Exit: bench-style eval cases for both (known-answer decompositions, datasets
with known distributional properties), because analyses get receipts too.

## B2. Data in

- B2.1 **Messy Excel intelligence.** Multi-sheet, multi-table-per-sheet,
  merged and hierarchical headers, notes rows, pivoted layouts. Structure
  recovery lands as findings (detected headers, unpivot suggestions, table
  boundaries) with receipts, human-gated. Doubles as a new check family.
  [gap 3]
- B2.2 **DuckDB-backed scale + cloud paths.** The 22 checks compiled to a
  DuckDB backend for larger-than-memory data; s3/gcs/http paths via fsspec.
  Headline: keyless checks over a 5 GB parquet folder on a laptop. Also the
  cheap first step toward warehouse pushdown. [gap 5]
- B2.3 **Google Sheets ingestion** (days, gspread). Demand order after that:
  warehouse connectors; streaming stays niche. [gap 13]

## B3. Outputs

- B3.1 **Shareable HTML report, interactive** (prior plan, refined): every
  embedded graph interactive (vega-lite or plotly JSON, self-contained, no
  server); findings, grades, per-column detail, receipts; PII-scanned by
  B0.1. Answer-card charts export PNG/SVG. [prior plan + gap 7]
- B3.2 **Session-to-notebook export.** Answer cards to runnable .ipynb and
  marimo .py; the notebook is the receipt. [gap 11]
- B3.3 **Compare report.** Dataset compare/drift (Phase 7 item) renders as a
  side-by-side HTML compare; metric-history anomaly detection (PSI, JS
  divergence) folds in here. [prior plan + gap 8 critique]

## B4. Quality science (Phase 7, upgraded)

- B4.1 **Semantic column typing** (email/phone/zip/currency/country/URL/ID)
  powering type-specific validity checks and mixed-unit detection; type
  claims carry pattern-hit-rate evidence and grade normally. Feeds B0.1.
  [gap 10]
- B4.2 **Phase 7 core as planned, with research alignments:** cross-column
  dependencies, multi-table/FK integrity, temporal consistency; ensemble
  detection stays deterministic-first with LLM on top (per Pebblous);
  severity tiers copy the threshold-with-actions shape. [prior plan]
- B4.3 **Record linkage.** Keyed reconcile (prior plan) plus optional
  Splink-backed keyless linkage with match probabilities as receipts.
  [prior plan + gap 14]
- B4.4 **Time-series analytics.** STL decomposition, seasonal-naive and
  statsforecast baselines, forecasts carrying rolling-backtest receipts
  ("beat naive by X on held-out weeks"). Transparent methods only. [gap 8]
- B4.5 **Free-text column categorization.** Cluster-then-label with
  human-gated taxonomy and row-cited evidence. [gap 12]

## B5. Contracts and ecosystem

- B5.1 **Expectations = ODCS.** Read and emit ODCS v3.1 instead of a
  homegrown DSL; "promote these findings to a contract" bridges keyless
  discovery to declarative enforcement; validates in CI like the existing
  linter. This activates the previously uncommitted expectations plan with a
  committed shape. [prior plan + gap 6]
- B5.2 **OpenLineage column-level events** for applied fixes (cheap interop
  flag; value-level causality has no standard home, receipts can occupy it).
- B5.3 **Scheduled-runs recipe** (docs + webhook-on-failure flag), not a
  scheduler. **cleanlab adapter** thin and later. [notes]

Explicitly not doing: slide export, dashboard servers, imputation except as
a person-graded suggestion with holdout receipts, LLM-written statistics.

## Sequencing note

B0.1 gates B3.1. B1 can start immediately and is the fastest path to "real
analyst" claims. B2.2 is the biggest single differentiator vs hosted tools.
Model breadth (OpenAI-compatible + Ollama) stays on the adoption spec and
serves both roadmaps. Each landed phase gets bench-style eval cases in the
same spirit as the cleaning bench: analyses have receipts, receipts have
tests.
