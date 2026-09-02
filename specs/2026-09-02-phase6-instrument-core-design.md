# Phase 6 instrument core — the Proving Ground bench (WRAP)

Status: APPROVED (Aarmen, 2026-09-02 — Approach A: detector untouched, scope
"instrument core first", no LLM this wave). Parent: `2026-08-31-phase6-plus-roadmap.md`.

## What

A `bench/` package that measures crivo instead of trusting it: a seeded
corruption-injection corpus with exact ground truth, a scorer for detection
quality (column × disease) and end-to-end repair quality (cell-level, the
Raha-comparable stat), fetchers for the external Raha datasets, and a headless
runner whose smoke subset runs in CI. Deterministic `crivo.clean()` is the only
pipeline scored this wave; the LLM kernel loop and the provider seam are the
next wave, measured by this instrument once it provably works.

## Requirements

### R1 — Ground truth format (`bench/truth.py`) [built first, by hand: it is the frozen interface]

```python
@dataclass(frozen=True)
class Cell:
    row: int          # positional index into the frame (bases guarantee RangeIndex)
    column: str
    original: object  # value in the pristine frame (JSON-safe)
    corrupted: object # value planted in the dirty frame

@dataclass(frozen=True)
class Corruption:
    disease: int                 # taxonomy id 1..22
    columns: tuple[str, ...]
    granularity: str             # "cell" | "row" | "column"
    cells: tuple[Cell, ...]      # granularity == "cell", else ()
    rows: tuple[int, ...]        # granularity == "row": positions in the DIRTY frame, else ()
    note: str = ""

@dataclass
class GroundTruth:
    seed: int
    base: str                    # base-generator name
    n_rows: int                  # pristine row count (dirty may exceed via appended rows)
    n_cols: int
    frame_sha256: str            # sha256 of the dirty frame's to_csv() bytes
    corruptions: list[Corruption]
```

JSON round-trip (`to_json` / `from_json`). The scorer refuses a
frame/manifest pair whose `frame_sha256` does not match.

Coordinate stability rule: injectors modify cells in place and append
row-granular material (duplicates, aggregate rows) at the end — existing rows
are never reordered or shifted, so `Cell.row` stays valid for the life of the
pair. No shuffling (limitation accepted for reproducibility).

### R2 — Bases + injectors (`bench/bases.py`, `bench/corrupt.py`)

- Bases emit **typed-canonical pristine frames** (dates as datetime64, money as
  float, RangeIndex): `transactions(seed, n)` (generalizing
  `scripts/make_transactions.py`'s domain) and `typed_frame(seed, n, spec)`
  (configurable columns: numeric / datetime / categorical / text / id).
- Injectors: one function per plantable disease,
  `inject_dNN(frame, truth, rng, columns=..., rate=...)` — degrade the pristine
  (cast column to object, plant corrupted strings/values), record every planted
  cell/row in the `GroundTruth`. Same seed ⇒ byte-identical corpus, forever.
- Applicability is validated (a date disease aimed at a numeric column raises
  `ValueError`; nothing is silently skipped).
- Coverage this wave: every disease of the 22 that is honestly plantable in a
  single table; the ones that are not (by their nature) are listed in the
  module docstring with one line of why.
- **Anti-circularity rule: this module is written from the disease taxonomy
  documentation only. Its author must not read `detect.py` or `autoclean.py`.**
  The injector plants what the disease *is*, not what the detector looks for.

### R3 — Scorer (`bench/score.py`)

Type-aware equivalence `equivalent(a, b)`: NaN ≡ NaN/None, numerics within
1e-9 relative tolerance, datetimes by parsed equality, everything else exact.
Modeled on `autoclean._same` but owned by bench (bench never imports private
core helpers).

Cell universes over the pristine's n_rows (appended rows are scored only at
row granularity):

- D = cells where dirty ≢ pristine (truly dirty)
- C = cells where cleaned ≢ dirty (touched by the cleaner)
- K = cells where cleaned ≡ pristine (ended correct)

Reported, per disease and aggregate:

