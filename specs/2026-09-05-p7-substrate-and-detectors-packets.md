# P7 substrate + core detectors: build packets (substrate, temporal, FK)

2026-09-05. **Status: build-packet spec for owner review. Planning only, no
code.** This decomposes P7 requirement R3 (the substrate) plus two of the
three new detector families (temporal, FK) into small, ordered, self-contained
packets, each a reviewed diff the way M1/M2 ran (P7 design decision 4). It is
grounded in the code as it stands today: every function, constant, and
invariant named below was read in `src/crivo/detect.py`,
`src/crivo/semantic_types.py`, `src/crivo/crosscol.py`, `src/crivo/rowdiff.py`,
`src/crivo/api.py`, `src/crivo/loop.py`, `tests/test_detect.py`,
`tests/test_semantic_types.py`, `tests/test_crosscol.py`,
`tests/test_taxonomy_drift.py`, and `scripts/check_taxonomy_drift.py`.

Inputs: `specs/2026-09-05-p7-harder-data-design.md` (the approved design, its
R1 to R5 and owner decisions 1 to 4). Cross-column FD is already built
(`crosscol.find_fd_violations`, surfaced as `Report.cross_column`); it is not
re-specified here except where the substrate touches it.

## What

Three interlocking substrate models, then two deterministic detectors that ride
them:

- **Substrate (P7 R3).** A column-role model that says which columns are keys,
  dates, amounts, or categoricals (extends `semantic_types.infer_types`, does
  not duplicate it); a time-axis selector that reads the role model rather than
  sniffing dates (owner decision 2); and a table-relation model that says which
  loaded frames are related and on what key (extends the `detect_family`
  grouping from same-schema slices to related tables).
- **Temporal detector (new disease id 27).** Monotonic-order breaks where order
  is expected, outsized gaps, duplicate periods, and values that jump beyond a
  plausible rate of change. Far-future timestamps are deliberately left to
  `_d13`, which already grades them GATE, so temporal does not re-report them.
  Registered through `register(27)` and returned through `_finding`, so it flows
  through `detect_all`, grading, and the report unchanged (R1). It is an
  `INDICATORS` member (like disease 12 and 15): detected and evidenced, never
  auto-fixed. An honest "not applicable" when there is no time axis.
- **FK-integrity detector (new disease id 28, family-only).** An orphan foreign
  key (a child key value with no parent row), a broken one-to-many cardinality,
  and a join key whose type or format disagrees across tables. Rides the family
  path (`detect_family`, `FAMILY_ONLY`), deterministic-first with an optional
  model-nomination budget verified before anything becomes a finding (R2).

## Requirements (inherited from the P7 design, restated as they bind here)

- **R1 Same detector contract.** Temporal registers through `register(27)` and
  returns `_finding(27, columns, evidence, stats, grade, confidence)`. FK
  returns `_finding(28, ...)`. No parallel finding shape (contrast the pre-P7
  `crosscol.find_fd_violations`, which returns its own dict shape and is not in
  `REGISTRY`; the two new detectors do go through `_finding`).
- **R2 Deterministic first, model only on the budget.** The keyless detectors
  do the deterministic verification and a cheap exhaustive blind search. Where
  P7 wants model nomination (non-obvious FK key pairs), the nomination is an
  optional parameter the detector accepts and then verifies deterministically;
  producing the nomination is the agent surface's job, out of the keyless
  detector (mirrors `crosscol`'s "keyless, no model" discipline and its
  import-isolation test).
- **R3 Interlocking models, built in order.** Role model first (packet P1),
  then the time selector (P2) and the relation model (P3), then the detectors
  that consume them (P4 to P7).
- **R4 Bounded and honest.** FK relationship discovery is capped
  (`max_relations`) with a `relations-capped` note, exactly as
  `crosscol.find_fd_violations` rides a `pairs-capped` note. A check that
  cannot run (no time axis, no key) reports that as a checked "not applicable,"
  never a false pass.
- **R5 Grades unchanged in spirit.** A clear violation (an out-of-order
  timestamp where order is expected, an orphan FK, a key-type mismatch) is GATE,
  matching how `_d13` grades a far-future datetime GATE (far-future timestamps
  stay with `_d13`; temporal does not re-report them) and how `detect_family`
  grades a dtype change GATE. A suspicion (a rate jump, a broken cardinality, an
  outsized gap) is HUMAN, matching the HUMAN indicators `_d15` and `crosscol`.
  Nothing new is AUTO except the honesty notes (`no-time-axis`,
  `relations-capped`), which are mechanical and certain.

## Substrate constraints these packets must not break

Read from the code; every packet's "invariant" section points back here.

1. **`detect.py` is import-pure.** Its module docstring: "pandas + stdlib only,
   no imports from the rest of the package." So a `register`ed detector
   (`_d27`) and `detect_relations` may **not** `import` `semantic_types` or
   `rowdiff`. The role map and relation candidates are **passed in** by the
   caller (`api.py`, which may import freely), or re-derived locally from the
   name regexes already in `detect.py` (`ID_NAME`, `LAT_NAME`, the
   `DATE_FAMILIES` machinery). This is why cross-column lives in its own
   `crosscol.py` and not in `detect.py`.
