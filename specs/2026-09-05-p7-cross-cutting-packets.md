# P7 cross-cutting packets: severity tiers + reconcile render polish

2026-09-05. **Status: build-packet spec for owner review. Planning only, no
code.** These are the two cross-cutting pieces named in
`2026-09-05-p7-harder-data-design.md` (What, final paragraph, and Decisions 3),
decomposed into small reviewed diff packets the way M1/M2/P7 ran. Severity
touches the finding contract in `detect.py`, so it lands spec-first behind owner
review; the reconcile render is an additive fast-follow to a module that already
ships.

Inputs actually read for this spec: `src/crivo/detect.py` (`_finding` at 238,
`register` at 206, `SLUGS`/`INDICATORS`/`FAMILY_ONLY` at 25 to 55, the d01
`_finding` call at 557), `src/crivo/api.py` (`Report.to_dict`/`to_json` at 113
to 122, `compare`/`reconcile`/`reconcile_report` at 260 to 286),
`src/crivo/checkup.py` (`render` at 180, `render_console` at 242),
`src/crivo/htmlreport.py` (`_findings_table` at 40), `src/crivo/report.py`
(`CleanReport`), `src/crivo/charts.py` (`_SEVERITY`/`_worst` at 14 to 18),
`src/crivo/rowdiff.py` (`reconcile` at 158, result shape at 330 to 345),
`src/crivo/rowdiff_report.py` (`reconcile_report` at 94, `_BUCKETS` at 31,
`_counts_table` at 46, `_keys_list` at 61), `src/crivo/__main__.py` (the
`--fail-on` exit-code map at 90 to 97), `tests/test_detect.py` (`FINDING_KEYS`
at 32, the schema test at 864), and `tests/test_reconcile.py` (the
`reconcile_report` HTML tests at 376 to 425).

## What

Two threads, four packets.

**Severity tiers (packets S1 to S3).** A `severity` tier on every finding,
separate from the AUTO/GATE/HUMAN autonomy grade. The grade says how much human
judgment a fix needs (it drives autonomy routing and the `--fail-on` linter
exit code); severity says how bad the violation is (fraction of the data in
breach), so a report can sort by impact. The two are orthogonal: a
`numbers-as-strings` finding keeps its grade (AUTO for a mechanical
currency/unit residue strip) whether that residue covers 5 percent of the
column or 60, while its severity should move with the fraction, so even a
low-judgment AUTO finding can top the impact sort. That divergence is only
deliverable where the detector already computes a breach fraction (one that
rises with the damage): S3 wires the one detector (d01) that plainly does, and
is explicit about which other detectors carry a usable extent and which need a
new metric first.
The shape copies pointblank's threshold-with-actions model named in the design
(design line 34, `docs/research/2026-09-04-capability-gaps.md` line 173): an
ordered table of tiers, each a floor on the failing extent plus a named action.

**Reconcile render polish (packet R1).** `reconcile()` and its HTML twin
`reconcile_report()` both already ship (design Decision 3 landed data-first plus
the fast-follow render). The render is largely complete. One concrete gap
remains: `reconcile()` deliberately elevates two excluded sets (duplicate keys
and null keys) to first-class, bounded evidence in its result
(`result["duplicate_keys"]`, `result["null_keys"]`, and their counts in
`result["counts"]`), but `reconcile_report()` surfaces those exclusions only as
a prose note, never as the key values or their counts. R1 renders what the data
layer already computes.

## Requirements

- **R1 Same finding contract.** Severity attaches at the single `_finding`
  chokepoint (`detect.py:238`), so every finding, indicator and fixable alike,
  carries it, and no parallel machinery appears. Detectors keep registering
  through `register` and returning through `_finding`.
- **R2 Additive and backward compatible.** Adding `severity` must not change any
  existing finding key or value. The one strict schema gate is
  `tests/test_detect.py` `FINDING_KEYS` (line 32) and its `assert set(f) ==
  FINDING_KEYS` (line 866); that is the only test that pins the key set. Every
  other consumer reads findings by key (`checkup.render`, `htmlreport`,
  `report.CleanReport`, `charts.overview`) and tolerates an added key. No stored
  JSON golden embeds a finding (verified: `"indicator"` appears only in inline
  test fixtures, never in a `.json` golden).
