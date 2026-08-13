# P2 Skill Harness — Packaging · Admission · Retrieval · Governance

- **Status:** approved (design session 2026-08-12)
- **Upstream:** P1 CLEAN mode (specs/2026-08-11-p1-clean-mode-design.md) — fix records (R12), verification (R9), gate surface (R11) all reused unchanged. Skill format, retrieval library, and governance mechanisms: build-research PARTS 1 and 4; brief §"the five properties".
- **Decisions locked in session:** harness only, family mode deferred to P2.5 · fixes generalise at proposal time, not at authoring time · the admission gate runs against the real case, the shipped test is synthetic · a skill runs unattended only when its disease is AUTO *and* its track record is proven · promotion requires successes on more than one dataset · retrieval keys on disease id, not embeddings.

## What (WRAP)

A verified fix currently dies with the session. P2 gives it a life: after a `/clean` run, each model-authored verified fix is rewritten as a column-general `fix(df, columns)` function, gated into the library by an execution that reproduces the original case plus a human's yes, then retrieved and re-applied on later runs — silently once it has earned that, always under the same verification P1 demands. Skills that keep working get promoted; skills that stop working get retired. The library is the compounding asset; the ledger is the audit trail.

## Requirements

**Skill format (skills.py — Claude-owned plumbing)**

- R1. Layout `skills/fix-<slug>/` containing `SKILL.md`, `scripts/fix.py`, `scripts/test_fix.py`. The directory name equals the `name` frontmatter field.
- R2. `SKILL.md` frontmatter carries **exactly** the six spec fields: `name` (1–64, `[a-z0-9-]`), `description` (1–1024, what + when), `license`, `compatibility`, `metadata` (string→string), `allowed-tools`. Any seventh top-level field hard-fails claude.ai packaging, so the validator rejects it. Disease id, spawning case, and skill version live inside `metadata` as strings. Body under 500 lines.
- R3. `scripts/fix.py` exposes `fix(df, columns) -> df`: pure, copies its argument, never mutates. The P1 fix contract generalised over which columns it targets.
- R4. A validator in `skills.py` enforces R1–R3 on every write. `skills-ref validate` stays an optional external conformance check, documented but not a dependency.

**Birth and admission (R5–R9)**

- R5. Proposal candidates are fix records with `status == "fixed"` **and** `origin == "model"`. A fix applied by an existing skill never spawns a new skill — this is the depth-1 recursion cap, and it is enforced by the flow, not the prompt.
- R6. One model call per candidate, after the run's summary, off the critical path: `SKILL_PROMPT` receives the finding JSON, the specific fix source, and the profile slice for its columns; it returns the generalised `fix(df, columns)`, a synthetic `test_fix.py`, and the description. Malformed replies get the P0 nudge treatment; two revision attempts, then the candidate is dropped.
- R7. **Admission gate 1 — the real case.** During each fix mini-turn, immediately after verification and before *P1 R10*'s baseline refresh, the flow captures a frozen slice from `_clean_backup` (the pre-fix frame) to `workspace/<session>/skill_cases/<slug>.parquet`: every row the detector flagged, capped at 150, plus 50 sampled unflagged rows. Admission re-runs the *generalised* fix on that slice and requires both halves — the disease's detector clears, and the unflagged rows are byte-identical. A fix that heals the sick by harming the healthy is refused.
- R8. **Admission gate 2 — the shipped test.** `scripts/test_fix.py` must pass. Both gates execute **inside the sandboxed kernel**, never on the host: this is model-authored code meeting real data.
- R9. **Admission gate 3 — the human.** A `GateRequest` whose title names the skill, its disease, and the case that spawned it. `run` admits, `skip` discards, `reject` returns the note for one revision. No LLM judge appears anywhere in R7–R9.

**Library and governance (library.py)**

