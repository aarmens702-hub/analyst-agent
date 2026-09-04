# Research: data-capability gaps, September 2026

Produced by a web-research agent on 2026-09-04 as input to
`specs/2026-09-04-capability-roadmap.md`. Ranked gap report on WHAT crivo can
do (data in, analyses, outputs) vs the 2026 landscape. Sources named per gap.

Market frame: GX Cloud shuts down June 1, 2026 after the Fivetran
acquisition, leaving open-source validation users looking for a home; the
most-cited weakness of hosted AI analysts in 2026 reviews is file-size
ceilings around 50 to 100 MB plus unverifiable answers. Both play directly to
a local-first receipts tool.

Trust landscape: no competitor recomputes and verifies answers the way crivo
does. The industry's trust primitive is provenance ("a human approved this
query earlier"): Snowflake Cortex Analyst's Verified Query Repository,
Databricks Genie trusted assets (1.5M+ Genie Spaces in 2026), Holistics and
Zenlytic metric promotion, Hex Threads auditability. The Pebblous evaluation
of seven cleaning-agent configurations (August 2026) validates crivo's
posture: deterministic detection beat LLM reasoning, repair accuracy was near
zero across all agents, evidence grounding averaged 4 percent, and no
standard lineage framework captures value-level causality. crivo stays
differentiated; the gaps are about capability, not proof.

## 1. Statistical inference with assumption receipts [NET-NEW]

Hypothesis tests (t, chi-squared, Mann-Whitney, ANOVA), confidence intervals,
effect sizes, correlation with significance, with automatic test selection
justified by explicit assumption checks (normality, variance homogeneity,
sample size) that ship as receipts on the answer card. Julius runs the full
statistical stack and is ranked the top AI analyst for statistics in 2026.
Gated model-written pandas is the wrong tool (subtle statistical errors pass
code checks); build a curated deterministic stats toolkit the model calls.
The best identity fit in the whole landscape: a p-value with its assumption
checks attached is an answer that re-checks itself. Size: 1-2 weeks.
Demand: high. Sources: Julius reviews, arXiv 2502.09858 (agentic sequential
falsification).

## 2. Driver decomposition: "why did this metric change" [NET-NEW]

Given a metric and two periods or segments, decompose the change into ranked
contributing factors (mix vs rate, by dimension), with the receipt that
contributions sum exactly to the observed delta. Tellius markets exactly this
("ranked drivers in 60 seconds"); Anomalo auto-generates root-cause
narratives; 2026 reviewer language shifted from "answers questions" to
"investigates changes". Decomposition is arithmetic, fully checkable.
Size: 1-2 weeks. Demand: high. Sources: Tellius root-cause post, Anomalo
product docs.

## 3. Messy Excel intelligence [NET-NEW]

Real workbooks: multiple sheets, multiple tables per sheet, merged and
hierarchical headers, notes rows, pivoted layouts. The landscape moved to
structure recovery (LlamaSheets; SpreadsheetLLM-line research). For crivo
this is ingestion AND a new check family: detected headers, unpivot
suggestions, per-sheet table boundaries as findings with receipts,
human-gated. Size: 2-3 weeks. Demand: high (Excel is where messy data
lives). Sources: LlamaSheets announcement, Sheetpedia (OpenReview).

## 4. PII detection and masking check [NET-NEW]

Detect emails, phones, names, SSNs, cards, IBANs, addresses; grade exposure;
offer masking as a safe fix. Presidio Structured is the reference; PIIBench
(2026) benchmarks detectors. Urgent the moment report.to_html ships: a
shareable artifact with embedded data is a leak vector; "PII scan before
share" is a receipts-native safety feature. Size: ~1 week (wrap Presidio as
optional dependency, own recognizers for the keyless path). Demand: high.
Sources: Presidio docs, arXiv 2604.15776 (PIIBench), arXiv 2506.22305.

## 5. Larger-than-memory engine and cloud object storage [NET-NEW]

2026 reviews hammer hosted analysts for 50-100 MB ceilings; DuckDB v2.0
(async I/O, spill to disk) and Polars streaming made multi-GB local analysis
normal; s3/gcs paths via fsspec are assumed. A pip-installable tool running
22 keyless checks over a 5 GB parquet folder on a laptop is a headline
differentiator, and DuckDB pushdown is the cheap first step toward the
parked warehouse plan. Checks compile to SQL with exact results. Size: 3-4
weeks. Demand: high. Sources: findanomaly 2026 comparison, MotherDuck
ecosystem newsletter, pandas vs Polars vs DuckDB comparisons.

## 6. Expectations aligned to ODCS, auto-drafted from findings [REFINES PLAN]

