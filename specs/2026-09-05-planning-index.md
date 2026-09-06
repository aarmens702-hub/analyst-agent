# P7 + autonomy planning index: read-first review guide

2026-09-05. **Status: read-first index for owner review. Planning only, no
code.** This is the front door to the three build-packet specs produced today.
Read it first, then read the thread specs in the order below. It summarizes each
thread, proposes one build order across all three, and consolidates every open
decision the three specs surface (the autonomy four first) with a recommended
answer for each. Every code anchor named here was read in the current tree
(`src/crivo/policy.py`, `governance.py`, `detect.py`, `autoclean.py`,
`router.py`, `events.py`, `semantic_types.py`, `crosscol.py`, `rowdiff.py`,
`loop.py`, `report.py`, `provenance.py`, `bench/agent_run.py`,
`src/crivo/__main__.py`, `checkup.py`, `htmlreport.py`, `charts.py`).

Inputs (the specs this index ties together):

- `specs/2026-09-05-autonomy-default-build-packets.md` (thread A), decomposing
  `specs/2026-09-05-autonomy-default-design.md`.
- `specs/2026-09-05-p7-substrate-and-detectors-packets.md` (thread B),
  decomposing `specs/2026-09-05-p7-harder-data-design.md`.
- `specs/2026-09-05-p7-cross-cutting-packets.md` (thread C), decomposing the
  cross-cutting pieces of the same P7 design (severity tiers, reconcile render).

## Orientation

Three threads land today, all cut to the same discipline the owner approved for
M1/M2/P7: an approved design is decomposed into small, ordered, self-contained
reviewed diffs, spec-first, no code until reviewed, every packet grounded in the
code as it stands. Thread A makes crivo autonomous by default: AUTO findings
that already carry a registered deterministic fixer apply unattended, human
approval becomes the opt-in `careful` mode, and `report-only` names the
look-but-do-not-touch level, all without touching one safety line. Thread B adds
the P7 substrate (a column-role model, a time-axis selector, a table-relation
model) and two new deterministic detectors that ride it (temporal id 27, foreign
key id 28). Thread C adds a `severity` tier to every finding (orthogonal to the
AUTO/GATE/HUMAN grade) and finishes the `reconcile` HTML render. The three are
independent at the module level (thread A touches `policy.py`/`loop.py`, thread B
touches `detect.py`/`semantic_types.py`, thread C touches the `_finding`
chokepoint and the report renderers) and share one hard line each of them
restates and none of them edits: the person-grade forbid at `policy.py:108-111`
and verify-then-revert. The single coordination point is `detect.py` and its
`tests/test_detect.py`, which threads B and C both edit; the build order below
front-loads the shared `_finding` change so that surface settles once.

## The three threads

### Thread A: autonomy-default mode (5 packets)

File: `specs/2026-09-05-autonomy-default-build-packets.md`. Touches core
(`policy.py` default factory, `loop.py` threading and silence). The load-bearing
observation, confirmed in code: in this phase autonomy is a silence decision
(which AUTO-with-fixer findings apply with no gate shown), not a loosening of any
check. It ships autonomous on the AUTO-with-fixer majority only; it does not
auto-apply GATE this phase (see decision A2).

- **Packet 1: `autonomy` setting in `governance.py`.** Config only. Adds
  `AUTONOMY_LEVELS = ("autonomous", "careful", "report-only")`, an `autonomy`
  field on the `Governance` dataclass (`governance.py:33-43`), and parse plus
  round-trip through `load_governance`/`save_governance`. No runtime reader this
  phase (`load_governance` has zero runtime callers today, confirmed), so it is
  forward config: land it now or defer.
- **Packet 2: default policy in `policy.py`.** A pure factory
  `default_autonomous_policies(valid_disease_ids, fixers)` returning one ENFORCE
  `PolicyRecord` (`policy.py:23-71`) over the AUTO-with-fixer ids. It cannot
  silence a person grade: `evaluate` checks grade before disease
  (`policy.py:108-111`). Twin of the bench `bench-auto` record
  (`bench/agent_run.py:280`).
