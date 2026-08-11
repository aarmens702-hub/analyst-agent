# P1 CLEAN Mode — Detection Engine · Fix Loop · Verification · Lineage

- **Status:** approved (design session 2026-08-11)
- **Upstream:** P0 core (specs/2026-08-09-p0-core-design.md) — protocol, loop, card all unchanged by P1 except where R11 says so. Disease taxonomy + detection signals: build-research PART 2.
- **Decisions locked in session:** host drives the fix checklist, model authors fixes · every fix gated with autonomy grade displayed (grades become policy in P2, advice in P1) · two-layer verification (template invariants + model asserts) · fixes are `fix_<slug>(df) -> df` functions from birth.

## What (WRAP)

`/clean <var>` turns the agent from answering over messy data to fixing it: a deterministic 22-signal diagnosis, then one gated fix mini-turn per finding (model-authored pure functions), each verified by re-running the detector plus invariants with automatic revert on failure, producing a cleaned parquet copy + lineage sidecar + clean report — while `data/` originals stay untouched. Plus a dev-only Raha scoring harness that turns fixes into precision/recall numbers.

## Requirements

**Detection (detect.py — kernel-side, pandas + stdlib only)**

- R1. `detect_all(df, name) -> {"findings": [...], "clear": [...]}` runs every implementable signal from the 22-disease table on every call. Finding schema: `{disease: int, slug, columns: [str], evidence: str (counts + truncated samples, never rows), stats: dict, grade: "AUTO"|"GATE"|"HUMAN", confidence: float, indicator: bool}`. The `clear` list names signals that ran and found nothing — absence is a checked claim.
- R2. Diseases 12 (cross-field/FD contradictions) and 15 (statistical outliers) are indicators: `indicator: true`, reported with evidence, never given fix turns — enforced by the flow, not the prompt.
- R3. Disease 20 (schema drift across family files) is detectable only across multiple files; P1 ships the detector behind a `detect_family(dfs: dict) -> findings` entry point but `/clean` (single-variable) does not invoke it. Family-mode cleaning is P2 scope (the harmonizer skill). All other 21 signals run in P1.
- R4. Each detector is re-runnable scoped to a target (`detect_one(df, disease, columns) -> clear|finding`) — the same code is verification layer 1. Detector and verifier can never drift because they are one function.

**CLEAN flow (Session.clean(var) — a generator speaking the P0 event language)**

- R5. Sequence: (1) run `detect_all` in-kernel, ungated, logged; print the diagnosis report (numbered findings with grades + evidence; indicators listed under "flagged, not fixed"). (2) Snapshot baseline in-kernel: row count, per-column `pd.util.hash_pandas_object` digests, and `_df_backup = df.copy()`. (3) One mini-turn per non-indicator finding, taxonomy order. (4) Summary + outputs (R12–R14).
- R6. A mini-turn's model context is scoped: CLEAN system prompt + this finding's JSON + profile slice for its columns + registry. The model must reply with one `<execute>` cell that defines `fix_<slug>(df) -> df` (pure: copies, never mutates its argument), applies it (`var = fix_<slug>(var)`), and ends with 1–3 fix-specific asserts (layer 2). Malformed/missing-function replies get the P0 nudge treatment.
- R7. ≤ 3 model attempts per finding. At cap the finding is recorded `failed` and the flow moves on — one stubborn disease never stalls the run. Terminal per-finding statuses: `fixed | skipped | failed`.
- R8. Gate per fix: `GateRequest.title` carries `fix {i}/{n} · {slug} · {grade} · conf {c} · {evidence}`. Actions: `[r]un`, `[j]eject` + note (demand a better fix — returns as observation, retry), `[s]kip` (record skipped, next finding). `--auto-run` runs every fix (dev only).
- R9. Verification, immediately after each fix cell, as a host-generated verify cell run ungated (our code, logged): layer 1 = target detector re-runs clear + row count unchanged (except diseases 9/10/21, whose templates assert the *expected* row delta from the finding stats) + untouched-column hashes equal baseline. Layer 2 already ran inside the fix cell (its asserts). Verify failure → kernel-side restore from `_df_backup` → failure fed back as observation → retry counts against R7's cap.
- R10. `_df_backup` refreshes after each *verified* fix (the baseline walks forward); hashes recompute for changed columns only.
- R11. Surface extensions, all backward-compatible: `GateRequest` gains `title: str = ""`; `GateDecision` accepts action `"skip"`; REPL gains `[s]` at the gate and a `/clean <var>` dispatch; `SessionLike` protocol gains `clean(var) -> Generator`. Kernel protocol, transcript kinds, and QUERY behavior unchanged.