1. **Detection (detector-level)**: `detect_all(dirty)` finding is a TP iff the
   manifest holds a corruption with the same disease on an intersecting
   column. Column × disease precision / recall / F1, macro-averaged.
2. **End-to-end dirt-targeting**: P = |C∩D|/|C|, R = |C∩D|/|D|.
3. **Repair F1** (Baran-comparable): P = |C∩D∩K|/|C|, R = |D∩K|/|D|.
4. **Verification stats** (from outside, no core changes): attempted = AUTO
   findings whose disease has a deterministic fixer; applied =
   `CleanSummary.applied`; survived-rate = applied/attempted; needs_review
   counted. Diseases with no fixer are reported **not attempted**, never
   folded into a blended number.

Aggregates are always presented three ways: per-disease, attempted-subset,
overall — the overall row carries the label "deterministic mode baseline".

### R4 — External datasets (`scripts/fetch_raha.py`, `bench/external.py`)

Fetch Hospital, Flights, Beers, Rayyan into gitignored `data/external/raha/`
with pinned URLs + sha256 (exact locations discovered and recorded at build
time; any dataset that turns out not to be freely fetchable is reported and
skipped — the script never half-downloads). Adapter: their clean/dirty pair →
a `GroundTruth` via cell diff (disease ids unknown externally ⇒ detection
scoring is N/A there; end-to-end and repair scoring run unchanged). External
numbers are published in a separate table — they are the un-gameable check on
the synthetic ones.

### R5 — Runner + publication (`bench/corpus.py`, `bench/run.py`)

`uv run python -m bench.run --smoke` (~20 datasets, < 60 s, deterministic) and
`--full` (thousands, headless, on demand). Emits `bench/results/*.json` and
`bench/RESULTS.md`; a `--write-readme` flag rewrites the marked
`<!-- bench:start -->…<!-- bench:end -->` section of README.md. Smoke never
writes the README.

### R6 — CI

One new step in `.github/workflows/ci.yml`: run the smoke bench. It asserts
invariants only (determinism across two in-process runs, oracle and no-op
sanity, schema round-trip) — **no absolute F1 thresholds** until baselines
exist. Nonzero exit on violation.

### R7 — Tests (TDD, colocated in `tests/`)

- Scorer: hand-computed tiny fixtures with known P/R/F1; oracle test (pristine
  passed as "cleaned" ⇒ repair P = R = 1.0); no-op test (cleaned = dirty ⇒
  R = 0, C = ∅ handled without division blowups).
- Injectors: every planted cell is recorded; seed determinism (two runs,
  identical frame bytes + manifest); applicability errors.
- Hypothesis property tests: inject→score invariants hold across the
  parameter space (rates, sizes, disease mixes).
- Raha: network mocked; checksum-mismatch and unavailable-dataset paths.

## Acceptance criteria

1. Smoke run completes < 60 s locally, twice, with byte-identical JSON.
2. Full suite + ruff green; `detect.py` and `autoclean.py` diffs are empty.
3. Oracle / no-op / determinism invariants enforced by the smoke run itself.
4. `fetch_raha.py` fetches (or gracefully reports) all four datasets; scored
   end-to-end when present.
5. README carries the marked bench section; RESULTS.md shows per-disease,
   attempted-subset, and overall tables with the baseline label.
6. Corpus and scorer are usable as a library (`from bench import ...`) so the
   next wave (LLM-loop evals) plugs in without rework.

## Priority

Now — ahead of all P3 leftovers and all other backlog. Rule applied: nothing
ships ahead of the instrument that measures it.

## Non-goals this wave

LLM-loop evals, provider seam, Inspect AI publication, question suites,
cell-mask emission in `detect.py`, corpus shuffling, charts-on-answers,
Windows/3.13 CI matrix.

## Build sequencing

1. `bench/truth.py` + tests — by the integrating agent, first (frozen interface).
2. In parallel: A = R2 (bases+injectors), B = R3 (scorer), C = R4 (Raha).
3. Integration: R5 runner, R6 CI step, README markers, full-suite + ruff, RESULTS.