- **Packet 3: loop threading.** The behavioral core. Threads `autonomy` onto
  `Session` (`loop.py:116`), seeds the default policy so AUTO-with-fixer runs
  silently through the existing `policy.evaluate` batched path in
  `_autoclean_attempt` (`loop.py:1375`), short-circuits `report-only` in `_clean`
  (`loop.py:509`) via `_save_report` (`loop.py:740`), and emits a once-per-
  workspace first-run notice on `events.Notice` (`events.py:50-54`) guarded by a
  `.crivo-autonomy-notice` sentinel. Adds `--autonomy` to `__main__` and threads
  it into both `Session(` sites (`__main__.py:128`, `:155`); leaves
  `router.route`, `policy.evaluate`, and `repl.policy_decision` unchanged.
- **Packet 4: provenance records the level and each silent apply.**
  Observer-only. Adds `autonomy` to `CleanReport` (`report.py:14`) and an
  `unattended` flag to the `_autoclean_attempt` records; no control-flow change.
- **Packet 5: bench arm autonomous vs careful.** Reuses the agent bench
  (`_drive` at `bench/agent_run.py:61`, `_run_case` at `:159`). Both arms drive
  `human_gates="skip"`, so HUMAN and `admit skill` stay skipped (the driver
  mirror of the forbid, `:91`); the autonomous arm's reach comes from the seeded
  policy, never from approving person grades. New result suffixes so it does not
  collide with the existing `.ceiling` files.

### Thread B: P7 substrate + core detectors (8 packets, P0 to P7)

File: `specs/2026-09-05-p7-substrate-and-detectors-packets.md`. Registers new
detectors in the hand-written `detect.py`, so it runs spec-first. Honors the
parent design's already-made owner decisions (time axis from the role model, not
a sniffer; deterministic-first with model nomination on a budget). The whole
thread respects `detect.py` import purity (pandas plus stdlib only), the
clear/found partition invariant (`set(clear) | diseases == set(SINGLE_FRAME)`),
and the taxonomy drift check.

- **P0: reserve FK id 28.** Adds `28: "foreign-key-integrity"` to `SLUGS`
  (`detect.py:25`) and 28 to `FAMILY_ONLY` (`detect.py:55`). 28 stays out of
  `SINGLE_FRAME` (`detect.py:56`), so `detect_all` never calls it and nothing
  reddens. Temporal id 27 is deliberately not reserved here (it would enter
  `SINGLE_FRAME` unregistered and break the partition); 27 lands with its
  detector in P4.
- **P1: column-role model `infer_roles`.** Extends `semantic_types.py` (calls
  `infer_types` at `semantic_types.py:374`, does not modify it), adding key,
  date, amount, categorical roles over dtype and cardinality. Imports only the
  `ID_NAME` constant from the import-pure `detect.py`. The gate for P2, P3, P5,
  P6.
- **P2: time-axis selector `time_axis`.** A pure selector over P1's role map (no
  bespoke date sniffer, per parent decision 2): datetime dtype wins over a
  value-typed date, a resolving hint overrides both, empty dates returns the
  honest `applicable=False`. Depends on P1.
- **P3: table-relation model `relate_tables`.** A new function beside
  `detect_family` (`detect.py:2145`), extending the family grouping to related
  tables via a bounded blind search plus verified nominations, with a
  `relations-capped` note mirroring `crosscol`'s `pairs-capped`
  (`crosscol.py:93`). A local `_canon_key` mirrors `rowdiff._canon_cell`. Depends
  on P1.
- **P4: temporal detector `_d27`.** Reserves 27 in `SLUGS`, adds it to
  `INDICATORS` (`detect.py:54`, becoming `{12, 15, 27}`), and registers
  `@register(27) def _d27` (`register` at `detect.py:206`). Four `stats.kind`
  sub-checks (order-break GATE, gap/duplicate-period/rate-jump HUMAN); no
  `future` kind (that stays with `_d13`). Evidence-only by the INDICATORS flag,
  not by FIXERS absence.
- **P5: temporal accessor `Report.temporal`.** In `api.py` (may import the role
  model, unlike `detect.py`): picks the axis by the role model, coerces a
  string-date axis on a copy, reports honest not-applicable. Depends on
  P1+P2+P4.