- **R3 Severity is separate from grade and never touches exit codes.** The
  `--fail-on` linter maps grade to a judgment rank (`__main__.py:94`,
  `judgment = {"AUTO": 0, "GATE": 1, "HUMAN": 2}`) and exits 1 above the
  threshold. `tests/test_cli_exit_codes.py:5` states it outright: "the threshold
  order is about judgment required, not severity." Severity must not enter that
  path.
- **R4 Threshold-with-actions shape.** Tiers are an ordered floor-plus-action
  table, not a free enum, so a tier both classifies impact and names what to do.
- **R5 Reconcile renders only what the result already carries.** R1 adds no new
  computation to `reconcile()`; it reads `result["duplicate_keys"]`,
  `result["null_keys"]`, and `result["counts"]` and reuses the existing
  `_keys_list` helper and the `compare_report` stylesheet. No signature change,
  no new dependency, no network, no key.
- **R6 House invariants.** Keyless and pure (pandas plus stdlib). No em dashes in
  code comments or the spec. Reports stay self-contained (no external resource,
  no script) and HTML-escape every dynamic value.

## Build plan (ordered packets)

Order: S1, then S2 and S3 (both depend on S1, independent of each other), then
R1. R1 depends on nothing and could land first; it is placed last to mirror the
design's severity-then-reconcile order.

---

### Packet S1: severity model and `_finding` attachment

**1. Files and functions touched.**
- `src/crivo/detect.py`: add the tier table and a `_severity` helper beside the
  taxonomy constants (near `INDICATORS`/`FAMILY_ONLY` at lines 54 to 55); extend
  `_finding` (line 238).
- `tests/test_detect.py`: extend `FINDING_KEYS` (line 32) and
  `test_finding_schema_and_ordering` (line 864).
- No change to `api.py`: severity reaches `to_json` for free (see below).

**2. Exact interface change.**
- New module constant, the threshold-with-actions table (names and thresholds
  are a proposal for owner review):
  ```python
  # severity: how bad the breach is, orthogonal to the AUTO/GATE/HUMAN grade.
  # ordered high floor first; each row is (tier, min_extent, action), the
  # pointblank threshold-with-actions shape. extent is the failing fraction.
  SEVERITY_TIERS = (
      ("critical", 0.40, "stop"),
      ("warn", 0.10, "review"),
      ("info", 0.0, "note"),
  )
  # provisional tier for a finding that does not yet pass an extent, keyed on
  # grade so every un-migrated detector still reports a sensible tier. a bridge,
  # replaced per detector by S3, not the end state.
  _SEVERITY_DEFAULT = {"AUTO": "info", "GATE": "warn", "HUMAN": "critical"}
  ```
- New helper:
  ```python
  def _severity(grade: str, extent: float | None) -> str:
      if extent is None:
          return _SEVERITY_DEFAULT.get(grade, "critical")
      for tier, floor, _action in SEVERITY_TIERS:
          if extent >= floor:
              return tier
      return "info"
  ```
- `_finding` signature gains one trailing keyword-defaulted parameter, so all 36
  existing positional call sites in `detect.py` are unchanged:
  ```python
  def _finding(disease, columns, evidence, stats, grade, confidence,
               extent=None) -> dict:
  ```
  and the returned dict gains one key (added last):
  ```python
      "severity": _severity(grade, extent),
  ```
- Flow to `to_json`: automatic, no `api.py` edit. `Report.to_dict` spreads
  `**self._result` (`api.py:118`) and findings live inside
  `result["findings"]` as these dicts; `Report.to_json` is `json.dumps` over
  that (`api.py:121`). A new key on the finding dict serializes with it.

**3. Acceptance tests.**
- `test_finding_schema_and_ordering` passes with `"severity"` added to
  `FINDING_KEYS`, plus a new assertion `assert f["severity"] in {t[0] for t in
  detect.SEVERITY_TIERS}` for every finding.
- A new unit test: `_severity("AUTO", None) == "info"`,
  `_severity("HUMAN", None) == "critical"` (the grade bridge), and
  `_severity("AUTO", 0.6) == "critical"`, `_severity("HUMAN", 0.05) == "info"`
  (extent overrides grade, proving orthogonality).
- `crivo.diagnose(frame).to_json()` on `load_example()` contains `"severity"` on
  each finding.
- The existing detector suite (the 22, `tests/test_detect.py`) is otherwise
  green with zero call-site edits.