**Records, outputs, lineage**

- R12. Per finding a fix record: `{finding, status, attempts, fix_source (the function text), model_asserts, verify: {signal_clear, rows, untouched, layer2}, transcript_evs, elapsed_s}`.
- R13. On completion, write `workspace/<session>/cleaned/<var>.parquet` (pyarrow — dtype-preserving, so parsed dates stay dates) and `<var>.lineage.json`: source path + sha256, ordered fix records, before/after shape and null counts, session id, event chain. `data/` is never written — verified by the source file's sha in AC4.
- R14. A clean report `workspace/<session>/clean_reports/r<NNN>.{json,md}`: fixed/skipped/failed/flagged counts, per-finding one-liners, indicator evidence, citing transcript event ids exactly as cards do.

**Prompts & scoring**

- R15. `CLEAN_PROMPT` mirrors P0 discipline (one tag, slices never dumps) plus the fix-function contract and the pure-function rule. Disease-specific guidance travels in the finding JSON, not prompt bloat.
- R16. `scripts/score_fixes.py`: cell-level diff of a cleaned output against a Raha `clean.csv` → precision / recall / F1 of changed cells, per dataset. Dev-only; never wired into the agent flow (private habit, per the brief).
- R17. Deps: `pyarrow` added; the Docker image must be rebuilt (`uv run pytest -m docker` re-run) since detect.py rides into the container.

## Acceptance criteria

- AC1. Every detector has unit fixtures (synthetic + Raha slices). Integration: `detect_all` on Raha beers finds units-in-values (1) and sentinel N/A (4); on hospital finds case/spelling (7); on flights finds date-format diseases (2/3) and flags contradictions (12) as indicator. Exact per-file expectations live in the tests.
- AC2. Live E2E: `/clean` on Raha beers (dirty) runs ≥ 2 fix mini-turns with titled gates; `[s]kip` works; verified fixes recorded; run completes with a summary.
- AC3. Verify-failure path (scripted model in tests): a fix that damages an untouched column → hash invariant fails → revert restores the dataframe → retry observation contains the failure.
- AC4. After `/clean`: cleaned parquet + lineage sidecar exist; the source file's sha256 is unchanged; lineage event ids resolve in the transcript.
- AC5. Indicators appear in report with evidence; zero fix turns were generated for them.
- AC6. Full P0 regression suite stays green (QUERY untouched).
- AC7. Loop tests drive `clean()` with scripted decisions + fake client/generate covering: fixed, skipped, rejected-then-fixed, failed-at-cap.
- AC8. `score_fixes.py` yields P/R/F1 for at least one Raha dataset from a real `/clean` output.

## Priority

P1, weeks 1–2. Blocks P2 (skills are packaged verified-fix records; the harness needs R12's shape).

## Ownership

Claude (plumbing): detect.py + all detector tests, events/repl extensions, score_fixes.py, pyarrow/dep + image rebuild, verify-cell builder. Core (loop `clean()`, CLEAN prompt, records/report): built by Claude per the standing P0 delegation — flag anytime to switch back to propose-diffs.

## Deliberately deferred

Honoring autonomy grades as policy (P2, once fixes have track records) · saving fixes as SKILL.md skills (P2) · family-file cleaning + harmonizer (P2) · provenance DAG + intent gate (P3) · clustering-assisted canonical-mapping UX for diseases 7/10 (in P1, HUMAN-grade findings get ordinary fix mini-turns like every other finding — the gate is the human step — but the model proposes only simple deterministic fixes; interactive fuzzy-cluster mapping review is P2).