- **P6: FK detector `detect_relations`.** Disease 28, family-only. Runs three
  deterministic checks (orphan-fk GATE, cardinality-break HUMAN,
  key-type-mismatch GATE) over relations from `relate_tables`, each via
  `_finding(28, ...)`. Depends on P0+P1+P3.
- **P7: wire FK into the family path.** Adds a second kernel cell in
  `loop._family_body` (`loop.py:861`) under a new `run["relations"]` key, leaving
  the disease-20 `run["drift"]` harmonize decision untouched; exposes
  `crivo.relations`. Depends on P6.

### Thread C: cross-cutting severity + reconcile render (4 packets)

File: `specs/2026-09-05-p7-cross-cutting-packets.md`. Severity touches the
finding contract in `detect.py`, so it lands behind review; the reconcile render
is an additive fast-follow.

- **S1: severity model and `_finding` attachment.** Adds `SEVERITY_TIERS` and a
  `_severity(grade, extent)` helper beside the taxonomy constants, and one
  trailing `extent=None` parameter plus a `"severity"` key to `_finding`
  (`detect.py:238`), the single chokepoint every finding flows through. Severity
  is orthogonal to grade and never enters the `--fail-on` exit code (the
  judgment map `{"AUTO": 0, "GATE": 1, "HUMAN": 2}` at `__main__.py:94` stays
  grade-only). Reaches `to_json` for free.
- **S2: severity in the human and HTML reports.** A `_severity_chip` beside
  `_grade_chip` (`htmlreport.py:35`) and a severity column plus impact-sort in
  `_findings_table` (`htmlreport.py:40`); a tier label appended in
  `checkup.render`/`render_console` (`checkup.py:180`, `:242`) with no reorder,
  to protect snapshots. `charts._worst` (`charts.py:17`) is left as is. Depends
  on S1.
- **S3: extent-driven severity, one detector as proof.** Passes d01's already
  computed `residue_frac` as `extent` to its one `_finding` call, so its severity
  tracks the breach fraction while its grade holds. Documents which other
  detectors carry a usable breach extent and which need a new metric first.
  Depends on S1.
- **R1: reconcile report surfaces the excluded key sets.** `reconcile_report`
  (`rowdiff_report.py`) renders the duplicate-key and null-key counts and bounded
  key lists that `reconcile` (`rowdiff.py:158`) already computes
  (`result["duplicate_keys"]`, `result["null_keys"]` at `rowdiff.py:281-282`),
  reusing `_keys_list`. Pure render, no signature change. Independent of the rest.

## Recommended build order

One order across all three threads. The threads are otherwise independent lanes
(thread A shares no files with B or C beyond reading `SLUGS` and
`autoclean.FIXERS` when it seeds its default policy), so with two builders,
thread A can run fully in parallel from step 1. The one hard cross-thread rule:
land **S1 (step 6) before the thread-B detector packets that call `_finding`**
(P4 at step 11, P6 at step 13) and before S2/S3, so the finding schema and the
`tests/test_detect.py` `FINDING_KEYS` gate settle once, not twice. A note on
thread A and B not interacting: 27 is an INDICATORS member and 28 is
`FAMILY_ONLY`, neither has a `FIXERS` entry, so the autonomous default policy's
disease set is unchanged when P7 lands; thread A needs no rework.

