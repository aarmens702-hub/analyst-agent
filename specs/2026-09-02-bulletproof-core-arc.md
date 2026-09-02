# Bulletproof-core arc: commons, taxonomy v2, hardening (WRAP)

Status: APPROVED (Aarmen, 2026-09-02 — "bulletproof core first" sequencing).
Scope: make the existing single-table story complete, robust, and adoptable
BEFORE Phase 7. Phase 7 (cross-column/multi-table + severity tiers) is parked;
its entry ticket is a bench evolution (multi-label `Corruption.diseases`).

## What

Three waves, each ending with full-suite authenticate + bench smoke + scoped
commits. Standing guardrails: a new check merges only with its injector,
detector, FP-discipline tests, and bench score (red-first); d16 stays
deferred; `detect.py` has a single writer; nothing touches Aarmen's core
files (loop/prompts/skills/provenance).

## Wave 1 — Commons (adoption surface)

- R1 `crivo.load_example() -> pd.DataFrame` — a small (~60-row) messy frame
  built deterministically IN CODE (no data file in the wheel), with planted,
  docstring-listed diseases, so the README's first block runs offline.
- R2 Header fixer: `FIXERS[18]` in autoclean.py — strip/collapse padded
  names, dedupe/replace "Unnamed: N"; renames recorded as receipts; verified
  by detect_one(18) signal-gone like every fix.
- R3 `Report.to_html(path)` — the notebook card renderer emitted as one
  self-contained standalone file (inline CSS, no external assets).
- R4 CLI linter semantics: `crivo diagnose` exits 0 (clean), 1 (findings at
  or above `--fail-on GRADE`, default GATE), 2 (error). `--json` unchanged
  in shape.
- R5 Docs: `import crivo as cv` convention in README + api docstrings
  (integration step, not an agent task).

## Wave 2 — Taxonomy v2 (six admissions)

- d23 boolean-chaos: mixed Y/N/yes/no/1/0/TRUE in one column; AUTO fixer to
  pandas boolean dtype (NA-safe).
- d24 stray header/footer rows: a row whose cells ≈ the column names, or
  trailing mostly-empty junk; fixer GATE (row deletion stays gated).
- d25 duplicated columns: identical content under two names; fixer GATE drop.
- d26 truncation artifacts: varchar-ceiling length pile-up, "…" endings;
  HUMAN indicator, no fixer.
- d13 fold: date-domain implausibility for datetime columns (far-future,
  epoch-artifact past); GATE.
- d22 fold: Excel remnants — leading apostrophes, formula droppings.
Each: injector (bench/corrupt.py) + corpus entries + detector + tests.

## Wave 3 — Hardening

- H1 property invariants (hypothesis): diagnose never raises/never mutates;
  clean idempotent; every applied fix's signal provably gone.
- H2 bench-as-fuzzer: full_corpus() sweep hunting exceptions; empty ledger
  required. H3 perf budget: detect_all on 300k rows under a stated budget.
- H4 hostile-file batch (truncated, BOM, encodings, malformed json, mixed
  parquet dirs). H5 remote-read timeout + size cap; compression-ratio guard.
- H6 seam robustness: OpenRouter error shapes in the overflow classifier;
  CRIVO_BASE_URL validation; STALL_S env override.
- H7 kernel snapshot upgrade per prime-agent backlog §2 (finale).

## Acceptance

Full suite + ruff green after every wave; smoke deterministic; RESULTS.md
gains rows for all six new checks; no detector merges with a silent
pristine-base FP; CLI exit codes documented in README.

## Priority

Now — ahead of Phase 7 and the LLM-eval wave, per Aarmen's sequencing call.