2. **The clear/found invariant.** `tests/test_detect.py` (lines 880, 1045)
   pins `set(res["clear"]) | diseases == set(detect.SINGLE_FRAME)`. Adding 27
   to `SLUGS` (and not to `FAMILY_ONLY`) puts it in `SINGLE_FRAME` the same
   instant, so `_d27` must be registered in the very diff that reserves 27:
   until it is, `detect_all` calls `REGISTRY[27]`, `KeyError`s, files 27 under
   `broken`, and 27 falls out of both `found` and `clear`, breaking this
   invariant. That is why 27's `SLUGS` entry lands in P4 with the detector, not
   in P0 (see P0). Once registered, on every existing test frame `_d27` must
   either fire or land in `clear`: it must not crash and must be conservative
   enough not to false-fire on the non-temporal fixtures already in the suite.
3. **`detect_family`'s return semantics are load-bearing.** `loop._family_body`
   reads `detect_family(...)` output into `run["drift"]` and branches on
   `if run["drift"]:` to decide whether to harmonize schema (disease 20). FK
   findings must **not** be appended to `detect_family`'s return, or the
   harmonize decision misfires. FK gets its own entry point (`detect_relations`)
   and its own kernel cell.
4. **Taxonomy drift.** `scripts/check_taxonomy_drift.py:find_drift` fails CI
   unless every `FAMILY_ONLY` id and every `autoclean.FIXERS` id is a key in
   `SLUGS` (it does not police `INDICATORS`). So 28 must be added to both
   `SLUGS` and `FAMILY_ONLY` in P0, before P6 calls `_finding(28, ...)` (which
   does `SLUGS[disease]` and would `KeyError`); 27 must be in `SLUGS` before
   P4's `_d27` calls `_finding(27, ...)`, which is why 27's `SLUGS` entry lands
   in P4 alongside the detector (not earlier: an unregistered `SINGLE_FRAME` id
   reddens `detect_all`, constraint 2).
5. **Pinned `semantic_types` behavior.** `tests/test_semantic_types.py` pins 25
   cases of `infer_types`, including "a text date column types as nothing" and
   "a unique numeric column is never read as text." The role model must **add a
   layer** (`infer_roles`) without changing `infer_types`'s output, so those
   cases stay green.
6. **`SIGNALS` is derived, not hardcoded.** `api.SIGNALS = len(SINGLE_FRAME)`
   and `checkup`/`notebook` render `len(SINGLE_FRAME)`, so the headline count
   updates itself when 27 joins `SINGLE_FRAME`. No literal "22"/"25" is
   asserted anywhere in the suite (grep confirmed: the only count assertion,
   `test_notebook.py:42`, interpolates `len(SINGLE_FRAME)`).

---

## Packets (ordered)

### P0. Reserve the FK taxonomy id 28 (the gate `_finding` requires)

1. **Files and functions touched.** `src/crivo/detect.py`: the `SLUGS` dict and
   `FAMILY_ONLY = frozenset({20})`. Nothing else.
2. **Interface change.** Add one key to `SLUGS`: `28: "foreign-key-integrity"`
   (kebab-case like the existing 26). Change `FAMILY_ONLY` to
   `frozenset({20, 28})`. `SINGLE_FRAME` recomputes automatically
   (`tuple(d for d in sorted(SLUGS) if d not in FAMILY_ONLY)`), and because 28
   is family-only it does **not** join `SINGLE_FRAME`, so `detect_all` never
   calls `REGISTRY[28]` and nothing reddens. `INDICATORS` is left unchanged
   here. The temporal id 27 is **not** reserved in P0: adding 27 to `SLUGS`
   would put it in `SINGLE_FRAME` while `_d27` is still unregistered, so
   `detect_all` would `KeyError` on `REGISTRY[27]` and break the partition
   invariant (constraint 2). 27's `SLUGS` entry and its `INDICATORS` membership
   land in P4, in the same diff as the detector.
3. **Acceptance tests.** `tests/test_taxonomy_drift.py::test_no_drift_in_the_live_taxonomy`
   stays green (28 is now in `SLUGS`, so `FAMILY_ONLY - set(SLUGS)` is empty).
   A new assertion: `28 not in detect.SINGLE_FRAME and 28 in detect.FAMILY_ONLY`.
   `detect_all(pd.DataFrame({"x": [1, 2, 3]}))` still returns without error and
   the pinned partition invariant `set(clear) | diseases == set(SINGLE_FRAME)`
   (`tests/test_detect.py:880`, and `:1045` under raha) still holds, because 28
   never enters `SINGLE_FRAME`, so no detector is called for it. There is no
   deferred 27 sub-assertion here: 27 is reserved and registered together in P4.
4. **Ordered steps.** (a) Add the `28: "foreign-key-integrity"` `SLUGS` entry.
   (b) Add 28 to `FAMILY_ONLY`. (c) Run `scripts/check_taxonomy_drift.py`, the
   taxonomy drift test, and the ungated `test_detect.py` partition test to
   confirm `SINGLE_FRAME` is unchanged (28 stayed out, no `broken["28"]`).
5. **Risks and the invariant.** Risk: calling `_finding(28, ...)` (P6) before
   this packet lands `KeyError`s on `SLUGS[28]`. Risk avoided by construction:
   27 is **not** added to `SLUGS` here, so P0 cannot put an unregistered id in
   `SINGLE_FRAME`; landing 27 early would `KeyError` `REGISTRY[27]` in
   `detect_all`, file `broken["27"]`, and break the invariant at
   `test_detect.py:880`/`:1045`, so 27 waits for P4. Invariant (constraint 4):
   every `FAMILY_ONLY` id is a `SLUGS` key. P0 is a prerequisite for P6; land it
   first.

### P1. Column-role model (`infer_roles`), extends `semantic_types`

