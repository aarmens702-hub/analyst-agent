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

## 2. Hospital overcleaning (external) — investigated, decomposed

`clean()` changed 4,078 cells where truth marks ~509 dirty (dirt-targeting
precision 0.013). Receipts (per-fix cell attribution, 2026-09-02) say the
original hypothesis was WRONG — the case-variants fixer never ran on
hospital. The real decomposition:

- **Score + Sample (2,000 cells): a benchmark-convention collision, not a
  bug.** Hospital's ground-truth *clean* file uses the literal token
  `'empty'` as its missing marker; `'empty'` is in crivo's own sentinel
  vocabulary (detect.py), so the d4 fix turns it into NaN — defensible
  cleaning that this benchmark scores as damage. Decision: keep crivo's
  behavior, do NOT teach the scorer that `'empty'` means missing (that would
  be fitting the metric to the dataset); hospital's repair numbers carry
  this asterisk permanently.
- **MeasureName (42 innocent cells): real d6 aggressiveness question.** The
  pristine prose legitimately contains internal double spaces ("the  right
  kind"); `_ws_tidy` collapses every internal run. A conservative variant
  (strip edges + nbsp, leave internal runs unless the padded-variant pattern
  co-occurs) would raise external precision but LOWER synthetic d6 repair —
  the taxonomy (and so the injector) counts internal doubles as damage. The
  bench now referees this trade properly: any change gets judged by both
  corpora. Aarmen's call; no unilateral semantics change.
- Verification survived 1.0 throughout while truth disagrees — still the
  textbook proof that "signal gone" and "matches ground truth" are different
  claims.

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

## Rate-sensitivity probe (2026-09-02)

Hypothesis (a) from §1 — prevalence gating — is REFUTED: raising the
corruption rate wakes nothing. Same bases/seeds as the smoke corpus,
injectors called directly at rates 0.1/0.3/0.5, detection micro-F1
("silent" = no finding of the right disease and nothing to score):

| disease | dataset | r=0.1 | r=0.3 | r=0.5 | other signals that fired |
|---|---|---|---|---|---|
| 5 suppression-codes | tx-suppression | silent | silent | silent | d1 |
| 7 case-variants | tx-case-variants | silent | silent | silent | d6 |
| 10 near-duplicate-rows | tx-near-dups | silent | silent | silent | d9 |
| 11 key-violations | tx-key-violations | silent | silent | silent | — |
| 13 out-of-domain | tx-out-of-domain | silent | silent | silent | — |
| 14 broken-coordinates | geo-broken-coords | 0.333 | 0.333 | 0.333 | d1, d11, d15 |
| 16 unit-heterogeneity | tx-unit-mix | silent | silent | silent | d15 |
| 17 packed-fields | tx-packed-fields | silent | silent | silent | d11 |
| 21 aggregate-rows | tx-aggregate-row | silent | silent | silent | d15 |
| 22 id-numeric-corruption | tx-excel-ids | silent | silent | silent | — |

Verdicts:

- d5, d7, d10, d16, d21 — **blind at all tested rates, adjacent-covered**: a
  neighboring signal (d1/d6/d9/d15) sees the symptom, so the dirt is noticed
  but misdiagnosed — detection F1 charges a miss plus a false positive.
- d11, d13, d22 — **blind at all tested rates, nothing fires**: planted
  duplicate keys, impossible negatives, and Excel-eaten ids go completely
  unreported. These three are the sharpest gaps.
- d17 — **blind**; the only companion signal is a d11 firing that looks like
  a false positive of its own.
- d14 — **partial and rate-flat** (0.333 at every rate): catches lat=999,
  never the in-range swaps; higher rates also draw d1/d11/d15 false
  positives on the geo frame.

Implication for the detector-diff proposals: this is not threshold tuning —
rate changes move nothing, so these are missing or mis-scoped signals, and
each proposal to Aarmen should be a new/extended detector for its disease,
landed one at a time with the bench as the regression net (the smoke corpus
already scores every one of them).

Also observed during the probe: d11 and d1 fired on frames where those
diseases were never planted — false-positive scoping bugs in their own
right; each detector-diff proposal should carry a line on its FP behavior
against the frames it should stay silent on.

### Landed same day (2026-09-02, bench-gated, red-first)

- **d13**: data-driven sign-anomaly fallback — a ≥20-value column that is
  ≥75% non-negative with a stray minority below zero fires GATE without
  needing a DOMAIN_BOUNDS name. tx-out-of-domain: silent → 0.667.
- **d11**: the 0.995 uniqueness gate (more damage = more silence, the A1
  shape) replaced by a two-path gate — near-perfect window kept, plus a
  damage-tolerant wide path (0.6–0.98) for id-named/text columns with ≥3
  duplicated rows. Floats never qualify (measurements, not keys); short
  all-digit codes never take the wide path (birthday collisions by
  construction). tx-key-violations: silent → 1.000, and the probe's
  d11-on-geo/reading FP is dead.
- **d22**: mixed-shape branch — an id-named string column whose minority
  collapsed to bare digits/scientific notation while the intact majority
  keeps a non-numeric shape fires GATE (the numeric-only path was
  structurally blind to partial damage). tx-excel-ids: silent → 0.667.
- **d1 FP**: uniform code schemes (constant alpha prefix + fixed-width
  digits, "SIT000123") no longer read as currency residue.
- The wide path's key prior was tightened twice: first floats and short
  all-digit codes, then bare textiness itself — a sentinel-riddled text
  column (12 x "N/A" beside unique names, caught by the admission-kernel
  suite) impersonates a damaged key under any textiness prior, so the wide
  path now requires an id-claiming NAME, full stop.
- Both pristine bases now detect fully clean across seeds. Smoke detect
  µF1 (silence-as-zero): 0.469 → **0.581**. Still open: the
  adjacent-covered group (d5/d7/d10/d16/d21), d14's in-range swaps, and
  the known-and-accepted d13-fallback firing on swapped coordinates in
  the d14 frame (a true observation charged as an FP by single-label
  truth).

## 5. Smoke timing vs spec

Acceptance said < 60 s. Measured: ~61 s locally *with* the 4 external
datasets, ~45 s in CI form (externals absent). Amend the spec line to name
the CI form, or shave the external pass later — not worth corpus surgery.