**4. Ordered implementation steps.**
1. Add `SEVERITY_TIERS`, `_SEVERITY_DEFAULT`, and `_severity` to `detect.py`.
2. Add the `extent=None` parameter and the `"severity"` key to `_finding`.
3. Update `FINDING_KEYS` and add the tier assertion in `tests/test_detect.py`.
4. Add the `_severity` unit test and the `to_json` assertion.
5. Run the detect and api test modules; confirm green.

**5. Risks and the invariant.**
- Risk: a consumer that assumes the fixed key set breaks. Mitigated: only
  `test_detect.py:866` pins it (grep-verified); all other reads are by key.
- Risk: name confusion with `charts._SEVERITY` (a grade-to-rank map at
  `charts.py:14`, unrelated). Keep the new field named `severity`; do not touch
  `charts._SEVERITY`.
- Invariant the packet must not break: `grade`, `confidence`, `indicator`,
  `slug`, `columns`, `evidence`, `stats`, and `disease` keep their exact values
  and types; severity is purely additive, and the `--fail-on` exit-code path
  (`__main__.py:94`) stays grade-only.

---

### Packet S2: severity in the human and HTML reports

**1. Files and functions touched.**
- `src/crivo/checkup.py`: `render` (the fixable loop at 204 to 214) and
  `render_console` (the fixable loop at 286 to 303).
- `src/crivo/htmlreport.py`: `_findings_table` (line 40), and a small
  `_severity_chip` beside `_grade_chip` (line 35).
- `src/crivo/report.py`: none required (CleanReport renders fix status, not
  grade or severity); left untouched to stay surgical.

**2. Exact interface change.**
- No signature changes. Display-only additions:
  - `checkup.render`: append the tier to each fixable line, for example after
    the grade note line at 210, ` · severity {f['severity']}`.
  - `checkup.render_console`: append the tier to the `tail` Text at 293 to 295.
  - `htmlreport._findings_table`: add a `severity` column header and a
    `_severity_chip(f.get("severity", ""))` cell; the chip mirrors
    `_grade_chip` with its own three-color map.
- Impact ordering (the design's stated purpose, "so a report can sort by
  impact"): sort the HTML findings table rows by severity, highest first, using
  a rank derived from `SEVERITY_TIERS` order, with disease id as the stable
  secondary key. The text and console reports keep today's order (fixable then
  flagged) to protect their snapshots; they gain the tier label only. The
  matplotlib overview already sorts by worst grade (`charts._worst`), so it is
  left as is.

**3. Acceptance tests.**
- `tests/test_htmlreport.py`: the rendered table contains a `severity` header
  and a tier chip per finding; given two findings with different tiers, the
  higher tier renders first in row order.
- `tests/test_checkup.py` (or the render test that exists): the text and console
  output contain the tier string for a fixable finding, and the fixable-then-
  flagged order is unchanged.

**4. Ordered implementation steps.**
1. Add `_severity_chip` and its color map to `htmlreport.py`.
2. Add the severity column and the severity-rank row sort to `_findings_table`.
3. Add the tier label to `checkup.render` and `render_console` (no reorder).
4. Update or add the render assertions above.

**5. Risks and the invariant.**
- Risk: reordering the HTML rows breaks a golden. Mitigated: only the HTML table
  reorders, with a deterministic secondary key; the text and console goldens see
  an appended label, not a reorder.
- Invariant: no finding is dropped, regraded, or recategorized; the
  fixable/flagged/broken partition and the clear-signals footer are unchanged.
  Only display order (HTML) and an added label or column change.

---

### Packet S3: extent-driven severity (opt-in, one detector as proof)

**1. Files and functions touched.**
- `src/crivo/detect.py`: one `_finding` call site, the numbers-as-strings
  detector d01 (line 557), which already computes its failing extent.