| # | Packet (thread) | Depends on | Reason |
|---|-----------------|-----------|--------|
| 1 | A Packet 2 (default policy factory) | none | Pure, independent foundation of the autonomous default; de-risks the headline thread first. |
| 2 | A Packet 3 (loop threading) | A2 | The behavioral core: seeds the default policy, adds `--autonomy`, `report-only`, and the first-run notice. |
| 3 | A Packet 4 (provenance) | A3 | Observer-only; records the level and the `unattended` flag so an autonomous run stays at least as auditable as a careful one. |
| 4 | A Packet 5 (bench arm) | A3 | Measures autonomous vs careful instead of asserting it, reusing the agent bench with both arms on `human_gates="skip"`. |
| 5 | A Packet 1 (governance key) | none | Forward config with no runtime reader this phase; land here or defer until governance-read wiring is scheduled. |
| 6 | C S1 (severity + `_finding`) | none | Changes the single `_finding` chokepoint every new P7 detector will call; landing it before them settles the finding schema and the `FINDING_KEYS` test gate once. |
| 7 | B P0 (reserve FK id 28) | none | Pure taxonomy gate that `_finding(28, ...)` needs before P6; 28 stays out of `SINGLE_FRAME`, nothing reddens. |
| 8 | B P1 (`infer_roles`) | none | The column-role model P2, P3, P5, P6 all read; strictly additive over `infer_types`. |
| 9 | B P2 (`time_axis`) | B P1 | Pure selector over the role map; the honest not-applicable seam. Parallel-safe with step 10. |
| 10 | B P3 (`relate_tables`) | B P1 | Table-relation model extending the family grouping. Parallel-safe with step 9. |
| 11 | B P4 (`_d27` temporal) | none (S1 preferred) | Declares and registers 27 in one diff (partition-invariant constraint); inherits `severity` from the step-6 `_finding`. |
| 12 | B P5 (`Report.temporal`) | B P1, P2, P4 | Role-selected or hint axis plus the honest not-applicable on the accessor. |
| 13 | B P6 (`detect_relations`) | B P0, P1, P3 (S1 preferred) | The three FK checks over verified relations, each via `_finding(28, ...)`. |
| 14 | B P7 (FK family wiring) | B P6 | Surfaces FK through the family flow without moving the disease-20 harmonize decision. |
| 15 | C S3 (extent-driven d01) | C S1 | One-line proof that severity tracks the breach fraction while grade holds; keeps the `detect.py` edits contiguous with the P7 block. |
| 16 | C S2 (severity in reports) | C S1 | Shows and impact-sorts severity in the HTML report, labels it in text and console. |
| 17 | C R1 (reconcile render) | none | Additive render of data `reconcile` already carries; last, per thread C's own severity-then-reconcile order. |

## Decisions for the owner

### A. Autonomy (needs your call; recommendations from thread A)

1. **Default reach.** Recommended: ship autonomous as the default WITH a
   first-run notice the first time it acts unattended, not a silent flip. Built
   in Packet 3 on `events.Notice("autonomy", ...)` (`events.py:50-54`) plus a
   per-workspace `.crivo-autonomy-notice` sentinel so it fires once. Rationale:
   the default should be autonomous, but a person deserves to be told the first
   time software edits their data without asking.
2. **GATE scope.** Recommended: defer GATE auto-apply this phase; ship autonomous
   on the AUTO-with-fixer majority only. Grounds, read from the code: the only
   GATE-with-registered-fixer case is d01's ambiguous number-convention branch,
   and its fixer `_fix_numbers` strips commas unconditionally, so
   `verify.verify_cell` re-runs `detect_one` and passes whether or not the
   reading was correct ("1,5" becomes 15, a 10x corruption, verifies identically
   to the right answer). A GATE rung waits for a GATE-with-fixer case whose
   re-check validates the resolution, not merely that the signal went quiet (R3).
3. **Third level (`report-only`).** Recommended: keep it as a named level. It
   short-circuits the fix loop and writes the report from diagnosis alone, so the
   one setting spans the full spectrum (decide everything verifiable, decide
   nothing, gate everything) rather than sending users to a different command.
4. **Careful ergonomics.** Recommended: `careful` batches through the M2
   plan-first gate by default (today's behavior, `_build_and_approve_plan` behind
   `CRIVO_PLAN_FIRST`); per-finding gates remain available but a dedicated toggle
   is deferred until asked for.
5. **Packet 1 timing (sequencing choice the spec surfaces).** Recommended: your
   call to land the governance `autonomy` key now as forward config or defer it,
   because `load_governance` has zero runtime callers today (verified) and no
   governance-file precedence is defined in the runtime; the `--autonomy` flag is
   the sole runtime source this phase either way.

### B. Severity tiers (needs your call; recommendations from thread C)

