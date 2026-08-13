# P2.5 Family Mode — Schema Drift and the Harmonizer

- **Status:** approved (design session 2026-08-12, decided by the standing delegation)
- **Upstream:** P2 skill harness (specs/2026-08-12-p2-skill-harness-design.md) — the harmonizer is only interesting because P2 can save it. P1 supplies `detect_family` (disease 20), the gate surface, and the verify-or-revert discipline.
- **Decisions locked:** a family is one kernel variable holding a dict of frames, so nothing in the registry or the profile changes shape · harmonizing is one gated fix over the whole family, not N fixes · the mapping is the artifact worth saving, not the code that applies it · after harmonizing, the existing single-frame CLEAN runs per slice, which is where P2's skills pay for themselves 21 times over.

## What (WRAP)

`/clean-family <glob> <name>` loads every matching file into one variable, diagnoses schema drift across them (disease 20), and asks the model for a single column mapping that makes them one table. The mapping is confirmed once, verified by execution, and saved as a skill whose payload is the mapping itself — so the twenty-second file costs nothing. Then the ordinary CLEAN flow runs per slice, and every single-frame skill born in P2 replays across the whole family.

## Requirements

- R1. `Session.load_family(pattern, name)` loads files matching a glob into `name = {"<slice key>": DataFrame}` in the kernel. The slice key is the file stem. Delimiter and encoding are sniffed per file, not assumed: the Vancouver exports are semicolon-delimited with a BOM, and guessing wrong turns 30 columns into 1.
- R2. The profile for a family reports per-slice shape plus the union and intersection of column sets — never a per-file dump. The model sees at most 40 column names and a drift summary.
- R3. `detect_family` (already built) runs across the loaded frames. Findings are disease 20, graded GATE.
- R4. One harmonize mini-turn for the family, not one per file. The model returns `harmonize(frames) -> frames` (dict in, dict out): pure, renames and reorders columns to a canonical schema, adds absent columns as all-null, never drops a column that carries data.
- R5. Verification, host-generated and run ungated: every frame has an identical column list afterwards; row counts per slice are unchanged; and for every mapped column, the non-null count is preserved or explained. A rename that silently drops values fails.
- R6. The harmonizer becomes a skill by P2's ordinary path, with one difference: its `metadata` carries the confirmed mapping as JSON, and `fix(df, columns)` applies the mapping to a *single* frame — so it replays on file 22 without re-deriving anything, and it fits the existing skill contract rather than inventing a second one.
- R7. After harmonizing, `/clean-family` runs the P1 CLEAN flow per slice against the harmonized frames, reusing `_skill_attempt` unchanged. Findings that recur across slices are the point: the same skill fires N times and its track record compounds.
- R8. Family reports: one `CleanReport` per slice plus a family summary naming the mapping, the slices harmonized, and the per-skill hit counts.

## Acceptance criteria

- AC1. `/clean-family "data/vancouver/property-tax-20*.csv" tax` loads N slices with correct delimiters and reports drift between the 2006-2010 era (29 columns, no `note`) and the current era (30 columns).
- AC2. One gated harmonize turn produces frames with identical column lists; R5's verification passes; a mapping that drops a populated column fails it and reverts.
- AC3. The harmonizer is admitted as a skill whose metadata carries the mapping, and re-running on an unseen slice applies it with no model call.
- AC4. Running the per-slice CLEAN over ≥3 harmonized slices fires at least one P2 skill more than once, and its ledger shows successes across ≥2 distinct sources.
- AC5. `data/vancouver/` originals are unmodified (sha256 unchanged); every output is a copy with lineage.
- AC6. P0/P1/P2 suites stay green.

## Priority

P2.5, week 3. This is the demo that makes the thesis visible: one confirmation, 4.25M rows, and a library that got stronger while doing it.

## Deliberately deferred

Interactive fuzzy-cluster mapping review (P3 UX) · cross-slice deduplication · incremental re-harmonizing when a new year is published · families whose slices disagree on units rather than columns.