**2. Exact interface change.**
- Pass the already-computed extent to `_finding`, no other change:
  ```python
  out.append(_finding(1, [_name(df, i)], evidence, stats, grade, confidence,
                      extent=stats["residue_frac"]))
  ```
  `residue_frac` is built at line 515 (`1.0 - float(direct)`, the fraction of
  values not already clean), which is exactly the "how bad" axis for this check.
  This turns d01's severity from the grade bridge into a real threshold result,
  demonstrating the S1 table on live data.

  The follow-up is documented here so it is not mistaken for uniform: it is NOT
  one line for every detector, because the fractions the detectors already store
  are not all breach fractions, and `_severity` reads `extent` as the failing
  fraction (`extent >= 0.40|0.10`). Only a fraction that rises with the damage
  passes through as is.
  - Breach-extent, ready one line each: `residue_frac` (d01, line 515, already
    wired here), d04's `frac` (lines 711 and 738, the sentinel share), and
    `null_frac` (d19, line 1676, the empty share). These pass straight through.
  - Health fractions, inverse: `modal_frac` (d22, line 1852, the share AT the
    intact modal width) and `in_bc_frac` (d14, line 1369, the share of coords
    inside the valid box) rise when the column is CLEAN, so passing either as
    `extent` reports `critical` on a healthy column. Each must be inverted
    (`1 - frac`), a change to the detector's stats, not a one-line wiring.
  - Confidence over a capped subset, not a column breach: `repair_frac` (d08,
    line 952, the share of the first 50 affected values a round trip fixes) and
    `healed_frac` (d17, line 1607, the share of split rows that rejoin into
    known words) measure how confidently the flaw is repairable, not how much of
    the column is in breach, so a real breach extent must be recomputed before
    wiring. (`parse_frac` at line 509 is likewise not a breach fraction; d01's
    breach axis is `residue_frac`, which is why S3 wires that one.)
  - No breach fraction at all: both indicators, `field-contradictions` (d12) and
    `statistical-outliers` (d15), carry only a conformance fraction or raw counts
    (d12 `confidence`/`violating_groups` at lines 1206 to 1207, d15
    `count`/`median`/`mad`/`max_score` at lines 1404 to 1407), so under S1 they
    fall to the grade bridge, and since both are graded HUMAN `_SEVERITY_DEFAULT`
    pins every one of them to `critical`: the opposite of impact sorting, and
    worst for the two flagged-only findings that most need it. Giving them a real
    severity needs a NEW breach-fraction metric per detector and is out of scope
    for the one-line follow-up.

  Picking or building the right extent metric per check is a per-detector
  judgement left to that detector's author; this packet wires only d01.

**3. Acceptance tests.**
- A crafted frame where d01's residue covers about 5 percent of a column yields
  `severity == "info"`, and one where residue covers about 60 percent yields
  `severity == "critical"`, while `grade` is identical across both (proving
  severity moves with extent and grade does not).
- The existing d01 tests stay green: evidence, grade, confidence, and stats are
  unchanged by adding `extent`.

**4. Ordered implementation steps.**
1. Add `extent=stats["residue_frac"]` to the d01 `_finding` call (line 557).
2. Add the two-frame extent test above.
3. Run the detect suite; confirm d01's other assertions are untouched.

**5. Risks and the invariant.**
- Risk: choosing an extent metric that misreads impact. Mitigated: S3 wires only
  d01, whose residue fraction is unambiguous; the rest is deferred per detector.
- Invariant: passing `extent` changes only `severity`; `grade`, `evidence`,
  `confidence`, `stats`, and the finding's identity are byte-for-byte unchanged.

---

### Packet R1: reconcile report surfaces the excluded key sets

**1. Files and functions touched.**
- `src/crivo/rowdiff_report.py`: `reconcile_report` (line 94), reusing
  `_keys_list` (line 61) and `_counts_table` (line 46); optionally a small
  `_excluded_counts` helper.
- `tests/test_reconcile.py`: extend the HTML render tests (376 to 425).
- No change to `src/crivo/rowdiff.py`: the data is already there.

**2. Exact interface change.**
- No signature change; `reconcile_report(a, b, keys) -> str` is unchanged. Two
  additions inside its returned HTML, both reading data the result already
  carries (`rowdiff.py:281` counts, `rowdiff.py:336` example lists):
  - Excluded counts, rendered as a separate mini-table beside the four-bucket
    table (not merged into it), so the four buckets keep meaning "the partition
    of the matchable key space" and the two excluded sets read as sitting
    outside that partition:
    ```python
    "<h2>Excluded from the diff</h2>"
    + _excluded_counts(counts)  # duplicate_keys and null_keys, from result["counts"]
    ```
  - Two bounded key-list sections, mirroring the existing Added and Removed
    sections and reusing `_keys_list`:
    ```python
    "<h2>Duplicate keys (excluded)</h2>"
    + _keys_list(result["duplicate_keys"], "rem", "No duplicate keys.")
    + "<h2>Null keys (excluded)</h2>"
    + _keys_list(result["null_keys"], "rem", "No null keys.")
    ```
  Natural placement: after the Removed section (line 133) and before Changed, so
  the excluded evidence groups with the other key lists. Every value is escaped
  by `_fmt_key` (line 39), which already wraps `escape`.

