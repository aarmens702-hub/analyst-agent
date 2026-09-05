# P7 harder-data: design (WRAP)

2026-09-05. **Status: design proposal for owner review.** P7 is core
detection work (it registers new detectors in the hand-written `detect.py`
and extends `detect_family`), so this spec exists to be reviewed and edited
before any code. Inputs: the master roadmap P7 entry, the capability roadmap
B4.2, and `docs/research/2026-09-04-capability-gaps.md` (Phase 7 aligns with
where the cleaning research actually landed: deterministic-first, LLM on a
small label budget).

## What

Today crivo sees single-column, single-table problems. P7 adds three error
families it currently cannot detect, each a receipts-native check that grades
safe/check/person like the existing 22:

1. **Cross-column dependencies** — a value that contradicts another column in
   the same row: a `state` that does not match its `zip`, a `total` that is
   not `price * quantity`, an `end_date` before `start_date`, a child column
   non-null where its parent is null. Detected as a functional-dependency or
   arithmetic/relational violation, reported with the offending rows as
   evidence.
2. **Multi-table / FK integrity** — across a loaded set of frames: a foreign
   key with no matching parent row (orphan), a broken 1:many cardinality, a
   join key whose types or formats disagree between tables. Rides the
   existing family path (`detect_family`, `FAMILY_ONLY`), extended from
   "same-schema slices" to "related tables."
3. **Temporal consistency** — within a time-ordered frame: a timestamp out of
   monotonic order where order is expected, an impossible gap or future date,
   a duplicate period, a value that jumps beyond a plausible rate of change.

Plus two cross-cutting pieces the research named:
- **Severity tiers** on every finding (not just the AUTO/GATE/HUMAN autonomy
  grade): how *bad* the violation is, copying pointblank's threshold-with-
  actions shape, so a report can sort by impact.
- **`reconcile()`** — a keyed diff between two tables (the keyed twin of the
  already-built `compare` report), surfacing added / removed / changed rows.

## Requirements

- R1 **Same detector contract.** Each new check registers through the
  existing `register(disease)` seam and returns via `_finding(disease,
  columns, evidence, stats, grade, confidence)`, so it flows through the same
  grading, the same report, the same bench scoring. No parallel machinery.
- R2 **Deterministic first, LLM only on the budget.** The detection signal is
  deterministic (a computable violation), per the Pebblous finding that
  deterministic detection beats LLM reasoning. An LLM is used only to
  *propose candidate relationships* to check (which columns might form an FD,
  which pair might be a key) on a small label budget, and every proposal is
  then verified deterministically before it becomes a finding. The model
  never asserts a violation; it only nominates what to test.
- R3 **Interlocking models, built in order.** These checks need shared
  substrate that must land first:
  (a) a **column-role model** (which columns are keys, dates, amounts,
      categoricals) — B4.1 `semantic_types` is the seed; extend it, do not
      duplicate;
  (b) a **table-family model** (which loaded frames are related, and on what
      keys) — extend the existing `detect_family` grouping;
  (c) a **time model** (which column orders the frame, at what grain).
  Cross-column needs (a); FK needs (a)+(b); temporal needs (a)+(c).
- R4 **Bounded and honest.** FD/key discovery is combinatorial, so cap the
  candidate space (pairs and small tuples, not all subsets) and `log`/report
  what was not checked, never silently. A check that cannot run (no key
  found, no time column) reports that as a clean "not applicable," not a
  false pass — absence stays a checked claim.
- R5 **Grades unchanged in spirit.** A clear arithmetic contradiction is
  GATE-worthy; a suspected FD violation on model-nominated columns is HUMAN
  (a judgement call); nothing new is AUTO unless it is as mechanical as the
  existing AUTO checks. Person-grade stays person-grade.

## Acceptance

- New disease ids registered in `detect.SLUGS`, passing the taxonomy
  drift-check; each with unit tests on crafted frames (a known FD violation,
  a planted orphan FK, an out-of-order timestamp) plus a clean-frame case.
- The bench corpus gains fixtures for each family so P7 detection is scored
  the same way (cell/finding-level, against known truth).
- `reconcile(a, b, keys)` returns added/removed/changed with a receipt that
  the three sets partition the key space.
- Suite green; the full CI pipeline green; no regression on the existing 22.

## Build plan (design first, then decompose)

1. **This spec, reviewed by the owner** (core surgery ahead).
2. **Substrate**: the column-role + table-family + time models (R3). Mostly
   extends existing modules; the gate for everything else.
3. **Detector-by-detector**, each a unit (detector + fixtures + grading):
   cross-column first (needs only the role model), then temporal, then FK
   (needs the family model). This stage is genuinely workflow-friendly once
   the substrate interfaces are fixed — one agent per detector, adversarial
   review, exactly like the B pure-module wave.
4. **Severity tiers + reconcile**, folded in.
5. **Bench fixtures + a scored P7 arm**, then the numbers.

## Non-goals (this phase)

Not the ensemble-of-many-detectors label-budget optimization (that is a
later tuning pass); not schema inference across unrelated files; not
imputation of the contradictions it finds (report, never guess). Warehouse-
scale execution of these checks is B2.2 DuckDB, a separate track.

## Decisions (owner, 2026-09-05)

1. **Cross-column search: pairs by blind search, complex by nomination.**
   Blind search covers column *pairs* only (cheap, exhaustive). Multi-column
   dependencies (3+ columns, e.g. total = price * qty * (1 - discount)) are
   NOT brute-forced; the model nominates the specific relationship on the R2
   budget and the checker verifies it deterministically. So depth comes from
   nominate-then-verify, not from widening the blind search. This resolves
   the concern that pairs-first caps complexity: it does not, because the
   complex cases arrive through nomination.
2. **Time column comes from the role model, not a bespoke sniffer.** Temporal
   checks ask the column-role model (the extended `semantic_types`) which
   column is the time axis, rather than sniffing dates themselves. A datetime
   dtype qualifies automatically; a string-date column qualifies only if the
   role model already types it as a date (conservative, evidence-carrying).
   Plus an optional explicit user hint (name the time column) that overrides
   the guess. One disciplined detector, not two.
3. **`reconcile` ships data-first.** `reconcile(a, b, keys)` returns
   added / removed / changed / unchanged keyed row sets with the partition
   receipt; the HTML render (reusing the `compare` styling) is a fast
   follow, not v1. (Distinct from `compare`, which diffs table shape;
   `reconcile` diffs rows by key.)
4. **Claude drafts the `detect.py` registrations behind owner review** — the
   substrate and each detector as small reviewed diff packets, as M1/M2 ran.