1. **Tier names, thresholds, and actions.** OPEN, your call. S1 proposes
   `info`/`warn`/`critical` at floors `0.0`/`0.10`/`0.40` with actions
   `note`/`review`/`stop` (the pointblank threshold-with-actions shape). Owner
   decides the vocabulary and the cut points.
2. **Absent-extent default.** Recommended (resolved in-spec, flagged for you):
   keep the grade bridge `_SEVERITY_DEFAULT = {"AUTO": "info", "GATE": "warn",
   "HUMAN": "critical"}` for a finding that does not yet pass a real extent
   (useful reports on day one, clearly labeled provisional), rather than a
   neutral `info`.
3. **Text-report sort.** Recommended (resolved in-spec): sort only the HTML
   findings table by impact, leave the text and console order intact to protect
   their snapshots. Owner may extend the impact sort to the text report,
   accepting the golden churn.

### C. P7 substrate + detectors (resolved in-spec from code constraints; confirm)

The parent `2026-09-05-p7-harder-data-design.md` owner decisions 1 to 4 are
already made. The substrate spec's own "open questions" are resolved
deterministically from the code and are listed here only for your confirmation;
none blocks the build.

- **New ids 27 and 28** (not folded into existing diseases), because `_finding`
  does `SLUGS[disease]` so a new family needs a `SLUGS` key, and 27/28 are unused
  (max id today is 26).
- **One temporal id (27)** with per-`stats.kind` sub-checks and per-kind grades
  (following the `_d13`/`_d18` precedent), not one id per sub-check.
- **Temporal is an INDICATORS member** (`{12, 15, 27}`), kept out of the `/clean`
  fix loop by the indicator flag (the `fixable` filter at `loop.py:529`), not by
  FIXERS absence.
- **FK (28) is `FAMILY_ONLY`** with its own `detect_relations` entry point and
  its own kernel cell, NOT folded into `detect_family` (which `loop._family_body`
  reads as disease-20 drift; mixing FK in would misfire the harmonize decision).
- **Import purity preserved:** the role map and nominations are passed in, and
  only the `ID_NAME` constant is imported from the import-pure `detect.py` into
  `semantic_types.py` (one-way, cycle-free).
- **Model nomination** enters as an optional parameter that the keyless detectors
  verify deterministically before it becomes a finding (R2); producing
  nominations is the agent surface, out of the detectors.
- **A local `_canon_key`** in `detect.py` mirrors `rowdiff._canon_cell`
  (int/float/null discipline), pinned by a cross-module agreement test, because
  `detect.py` cannot import `rowdiff`.

## Shared invariants across all three threads

The tie that binds, stated once so each thread spec's restatement can be checked
against it. No packet in any thread edits any of these:

- **The person-grade forbid.** `policy.evaluate` denies any non-AUTO grade before
  any policy is read (`policy.py:108-111`), reading only `"grade"` and
  `"disease"`. This is why thread A's default policy cannot silence a GATE or
  HUMAN finding even for a listed disease id.
- **The router never routes a person grade to auto.** GATE and HUMAN always
  return the model executor (`router.py:40-43`).
- **Verify-then-revert is mandatory.** An applied fix is kept only when
  `verify.verify_cell` returns ok, else `verify.revert_cell` runs; live in
  `_autoclean_attempt` (`loop.py:1375`) and `_fix_mini_turn` (`loop.py:1096`).
- **One finding contract.** Every finding flows through `register`
  (`detect.py:206`) and `_finding` (`detect.py:238`); thread B's new detectors
  and thread C's `severity` field both attach there, so there is no parallel
  machinery.
- **Taxonomy gates.** `scripts/check_taxonomy_drift.py` and the
  `tests/test_detect.py` partition invariant
  (`set(clear) | diseases == set(SINGLE_FRAME)`) gate every taxonomy edit in
  threads B and C.

## Non-goals of this index

This is a review guide, not a fourth spec: it adds no new packet, changes no
requirement, and re-decides nothing the three threads already settled. Where a
thread resolved a question in-spec, this index carries that resolution forward
rather than reopening it. The authoritative detail lives in each thread spec;
read this first, then read them in the build order above.
