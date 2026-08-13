# P4 Distribution — Export · Ablation · Write-up

- **Status:** written retroactively (2026-08-12), after the work shipped. Same admission as the P3 spec: the WRAP document should have come first.
- **Upstream:** P2 skill harness (the library being exported and measured). Positioning: the brief's competitive section, contrasting EvoDS (KDD 2026) — skills admitted on "ran once, used ≥3 times", no tests, no retirement.

## What (WRAP)

Make the library portable, make the governance claim measurable, and write down what the project actually does — including what it costs. Three artifacts: an exporter that produces a shareable bundle, a simulation that turns "governance matters" into a number with its own caveats attached, and a document that cites both.

## Requirements

- R1. `scripts/export_skills.py` exports `proven` skills by default. `--include-probation` and `--all` widen selection; `--include-retired` is required separately and is never implied by `--all`. An unknown state selects nothing rather than guessing.
- R2. Scrubbing: `metadata.born_from` reduces to a basename (or is dropped under `--strict`), the same path is replaced where it appears in the body, and any remaining absolute path anywhere in the rendered SKILL.md refuses the skill outright rather than rewriting prose. Every scrub is reported per skill — silent scrubbing is worse than none.
- R3. Export conformance is stricter than storage: exactly the six spec fields, no files beyond `SKILL.md` and `scripts/`, nothing over 1 MiB. All reasons are reported together, not just the first.
- R4. `MANIFEST.json` records name, disease, state, successes/failures, and a sha256 of the bytes actually written. Types are normalised — the ledger holds `disease` as an int and metadata as a string, and a manifest reporting whichever it read is one a consumer must guess at.
- R5. `scripts/ablate_governance.py` simulates a skill population against the real `Library` and against an EvoDS-style regime implemented locally (admit after N uses, never retire, no cap). It must not modify `library.py`.
- R6. The population includes skills that *rot* — start reliable, then degrade — because that is the failure mode retirement exists for.
- R7. Seeded and reproducible, since the output is meant as evidence. Both regimes consume the identical outcome stream, so the comparison is of rules rather than luck.
- R8. The run states its own limits in its own output: assumed rates on a synthetic schedule show the rules behave as designed on a plausible shape; it is not a measurement of real skills.
- R9. `docs/what-this-is.md` explains the three claims and cites measured numbers, including the ones that complicate the story.

## Acceptance criteria

- AC1. Default export takes only proven skills; retired ones need the explicit flag even under `--all`; `--dry-run` writes nothing.
- AC2. A skill carrying a machine path exports with it scrubbed and the scrub reported, and one with a seventh frontmatter field is refused with the reason given.
- AC3. Manifest types are stable regardless of which source supplied them.
- AC4. Same seed reproduces identical results across separate processes.
- AC5. A rotted skill is retired under governance and never under the ungoverned rule; the ungoverned library grows unbounded while the governed one respects `ACTIVE_CAP`.
- AC6. The write-up cites real numbers and states the cost of governance as well as its benefit.

## Status against those criteria

AC1–AC5 covered by tests (`tests/test_export_skills.py`, `tests/test_ablate_governance.py`) and by real runs, including an export of a skill the live pipeline actually produced. AC6 was initially failed and then fixed: the first draft cited only favourable numbers, and the per-archetype breakdown — governance keeps 74% of reliable throughput, 29% of rotted, and just **8%** of merely-mediocre — was added after being verified independently.

**Not done: real users.** The brief's P4 is "classmates / r-datasets / BC open data community", and nothing here substitutes for it. Every number in the write-up comes from the author's own runs, and the document says so.

## Deliberately deferred

Publishing a bundle anywhere · tuning the governance thresholds, which stay placeholders until real usage exists to tune them against · an ablation driven by real recorded outcomes rather than a simulation.