Two refinements to the uncommitted declarative-expectations plan. Format:
ODCS v3.1 (Linux Foundation) is the convergence point; read and emit ODCS
rather than invent a DSL; datacontract-cli and DataVow already validate ODCS
against DuckDB in GitHub Actions, crivo's CI lane exactly. Drafting:
pointblank's 2026 headline is LLM-drafted validation plans; crivo has a
better seed, its own findings, so "promote these findings to a contract" is
a one-command bridge from keyless discovery to declarative enforcement.
Size: 2-3 weeks. Demand: high among engineering users. Sources: ODCS v3.1,
zircote contract-enforcement post, pointblank fifty-releases post.

## 7. Interactive charts in cards and the report [REFINES PLAN]

Plotly-class interactivity is table stakes (ChatGPT interactive charts,
Julius plotly, marimo). Refine the planned HTML report so every embedded
graph is interactive (vega-lite or plotly JSON, no server); answer-card
charts export PNG/SVG. pointblank's interactive HTML reports are the bar on
the validation side. Size: ~1 week riding on report work. Demand: high.

## 8. Time-series analytics with backtest receipts [NET-NEW]

Trend/seasonality decomposition, anomaly flags on aggregated metrics (Soda
4.0 metric monitors learn thresholds from history), and baseline forecasting
via transparent methods (STL, seasonal-naive, statsforecast) where the
receipt is a rolling backtest: "beat naive by X on held-out weeks."
Foundation models (TimeGPT-2, Chronos-2) exist but transparent methods fit
receipts. Fold metric-history anomaly detection into the planned
compare/drift work (PSI, JS divergence are the credibility reference).
Size: 2-3 weeks. Demand: medium-high.

## 9. Verified answers memory [NET-NEW]

crivo learns fix skills but forgets answers. Pin a recurring question to
approved named code (a lightweight metric definition); repeat questions run
the approved recipe and the card says so. Snowflake VQR and Genie trusted
assets in miniature, using the existing human-gated skills mechanism.
Answers the reviewer complaint that chat analyses "cannot be reused or
governed." Size: 1-2 weeks. Demand: medium.

## 10. Semantic column typing powering validators and unit checks [NET-NEW]

Recognize email/phone/zip/currency/country/URL/ID columns (Sherlock/Sato
lineage; 2025-2026 LLM work), unlocking type-specific validity checks
including mixed-unit detection (kg vs lb flagged as a top-impact error class
in the 2026 Catalog of Data Errors). Feeds PII and sharpens category checks;
each type claim carries pattern-hit-rate evidence, grading naturally.
Size: 1-2 weeks. Demand: medium. Sources: arXiv 2604.09277, arXiv 2508.17203.

## 11. Session-to-notebook export [NET-NEW]

One command turning a session's answer cards into a runnable .ipynb and
marimo .py, with code, checks, lineage as cells. The notebook is the receipt;
reviewers flag non-reproducibility of chat analyses. Size: 2-4 days.
Demand: medium.

## 12. Free-text column categorization [NET-NEW]

Cluster-then-label thematic coding for open-text columns, taxonomy
human-gated, every label citing row evidence. Size: ~1 week. Demand: medium.

## 13. Google Sheets (and Drive) ingestion [NET-NEW]

Data-in demand order from 2026 reviews: messy Excel first, cloud object
storage second, Google Sheets third, warehouse connectors fourth, streaming
niche. gspread makes Sheets days of work. Size: 2-3 days. Demand: medium.

## 14. Keyless record linkage at scale [REFINES PLAN]

Splink links a million records in about a minute on a laptop, unsupervised,
the open-source standard (230k monthly downloads, 2026 Census QA). Optional
Splink-backed "link these tables without keys" with match probabilities as
receipts extends near-duplicate checking into entity resolution. Size: 1-2
weeks. Demand: medium-niche.

## 15. Label-error detection for ML datasets [NET-NEW, later]

cleanlab Datalab flags label errors from predictions; needs model outputs as
input, breaking the keyless promise; audience skews ML engineers. Thin
adapter later, not core. Size: ~1 week. Demand: niche.

## Smaller notes

Scheduled runs: ship a docs recipe (CI Action + cron) plus a
webhook-on-failure flag, not a scheduler. Slide export: off-identity; the
interactive HTML report is the artifact. Imputation: only ever a
person-graded suggestion with holdout receipts, if at all. OpenLineage:
emitting column-level events for applied fixes is a cheap interop flag;
Pebblous notes value-level causality has no standard home, which crivo's
receipts could occupy. Dashboards and generative apps: out of scope.

Critique of the existing plan: Phase 7's ensemble-on-small-label-budget
matches where research landed (deterministic first, LLM on top, per
Pebblous); severity tiers should copy pointblank's threshold-with-actions
shape; dataset compare should produce a ydata-style side-by-side HTML
compare; warehouse pushdown starts as DuckDB pushdown.

## Top-5 shortlist

1. Statistical inference with assumption receipts
2. Driver decomposition
3. Messy Excel intelligence
4. PII scan and mask (with or before the shareable report)
5. DuckDB-backed scale plus s3/gcs paths

With ODCS-aligned expectations as the committed shape whenever the
declarative plan activates.
