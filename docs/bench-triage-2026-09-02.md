# Proving Ground first-light triage (2026-09-02)

What the instrument found on its first full run (smoke corpus + 4 Raha
datasets, deterministic mode). Numbers: `bench/RESULTS.md`. Nothing here is a
bench bug — these are findings *about crivo*, surfaced by scoring it against
planted and external ground truth. Detector/fixer changes are core-adjacent:
propose-diffs to Aarmen, gated on this list, never silent fixes.

## 1. Detector silence (the big one)

At 10% cell corruption on 250-row frames, `detect_all` produced **nothing to
score on 11 of 23 datasets**. Complete misses by disease:

- d5 suppression-codes, d7 case-variants (fired d6 instead — see below),
  d10 near-duplicate-rows, d11 key-violations, d13 out-of-domain,
  d16 unit-heterogeneity, d17 packed-fields, d21 aggregate-rows,
  d22 id-numeric-corruption
- d14 broken-coordinates partial (µF1 0.333: catches lat=999, misses swaps
  inside plausible ranges — a swapped (50, -120) is a valid-looking pair)
- d7's dirt still got 0.78-repaired because its trailing-space variant trips
  the d6 whitespace signal — right fix, wrong diagnosis; detection scoring
  correctly charges it as a d7 miss plus a d6 false positive.

Hypotheses to test before any threshold change: (a) prevalence gates — 10%
may sit under per-signal homogeneity thresholds (echoes the old A1 pattern:
quieter as damage spreads); (b) rate sensitivity — rerun the same seeds at
rate 0.3 and 0.5 and see which diseases wake up; (c) genuine gaps (d16/d17
plausibly have no signal at all for mixed-magnitude or packed cells).
The bench can answer (b) cheaply; do that first.

## 2. Hospital overcleaning (external)

`clean()` changed 4,078 cells where truth marks ~509 dirty (dirt-targeting
precision 0.013). Likely the case-variants fixer folding an entire text
column to most-common casing. Verification survived 1.0 while truth
disagrees — textbook proof that "signal gone" and "matches ground truth" are
different claims. Candidate: case-fold only when variants of the same folded
value actually co-occur, not blanket-normalize.

## 3. Repair-undefined-by-design diseases

d4 sentinel-clearing and d19 constant-drop are correct fixes whose landing
spot (NaN, removed column) can never equal the truth value, so repair F1 is
undefined for them — reported as such, never blended (RESULTS legend). The
LLM wave may do better on d4 (imputation is a judgment call, gated).

## 4. `scripts/score_fixes.py` overlap — keep, for now

The P1-era manual scorer overlaps `bench/score.py` but does one thing bench
does not yet: score an **arbitrary cleaned file** (i.e., LLM-mode output)
against a Raha pair. Retire it when the LLM-eval wave gives bench that
entry point. Its plain-number/leading-zero equivalence already migrated into
`bench.score.equivalent_str`. Note: `scripts/fetch_raha.py` now fetches into
`data/external/raha/` (pinned + hashed); score_fixes' old `data/raha/` path
comments are stale.

## 5. Smoke timing vs spec

Acceptance said < 60 s. Measured: ~61 s locally *with* the 4 external
datasets, ~45 s in CI form (externals absent). Amend the spec line to name
the CI form, or shave the external pass later — not worth corpus surgery.