**3. Acceptance tests.**
- Extend `test_report_surfaces_duplicate_key_note_and_column_diff` (line 399,
  which today asserts only that the word "duplicate" appears as a note): also
  assert the excluded duplicate key value renders (its `<code>` key cell) and
  its count renders.
- New `test_report_shows_null_keys_excluded`: a frame with a null key renders a
  "Null keys" section showing `None` and the null-key count (today no HTML test
  covers null keys at all).
- The self-contained and escaping invariants still hold: the render stays free
  of `http://`, `https://`, and `<script`, and a hostile key value is escaped
  (extend `test_report_escapes_hostile_values` at 422 to a hostile duplicate or
  null key).

**4. Ordered implementation steps.**
1. Add `_excluded_counts` (or inline the two count cells) to
   `rowdiff_report.py`.
2. Insert the excluded-counts mini-table and the two `_keys_list` sections into
   `reconcile_report`, after Removed.
3. Extend the duplicate-key test and add the null-key test and the hostile-key
   escape assertion.
4. Render a fixture with both a duplicate and a null key; confirm counts, key
   values, and escaping.

**5. Risks and the invariant.**
- Risk: merging the excluded counts into the four-bucket `_counts_table` would
  misrepresent the partition and undercut the receipt's disjoint-cover claim
  (`rowdiff.py:285` to 299). Mitigated: render the excluded counts as a separate
  block, never summed into the four buckets.
- Invariant: `reconcile_report` stays a pure render of the `reconcile` result,
  self-contained, script-free, and fully escaped; no new computation, no
  dependency, no signature change.

## Acceptance (roll-up)

- Every finding, from `crivo.diagnose(...).findings` and `.to_json()`, carries a
  `severity` in the `SEVERITY_TIERS` vocabulary (S1).
- Severity is visibly separate from grade: an extent-driven finding shows a tier
  that tracks the failing fraction while its grade is unchanged (S3), and the
  `--fail-on` exit code is untouched (S1 invariant, R3).
- The HTML diagnosis report shows and sorts by severity; the text and console
  reports show the tier (S2).
- `reconcile_report` renders the duplicate-key and null-key counts and their
  bounded key lists, so the full six-way partition the receipt verifies is
  legible (R1).
- Suite green, the full CI pipeline green, no regression on the existing 22.

## Non-goals (these packets)

- Not calibrating an extent for all 26 detectors; S3 wires one (d01) as proof
  and defers the rest. Detectors that already store a true breach fraction are
  one reviewed line each; the inverse-health, confidence-only, and no-fraction
  cases (both indicators included) need a metric change first, per S3.
- Not acting on the tier action (`stop`/`review`/`note`): the action is recorded
  in the table and shown, not yet wired into autonomy routing or the gate. Tying
  a tier action to policy is a later thread.
- Not adding a severity axis to the CLI exit code or the linter; grade stays the
  sole exit-code key (R3).
- Not restyling the reconcile or compare reports; R1 reuses the existing
  stylesheet and helpers.
- Not the P7 detectors themselves (cross-column, FK, temporal); those are the
  design's separate per-detector packets.

## Open questions (owner decides)

1. **Tier names, thresholds, and actions.** S1 proposes
   `info`/`warn`/`critical` at floors `0.0`/`0.10`/`0.40` with actions
   `note`/`review`/`stop`. Owner's call on the vocabulary and the cut points.
2. **Absent-extent default.** S1 bridges an un-migrated finding to a tier by
   grade (`_SEVERITY_DEFAULT`). Keep the grade bridge (useful reports on day
   one, clearly labeled provisional), or default to a neutral `info` until a
   detector passes a real extent (cleaner separation, blander reports)? Resolved
   here as the grade bridge, flagged for the owner.
3. **Text-report sort.** S2 sorts only the HTML table by impact and leaves the
   text and console order intact to protect snapshots. Extend the impact sort to
   the text report too, accepting the golden churn, or keep it HTML-only?
   Resolved here as HTML-only.