1. **Files and functions touched.** `src/crivo/semantic_types.py`: new
   module-level predicate `_is_date` (reusing the existing `_DATEISH` regex, as
   a `fullmatch` positive test), new public `infer_roles`, and a new module
   import `from crivo.detect import ID_NAME` (the codebase's only column-name
   identifier regex, reused for the key role; one-way and cycle-free, see step
   5). It **calls** the existing `infer_types`, `_present_values`, and `_rate`;
   it does not modify them or `_DETECTORS` or `_classify`.
   `tests/test_semantic_types.py`: a new parametrized block for roles.
2. **Interface change.** New:
   ```python
   def infer_roles(df: pd.DataFrame, time_hint: str | None = None) -> dict:
       """Per-column role map for the P7 harder-data detectors (R3a). Extends
       infer_types (does not duplicate it): its value-typed semantic types
       (email, currency_amount, identifier, ...) are folded in, and roles that
       need a dtype (date, amount, key, categorical) are layered on so numeric
       and datetime columns, which infer_types never reads, get a role too."""
   ```
   Return shape (deterministic, conservative, evidence-carrying like
   `infer_types`):
   ```python
   {
     "roles": {                       # one entry per column
       "<col>": {
         "role": "key"|"date"|"amount"|"categorical"|"other",
         "semantic_type": str | None, # passthrough from infer_types, else None
         "source": "dtype"|"values"|"hint",
         "confidence": float,         # match_rate for value roles, 1.0 for dtype
         "evidence": str,             # names the rule and threshold, auditable
       }, ...
     },
     "keys": ["<col>", ...],          # convenience projections the detectors read
     "dates": ["<col>", ...],
     "amounts": ["<col>", ...],
     "categoricals": ["<col>", ...],
     "notes": ["time_hint 'x' did not resolve to a date column", ...],
   }
   ```
   Deterministic assignment rules, each reusing a threshold already in the code
   so nothing new is invented:
   - **date**: a `datetime64` dtype column (`pd.api.types.is_datetime64_any_dtype`,
     the same test `_d13` uses), source `dtype`, confidence 1.0; or an
     object/string column with `_rate(present, _is_date) >= THRESHOLD` (the
     existing 0.9), source `values`. An unresolved `time_hint` is recorded in
     `notes`, never forced.
   - **amount**: a non-bool numeric dtype column (`is_numeric_dtype` and not
     `is_bool_dtype`, matching `_numbers`' guard in `detect.py`), source `dtype`;
     or a text column `infer_types` typed `currency_amount`, source `values`.
   - **key**: a text column `infer_types` typed `identifier` (value-based:
     unique and code-shaped, `semantic_types._is_code`); or any non-float column
     unique per row (`nunique(dropna=True) == len(df)`) whose name matches
     `detect.ID_NAME` (`src/crivo/detect.py:188`, the only column-name
     identifier regex in the codebase, the same one `_d22` keys on, imported per
     step 1). The second clause is load-bearing, not decorative: it catches a
     numeric primary key (an integer `id` column) that `infer_types` never types
     because `_rate` only scores string cells. Floats are never keys (mirror
     `_d11`'s `is_float_dtype` guard). Conservative on purpose: a key claim
     gates the FK detector.
   - **categorical**: a non-numeric, non-datetime, non-key column with
     `2 <= nunique(dropna=True) <= max(50, len(df) / 5)` (the coarseness bound
     `_fd_candidates` already uses in `detect.py`).
   - **other**: anything unclassified. Precedence when two rules match: key >
     date > amount > categorical (a unique date-shaped id column is a key).
3. **Acceptance tests.** Table-driven like the existing `CASES`: a datetime
   column types `date`/`dtype`/1.0; a text `YYYY-MM-DD` column at >= 0.9 types
   `date`/`values`; a text column 1/3 dates types `other` (under threshold); an
   `int` amount column types `amount`/`dtype`; a unique `INV-100x` column types
   `key` (via `infer_types` identifier); a unique integer `customer_id` column
   types `key` via `detect.ID_NAME` (the numeric-key case `infer_types` cannot
   see); a float column that happens unique types `amount`, never `key`; a
   3-value text column types `categorical`; `time_hint="ts"` on a datetime `ts`
   puts `ts` first in `dates` with source `hint`; `time_hint="missing"` leaves
   `dates` empty and adds a `notes` line. Regression: **all 25 existing
   `test_infer_types` cases stay green** (proves "extend, do not duplicate").
   The keyless-import test `test_import_is_keyless_and_pulls_no_core_module`
   (`tests/test_semantic_types.py:127`) still passes: it reserves
   `crivo.loop/prompts/skills/provenance/llm`, not `crivo.detect`, so importing
   `ID_NAME` from the import-pure `detect` module does not trip it.
4. **Ordered steps.** (a) Add `_is_date`. (b) Write `infer_roles`: start from
   `infer_types(df)` keyed by column, then walk `df.columns` applying the dtype
   and cardinality rules and the precedence. (c) Build the four projection lists
   and `notes`. (d) Tests, including the 25-case regression.
5. **Risks and the invariant.** Risk 1: touching `_DETECTORS`/`infer_types` to
   add a date type would flip the pinned "date strings type as nothing" case
   (constraint 5). Mitigation: date lives only in the new `infer_roles` layer.
   Risk 2: the new `from crivo.detect import ID_NAME` edge could add a core
   module to the keyless graph or a cycle. Mitigation and proof: `detect.py`
   imports only pandas + stdlib (its module docstring, constraint 1) and never
   imports `semantic_types`, so the edge is one-way and cycle-free; the keyless
   test reserves `loop/prompts/skills/provenance/llm`, not `detect`; import only
   the `ID_NAME` constant, not the detectors. Invariant: `infer_types`' output
   is byte-for-byte unchanged; the role model is strictly additive.

### P2. Time-axis selector (`time_axis`), the "not applicable" seam

1. **Files and functions touched.** `src/crivo/semantic_types.py`: new
   `time_axis`. `tests/test_semantic_types.py`: a small block.
2. **Interface change.** New:
   ```python
   def time_axis(roles: dict, hint: str | None = None) -> dict:
       """Pick the single ordering column for temporal checks from the role map
       (owner decision 2: from the role model, not a bespoke sniffer). A
       datetime-dtype date wins over a value-typed date; a hint that resolves to
       a date role overrides both."""
       # -> {"column": str | None, "source": "hint"|"dtype"|"values"|None,
       #     "applicable": bool, "reason": str}
   ```
   `applicable` is `False` with `reason="no date/time column typed"` when
   `roles["dates"]` is empty and no hint resolves. This is the honest
   not-applicable claim the accessor (P5) surfaces.
3. **Acceptance tests.** A role map with one datetime date returns it,
   `source="dtype"`, `applicable=True`. A map with a datetime date and a
   value-typed date returns the datetime one (dtype wins). A hint naming a
   value-typed date overrides the dtype default (`source="hint"`). An empty
   `dates` returns `column=None`, `applicable=False`, with a non-empty `reason`.
4. **Ordered steps.** (a) Implement the precedence (hint that resolves, else
   first dtype-sourced date, else first value-sourced date, else none). (b)
   Tests.
5. **Risks and the invariant.** Risk: a bespoke date sniffer creeping in here
   would violate owner decision 2. Invariant: `time_axis` reads only the `roles`
   dict and the hint; it never inspects `df` values itself. Keep it a pure
   selector over P1's output.

### P3. Table-relation model (`relate_tables`), extends the family grouping

1. **Files and functions touched.** `src/crivo/detect.py`: new
   `relate_tables` beside `detect_family`; a small local `_canon_key` helper
   (int/float and null canonicalization). Reuses the existing `ID_NAME` regex
   and `_name`/`_key`. New `tests/test_relations.py`.
2. **Interface change.** New (import-pure, so `roles` is passed in, not
   imported, per constraint 1):
   ```python
   def relate_tables(
       dfs: dict,
       roles: dict | None = None,          # {table_name: infer_roles(frame)}, optional
       nominations: list[tuple] | None = None,  # (parent_tbl, parent_col, child_tbl, child_col)
       max_relations: int = 200,
   ) -> dict:
       """Candidate parent/child key relationships across loaded frames (R3b),
       extending detect_family from same-schema slices to related tables.
       Deterministic blind search over name- and role-matched key columns;
       optional model nominations (R2) are verified here before they are
       returned. Bounded by max_relations with a relations-capped note (R4)."""
   ```
   Return shape (mirrors the `crosscol` bounded-and-honest shape):
   ```python
   {
     "relations": [
       {"parent": [table, col], "child": [table, col],
        "coverage": float,               # child fk values found in the parent key set
        "source": "blind"|"nominated",
        "evidence": str},
       ...
     ],
     "checked": int, "candidates": int,
     "notes": ["relations-capped: examined N of M candidates (cap K)", ...],
   }
   ```
   A candidate qualifies as a relation only when `parent_col` is a de-facto key
   in the parent frame (unique, non-float) and a strong majority of `child_col`
   values fall in the parent key set (verified with `_canon_key` so an int
   parent key and a float-drifted child key still line up; this re-implements,
   locally, the `3.0 matches 3` and null discipline that `rowdiff._canon_cell`
   documents, because `detect.py` cannot import `rowdiff`). Blind search pairs
   columns whose normalized names match (`_key`) or whose roles are both `key`;
   nominations bypass name matching but face the same value verification.
3. **Acceptance tests.** Two frames, `orders.customer_id` drawn from
   `customers.id`: `relations` names `(customers, id) -> (orders, customer_id)`
   with `coverage == 1.0`. A differently-named pair
   (`customers.id`/`orders.cust`) is found only when passed as a `nomination`,
   and only if the values actually overlap (a nomination whose values do not
   overlap is verified away, returning no relation, proving R2). A frame set
   with many key-shaped columns caps at `max_relations` and rides a
   `relations-capped` note. A single frame (`len(dfs) < 2`) returns empty
   `relations`, like `detect_family`.
4. **Ordered steps.** (a) `_canon_key` (null -> sentinel, integral float ->
   int, else passthrough). (b) A key-detector over one frame (unique, non-float,
   optionally role-confirmed). (c) Blind candidate enumeration by name/role
   match, capped. (d) Value verification (coverage over the canonicalized parent
   key set). (e) Fold in verified nominations. (f) Tests.
5. **Risks and the invariant.** Risk 1: two canonicalizers (`_canon_key` here,
   `rowdiff._canon_cell` there) drifting apart; mitigation: a cross-module test
   asserting they agree on a shared sample, and keep `_canon_key` minimal. Risk
   2: a coincidental value overlap reading as a relation; mitigation: require
   parent-side uniqueness plus a high coverage floor, and grade downstream FK
   findings per R5, never AUTO. Invariant (constraint 3): `relate_tables` is a
   **new** function; `detect_family`'s signature and return are untouched, so
   `loop._family_body`'s `run["drift"]` semantics do not move.

### P4. Temporal detector core (`_d27` via `register(27)`)

1. **Files and functions touched.** `src/crivo/detect.py`: the `SLUGS` dict
   (add `27: "temporal-consistency"`, moved here from P0 so the id and the
   detector land in one diff, constraint 2), `INDICATORS` (add 27:
   `frozenset({12, 15, 27})`), new `@register(27) def _d27(df, cols)`, and a
   module-level helper `_temporal_findings(df, i)` for one datetime column at
   positional index `i`. Reuses `_targets`, `_name`, `_numbers`, `_samples`,
   `_finding`, `is_datetime64_any_dtype`. `tests/test_detect.py`: repoint the
   pinned indicator assertion at line 876 (see step 3). New
   `tests/test_temporal.py`. `autoclean.FIXERS` is **not** touched: temporal has
   no deterministic fixer, and its evidence-only guarantee comes from being an
   `INDICATORS` member, not from the FIXERS absence (step 5, risk 2).
2. **Interface change.** `_d27(df, cols) -> list` in the standard registered
   shape. Owner decision 2 is honored within the import-pure constraint by
   splitting the axis source: `_d27` (the `detect_all` signal) trusts only real
   `datetime64` columns (unambiguous, no sniffer, the case decision 2 calls
   "qualifies automatically"); the string-date and explicit-hint axes are the
   accessor's job in P5 (which can import the role model). This is also correct
   sequencing: a string-date column is disease 2/3's subject first, becomes
   `datetime64` after that fix, and only then is a clean time axis.
   This packet also reserves 27 in `SLUGS` and adds it to `INDICATORS`
   (`frozenset({12, 15, 27})`). Temporal is an indicator exactly like disease 12
   and 15: every `_d27` finding carries `indicator=True` (set by `_finding` at
   `detect.py:247`), so `loop._clean` lists it under `state["indicators"]`
   (`loop.py:530`) and excludes it from `state["fixable"]` (`loop.py:529`). That
   INDICATORS membership, not the absence of a `FIXERS` entry, is what keeps a
   temporal finding out of the `/clean` model-fix loop and keeps
   `detect_one`/`verify` from ever running on disease 27 (step 5, risk 2).
   Findings emitted (all via `_finding(27, ...)`): one per datetime column per
   axis-integrity kind (all carrying `[time_col]`, distinguished by
   `stats.kind`), and one per (time, value) pair for rate-jump (carrying
   `[time_col, value_col]`). Because 27 is an indicator, `detect_one` never
   re-runs on them, so kinds sharing `[time_col]` are harmless:
   - `columns=[time_col]`, `stats.kind="order-break"`, **GATE**: the column is
     mostly monotonic (ordered fraction of adjacent pairs `>= 0.8`, so order is
     "expected") but some adjacent pairs descend; evidence names the count and
     the ordered fraction.
   - `columns=[time_col]`, `stats.kind="gap"`, **HUMAN**: an adjacent gap far
     beyond the series cadence (a modified-z-style outlier on the diffs, the
     `_d15` technique); HUMAN because a real gap is rarely provably impossible.
   - `columns=[time_col]`, `stats.kind="duplicate-period"`, **HUMAN**: repeated
     identical timestamps where the column otherwise reads as a unique index.
   - `columns=[time_col, value_col]`, `stats.kind="rate-jump"`, **HUMAN**: after
     sorting by the axis, a numeric value's successive delta is a modified-z
     outlier (`_d15` again). HUMAN, echoing `_d15`'s "extremes are often the
     real signal."
   There is deliberately no `future` kind: far-future timestamps
   (`dt.year >= 2050`) are already `_d13`'s GATE finding
   (`detect.py:1228-1254`), so re-flagging them under 27 would double-report one
   defect (the codebase's "do not make one disease look like two" rule). The
   four kinds above are genuinely new and do not overlap `_d13`.
   When a datetime column is present and none of these fire, `_d27` returns
   `[]` and 27 lands in `detect_all`'s `clear` (checked, clean). When **no**
   datetime column is present, `_d27` also returns `[]` (lands in `clear`); the
   explicit not-applicable claim is surfaced on the accessor (P5), not as a
   finding, to keep the default `diagnose` quiet.
3. **Acceptance tests.** A frame with a descending stretch in a mostly-sorted
   datetime column yields one `order-break` GATE finding with the right count.
   A regular daily series with one month-long hole yields a `gap` HUMAN finding.
   A series with a doubled timestamp yields `duplicate-period`. A time+value
   frame with one 10x-step value yields a `rate-jump` HUMAN finding on
   `[time, value]`. Every `_d27` finding has `indicator=True` and, run through
   `loop._clean`, lands in `state["indicators"]`, never in `state["fixable"]`
   (the evidence-only guarantee). A clean sorted series yields `[]` and
   `27 in detect_all(...)["clear"]`. A frame with no datetime column yields `[]`
   and `27 in clear` (the not-applicable case at `detect_all` level). The pinned
   partition invariant `set(clear) | diseases == set(SINGLE_FRAME)` holds on all
   of these, and the pinned indicator assertion at `tests/test_detect.py:876` is
   repointed from `f["disease"] in {12, 15}` to `f["disease"] in
   detect.INDICATORS` so it tracks the indicator set. (It does not fail today,
   because the ungated `_kitchen_sink` fixture (`test_detect.py:851`) has no
   datetime column, so `_d27` emits no finding there; the repoint is for
   correctness, matching how the partition assertions already read
   `detect.SINGLE_FRAME`.)
4. **Ordered steps.** (a) Reserve 27 in `SLUGS` and add it to `INDICATORS`.
   (b) `_temporal_findings(df, i)` computing the four kinds over one datetime
   column, each guarded so it under-claims. (c) `_d27` iterating `_targets` for
   `is_datetime64_any_dtype` columns and, for rate-jump, pairing each with
   `_numbers` columns. (d) Grade per kind (GATE for order-break, HUMAN for
   gap/duplicate/rate-jump). (e) Repoint the pinned indicator assertion at
   `test_detect.py:876` to `detect.INDICATORS`. (f) Tests, including the `clear`
   invariant and a scan of the existing `test_detect.py` fixtures to confirm no
   false fire.
5. **Risks and the invariant.** Risk 1: false fire on the many non-temporal
   fixtures already in `test_detect.py` breaks unrelated tests (constraint 2);
   mitigation: conservative guards (require `>= 0.8` ordered before calling a
   descent an order-break; require a minimum row count like the other
   detectors' `len < N` gates). Risk 2: a temporal finding must never reach the
   `/clean` fix loop, or the model would be asked to "fix" a report-only signal
   and `detect_one(27, ...)` would run inside `verify.verify_cell`
   (`verify.py:227`). Note the FIXERS-absence argument does **not** deliver
   this: `router.route` sends every GATE/HUMAN finding to the `model` executor
   (`router.py:40-43`), and `loop._clean`'s `fixable` filter keys on the
   `indicator` flag, not on `FIXERS` (`loop.py:529`), so a non-indicator
   temporal finding would fall through `_skill_attempt` and the skipped
   `_autoclean_attempt` straight into `_fix_mini_turn` (`loop.py:568-571`).
   Mitigation: 27 is in `INDICATORS`, so every `_d27` finding is
   `indicator=True`, is excluded from `fixable`, and `detect_one`/`verify` never
   run on it, exactly as for diseases 12 and 15. Two axis-integrity kinds
   sharing `[time_col]` is therefore harmless (`detect_one` is never invoked on
   27), and rate-jump carries `[time_col, value_col]` regardless. Invariant:
   `_d27` never raises (a brittle signal must not sink `detect_all`, though
   `detect_all`'s own try/except is the backstop) and returns `[]` rather than a
   finding whenever the axis is absent or clean.

### P5. Temporal accessor + honest not-applicable (`Report.temporal`)

1. **Files and functions touched.** `src/crivo/api.py`: new method
   `Report.temporal`; it may import `semantic_types` freely (unlike
   `detect.py`). A thin public entry in `detect.py`,
   `temporal(df, time_col) -> list`, that runs `_temporal_findings` against one
   named/coerced column so the accessor can drive a role-selected or
   hint-named axis (including a string-date column coerced to `datetime64`).
   `tests/test_wiring.py`: one test, mirroring
   `test_report_exposes_cross_column_contradictions`.
2. **Interface change.** New:
   ```python
   def temporal(self, time_hint: str | None = None) -> dict:
       """Temporal-consistency checks (P7): order breaks, gaps, duplicate
       periods, and rate-of-change jumps on the frame's time axis. The axis is
       chosen by the role model (owner decision 2), overridable by time_hint.
       Reports an explicit not-applicable when there is no time column.
       Keyless."""
       # -> {"applicable": bool, "reason": str, "time_column": str | None,
       #     "findings": list}  (findings are _finding(27, ...) dicts)
   ```
   Implementation: `roles = semantic_types.infer_roles(self._frame, time_hint)`;
   `axis = semantic_types.time_axis(roles, time_hint)`; if not
   `axis["applicable"]`, return `{"applicable": False, "reason": axis["reason"],
   "time_column": None, "findings": []}` (the honest not-applicable, R4); else
   coerce the axis column to `datetime64` if it is a value-typed date and call
   `detect.temporal(frame, axis["column"])`.
3. **Acceptance tests.** A frame with a `datetime64` axis and an out-of-order
   row returns `applicable=True`, `time_column` set, and an `order-break`
   finding. A frame with a text `YYYY-MM-DD` axis at >= 0.9 is picked up via the
   role model and coerced, so it too returns findings (the case `detect_all`
   alone cannot see). A frame with no date column returns `applicable=False`
   with a non-empty `reason` and empty `findings`. `time_hint` naming a specific
   column overrides the default pick.
4. **Ordered steps.** (a) `detect.temporal(df, time_col)` (import-pure: takes an
   already-chosen column). (b) `Report.temporal` wiring role model + time axis +
   coercion. (c) Tests.
5. **Risks and the invariant.** Risk: coercing a string-date axis mutating the
   caller's frame; mitigation: coerce a copy, never `self._frame` in place.
   Invariant: the accessor is read-only over `self._frame` and never auto-fixes
   (matches `cross_column`, `semantic_types`, `pii` which are all read-only
   accessors).

### P6. FK-integrity detector (`detect_relations`, disease 28, family-only)

1. **Files and functions touched.** `src/crivo/detect.py`: new
   `detect_relations` beside `detect_family`; reuses `relate_tables` (P3),
   `_canon_key` (P3), `_name`, `_finding`. New `tests/test_fk.py`.
2. **Interface change.** New (import-pure; `roles`/`nominations` passed in):
   ```python
   def detect_relations(
       dfs: dict,
       roles: dict | None = None,
       nominations: list[tuple] | None = None,
       max_relations: int = 200,
   ) -> list:
       """FK-integrity findings across a related set of frames (P7 R3 family
       path). Discovers parent/child key relationships via relate_tables
       (bounded, R4), then for each verified relation checks three deterministic
       violations, each returned via _finding(28, ...). Family-only: reached
       from the family flow, never from detect_all."""
   ```
   For each relation `(parent_tbl, parent_col) -> (child_tbl, child_col)` from
   `relate_tables`, emit `_finding(28, [parent_col, child_col], evidence, stats,
   grade, confidence)` for each violation present, with the tables named in
   `stats` (mirroring how `detect_family` puts `"files": [base, other]` in
   stats, since `columns` cannot express two frames):
   - `stats.kind="orphan-fk"`, **GATE**: child key values absent from the parent
     key set (`_canon_key` normalized); stats `{parent_table, child_table,
     orphans, child_rows, sample}`. GATE because referential integrity is
     mechanical, like `detect_family`'s dtype-change GATE.
   - `stats.kind="cardinality-break"`, **HUMAN**: the relation is not the
     one-to-many it looks like (a child fk value maps to rows that disagree on
     what the parent says, or the parent key is not actually unique on the
     child side where a 1:1 was implied). HUMAN because whether it *should* be
     1:many is a judgment.
   - `stats.kind="key-type-mismatch"`, **GATE**: `parent_col` and `child_col`
     disagree on dtype or on a normalized string format (for example int vs
     zero-padded string), so the join silently drops rows. GATE, mechanical.
   Bounded and honest: the `relations-capped` note from `relate_tables` is
   surfaced as a `_finding(28, [], ...)` AUTO note with `stats.kind="relations-capped"`,
   the FK analogue of `crosscol`'s `pairs-capped` (R4).
3. **Acceptance tests.** `customers`/`orders` with two `orders.customer_id`
   values absent from `customers.id`: one `orphan-fk` GATE finding, `orphans==2`,
   both tables named in stats. An int parent key against a zero-padded string
   child key: one `key-type-mismatch` GATE finding. A clean parent/child pair:
   no disease-28 findings. `detect_relations({"only": df})` (single frame) is
   `[]`. A verified-away nomination (values do not overlap) produces no orphan
   finding (R2: nomination is not assertion). Taxonomy drift stays green (28 in
   `SLUGS` and `FAMILY_ONLY`).
4. **Ordered steps.** (a) `detect_relations` calls `relate_tables`. (b) Per
   relation, the three checks, each guarded and graded per R5. (c) Surface the
   capped note. (d) Tests.
5. **Risks and the invariant.** Risk: a false orphan from a type mismatch (the
   values match but the dtypes differ, so a naive set test says orphan);
   mitigation: run `key-type-mismatch` first and normalize with `_canon_key`
   before the orphan set test, so a pure representation gap is reported as a
   type mismatch, not a phantom orphan. Invariant (constraint 3):
   `detect_relations` is a **separate** function from `detect_family`; it does
   not change `detect_family`'s output, so schema-drift harmonization is
   unaffected.

### P7. Wire FK into the family path (`loop._family_body`) and the public surface

1. **Files and functions touched.** `src/crivo/loop.py`: `_family_body` gains a
   second kernel cell that calls `detect_relations`, separate from the existing
   `detect_family` drift cell (constraint 3). `src/crivo/api.py`: an optional
   public `relations(dfs, ...)` entry and `__init__.py` export, mirroring how
   `reconcile` is exposed, so the FK check is reachable keyless without the
   agent loop. `tests/test_loop.py` (or the family test that covers
   `_family_body`) and `tests/test_wiring.py`.
2. **Interface change.** In `_family_body`, after the drift cell sets
   `run["drift"]`, add:
   ```python
   fk_code = (
       "from crivo.detect import detect_relations\n"
       "import json\n"
       f"print(json.dumps(detect_relations({name})))"
   )
   ```
   run it the same way (`self._exec_events(..., quiet=True)`), parse the last
   stdout line, and record the disease-28 findings on `run` under a **new** key
   (`run["relations"]`), reported alongside the family summary in
   `_write_family`. The harmonize branch still keys only on `run["drift"]`
   (disease 20), untouched. `roles`/`nominations` default to `None` in the
   kernel cell (the deterministic blind search runs with no model); the
   nomination budget is a later agent-surface packet, out of scope here.
   `api.relations(dfs, roles=None, nominations=None)` simply calls
   `detect.detect_relations` for the keyless library surface.
3. **Acceptance tests.** A two-frame family with a planted orphan FK: the family
   run's saved summary (`_write_family`'s JSON) carries the disease-28 finding,
   and the harmonize decision is unchanged from today (a family with clean
   schema still logs "slices already share one schema"). `crivo.relations({...})`
   is exported and returns the disease-28 findings for a planted orphan. The
   existing family-flow tests stay green (the drift cell and `run["drift"]`
   semantics did not move).
4. **Ordered steps.** (a) Add the second kernel cell and `run["relations"]`.
   (b) Thread it into `_write_family`'s summary dict. (c) `api.relations` +
   export. (d) Tests, including a regression that `run["drift"]`-driven
   harmonization is byte-for-byte unchanged.
5. **Risks and the invariant.** Risk: appending FK output to the drift cell
   would corrupt the harmonize decision (constraint 3); mitigation: a strictly
   separate cell and a separate `run` key. Invariant: `detect_family`'s cell,
   its parse into `run["drift"]`, and the `if run["drift"]:` branch are
   untouched; `_replay_mapping`'s `self.library.candidates(20)` (disease-20
   specific) is untouched.

---

## Acceptance (whole thread)

- 27 and 28 registered in `detect.SLUGS`; 28 in `FAMILY_ONLY`; 27 in
  `INDICATORS`; `scripts/check_taxonomy_drift.py` and `test_taxonomy_drift.py`
  green.
- `infer_roles` and `time_axis` land in `semantic_types.py` with all 25 existing
  `test_infer_types` cases still green (extend, not duplicate) and the keyless
  import test still passing.
- `_d27` runs inside `detect_all`, is conservative enough that no existing
  `test_detect.py` fixture false-fires, and the invariant
  `set(clear) | diseases == set(SINGLE_FRAME)` holds on every temporal fixture;
  every `_d27` finding is an indicator (`indicator=True`), so the `/clean` fix
  loop never touches it (evidence only).
- `Report.temporal` reports findings on a `datetime64` axis and on a role-typed
  string-date axis, and reports an explicit not-applicable when there is no
  axis.
- `detect_relations` finds a planted orphan FK and a key-type mismatch across
  two frames, verifies away a non-overlapping nomination (R2), and caps with a
  `relations-capped` note (R4); it never changes `detect_family`'s output.
- FK is reachable through the family flow and as `crivo.relations`; the family
  harmonize path is unchanged.
- Suite green, CI green, no regression on the existing 22 checks.

## Build plan (order and dependencies)

| # | Packet | Depends on | New disease id |
|---|--------|-----------|----------------|
| P0 | Reserve FK taxonomy id 28 | none | 28 (declared) |
| P1 | Column-role model `infer_roles` | none | none |
| P2 | Time-axis selector `time_axis` | P1 | none |
| P3 | Table-relation model `relate_tables` | P1 | none |
| P4 | Temporal detector `_d27` | none | 27 (declared + registered) |
| P5 | Temporal accessor `Report.temporal` | P1, P2, P4 | 27 |
| P6 | FK detector `detect_relations` | P0, P1, P3 | 28 |
| P7 | FK family-path wiring | P6 | 28 |

Land P0, P1 first (the gates: P0 reserves 28 for P6, P1 seeds the role model).
P2/P3 then run in parallel. P4 declares and registers 27 in one diff, so it
depends on no other packet; P5 needs P1+P2+P4; P6 needs P0+P1+P3; P7 needs P6.
Each packet is one reviewed diff (P7 design decision 4). Severity tiers and
`reconcile` (already built) are folded per the parent design and are not
re-specified here.

## Non-goals (this thread)

Not cross-column FD (already built as `crosscol.find_fd_violations`). Not the
model-nomination plumbing itself (the detectors accept nominations as a
verified parameter; producing them on the R2 budget is the agent surface, a
later packet). Not a fixer for temporal or FK findings (report and evidence only). Temporal
(27) is an `INDICATORS` member like disease 12 and 15, so `loop._clean` files
it under indicators and excludes it from `fixable`, and the model-fix loop
never runs on it; FK (28) is family-only, produced by `detect_relations`
outside `detect_all`, so it never enters the single-frame `/clean` loop.
Neither has an `autoclean.FIXERS` entry, but the guarantee comes from the
indicator flag and family-only routing, not from FIXERS absence (the `fixable`
filter keys on `indicator`, `loop.py:529`).
Not the bench corpus fixtures and the scored P7 arm (P7 design build-plan step
5, a separate wave once these detectors are green). Not warehouse-scale
execution (B2.2 DuckDB). Not an HTML render for the temporal or FK results
beyond what the existing report already shows.

## Open questions resolved (and how)

1. **New disease ids, or fold into existing ones?** New: 27 temporal, 28 FK.
   `_finding` does `SLUGS[disease]`, so a new family needs a `SLUGS` key; the
   ids 27/28 are unused (max is 26). Cross-column deliberately has no id (it is
   a separate `crosscol` accessor), but the task requires temporal to be
   registered, so it takes a real id.
2. **One temporal id or one per sub-check?** One (27), following `_d13`
   (numeric range + datetime far-future in one detector) and `_d18` (several
   header problems in one finding). Sub-checks are `stats.kind` values with
   per-kind grades; axis-integrity kinds share `[time_col]` (distinguished by
   `stats.kind`) and rate-jump carries `[time_col, value_col]`, and because 27
   is an `INDICATORS` member `detect_one` never re-runs on them, so sharing
   `[time_col]` is harmless.
3. **How does temporal "ask the role model" when `detect.py` cannot import it
   (owner decision 2 vs `detect.py` import purity)?** Split by axis source. The
   registered `_d27` trusts only real `datetime64` columns (the "qualifies
   automatically" case, no sniffer). The string-date and explicit-hint axes are
   handled by `Report.temporal`, which lives in `api.py` and may import the role
   model, coercing a role-typed string date to `datetime64` before running the
   same core. Owner decision 2 is honored at the layer that can honor it.
4. **Where does the not-applicable report live?** On the accessor
   (`Report.temporal` returns `applicable=False`, `reason=...`), not as a
   `detect_all` finding, so the default `diagnose` stays quiet on the many
   frames with no time axis while the honest claim is still available. At the
   `detect_all` level, absence is already a checked claim via the `clear` list.
5. **Does FK go into `detect_family`?** No. `loop._family_body` branches on
   `detect_family`'s output as disease-20 drift; mixing FK in would misfire the
   harmonize decision. FK gets its own `detect_relations` and its own kernel
   cell, and 28 goes in `FAMILY_ONLY`. The "family path" is the plumbing, not
   the single function.
6. **How does model nomination enter without breaking keyless/deterministic
   discipline (R2)?** The keyless detectors accept `nominations` as an optional
   parameter and verify every one deterministically before it becomes a finding;
   with `nominations=None` they run a cheap exhaustive blind search only.
   Producing nominations (the LLM call on the budget) is the agent surface, kept
   out of the import-isolated detectors, exactly as `crosscol` stays keyless.
7. **Key matching across int/float/null drift without importing `rowdiff`?** A
   small local `_canon_key` in `detect.py` re-implements the minimal
   `3.0 == 3` and null discipline that `rowdiff._canon_cell` documents, forced
   by `detect.py`'s import purity; a cross-module test pins the two
   canonicalizers in agreement to stop them drifting.