- R10. `skills/ledger.json` records per skill: `name`, `disease`, `state` (`probation | proven | retired`), `uses`, `successes`, `failures`, `datasets` (source sha256 list), `created`, `last_used`, and an append-only `events` list citing transcript event ids. The ledger, not `SKILL.md`, holds every mutable number: `metadata` is string→string, and a score that changes per use would rewrite the skill file constantly.
- R11. Retrieval matches on disease id among non-retired skills, ranked by success count then recency. Each skill record carries a `match` block (disease plus the stats keys that mattered) so a semantic tiebreak can be added later without a rewrite.
- R12. Application policy: `state == "proven"` **and** `grade == "AUTO"` → apply silently, no model call and no gate. Every other combination → a `GateRequest` pre-filled with the skill's code. P1 R9 verification runs in both cases, unconditionally.
- R13. Outcomes. Verified success → `successes++`, dataset sha recorded. Verification failure → P1's revert, `failures++`, and the finding falls back to a model-authored fix in the same run. Human rejection at the gate → `failures++`.
- R14. Transitions, thresholds in one constants block: probation → proven at ≥3 successes across ≥2 distinct dataset shas with no failure in the last 5 uses. → retired on 2 consecutive verification failures, or on eviction when the active library exceeds `ACTIVE_CAP = 50` — evicting the lowest `score = successes - failures`, ties broken by least recently used. Retiring moves the folder to `skills/retired/<name>/` and excludes it from retrieval; the ledger row survives.

**Surface and records (R15–R17)**

- R15. `clean()` gains a retrieval step before each fix mini-turn and a proposal pass after the summary. QUERY mode is untouched.
- R16. No new event types. Skill review reuses `GateRequest`/`GateDecision`, so the REPL and P3's Streamlit render it unchanged. REPL gains `/skills` (list: name, disease, state, successes/failures) and `/skills show <name>` (SKILL.md plus ledger row).
- R17. P1 fix records gain `origin: "model" | "skill:<name>"`. The clean report counts skill-applied fixes separately, and lineage names the skill behind each fix.

## Acceptance criteria

- AC1. Round trip: a verified fix from a real `/clean` becomes a skill folder that passes the R4 validator, and its `test_fix.py` passes inside the kernel.
- AC2. Compounding, measured: a skill born on one Raha dataset is retrieved and applied to a *different* one. The assertion is that `generate()` is never called for that finding and the clean report shows `origin: skill:<name>`; wall-clock against the same run with an empty library is the demo number, reported but not asserted.
- AC3. Admission is real: a generalisation that damages unflagged rows fails gate 1 and is never written under `skills/`.
- AC4. Policy, via scripted decisions: probation always gates; proven + AUTO applies silently; proven + HUMAN still gates.
- AC5. Failure path: a skill application that fails verification reverts, scores a failure, and the model authors a fresh fix for that finding within the same run.
- AC6. Governance: two consecutive verification failures retire a skill — folder moved, excluded from retrieval, ledger intact. Cap eviction is covered by a test with `ACTIVE_CAP` monkeypatched low.
- AC7. Depth-1 holds: a fix applied by a skill produces no skill proposal.
- AC8. P0 and P1 suites stay green, and every generated `SKILL.md` has exactly six top-level frontmatter fields.

## Priority

P2, weeks 2–3. Blocks P2.5 (family-file cleaning + the disease-20 harmonizer, whose value is that it becomes a skill) and P3's trust layer.

## Ownership

Claude builds all of it under the standing P0/P1 delegation, confirmed this session — including `library.py` and the `clean()` changes that CLAUDE.md reserves as core. Flag anytime to switch back to propose-diffs.

## Deliberately deferred

Semantic retrieval via model2vec — added when two proven skills first tie on one disease, not before (R11 keeps it additive) · workspace/user skill tiering · family-file cleaning and the harmonizer (P2.5) · skill export and fixture scrubbing (P4) · autonomy grades as policy for *model-authored* fixes, which have no track record to earn it — in P2 grades govern only skill application · explicit dedup, subsumed by retirement per Ratchet.
