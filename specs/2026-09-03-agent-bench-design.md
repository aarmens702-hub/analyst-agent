# Agent-mode bench — WRAP spec

2026-09-03. Positioning rule: crivo is an AI analyst first, so the storefront
number must include the analyst, not only the deterministic baseline. This is
the P4.1/Phase-6 lane for the agent half.

## What

A headless bench lane that drives the real agent CLEAN loop (`Session.clean`,
auto-approved gates) over the same corpus the deterministic bench uses, scores
the cleaned frames with the same `score_end_to_end`, and reports the agent's
column next to the deterministic baseline. New code lives in `bench/` only;
`src/crivo/` is untouched (the loop already supports auto-approving drivers,
loop.py R3 note).

## Requirements

- R1 **Same instrument.** Cases come from `corpus.SMOKE` / `corpus.full_corpus()`
  via `corpus.build(entry)`; scoring is `score_end_to_end(pristine, dirty,
  cleaned, truth)`. No agent-specific scoring path, or the columns are not
  comparable.
- R2 **Faithful hand-off.** The dirty frame reaches the agent as a parquet file
  (dtype- and value-exact) loaded through `session.load`, so the agent faces
  exactly the frame the baseline faced, through the product's real front door.
- R3 **Auto-approve, eyes open.** Gates are answered `GateDecision("run")`;
  `preview=False, snapshots=False` (nobody reads them headless). The kernel is
  the dev subprocess by default; `--docker` opts into the sandbox image.
- R4 **Cost and hang caps.** `--sample N --seed S` picks a deterministic subset;
  an event cap per case (default 500) and a wall-clock cap per case abort the
  case, record `status: aborted`, and move on. A run never dies to one dataset.
- R5 **Resume-safe.** One JSON per case under `bench/results/agent/`; existing
  files are skipped unless `--force`. Aggregation reads whatever is on disk, so
  a run can be stopped and continued.
- R6 **Terminal first, storefront later.** Like the deterministic bench: sampled
  runs print to the terminal only. RESULTS.md / README get an agent column only
  from a full, spec-blessed run.
- R7 **Key from the project's own .env**; refuse to start without one, naming
  which vars were checked. Model choice is whatever `crivo.llm` resolves.

## Acceptance

- `uv run python -m bench.agent_run --sample 2` completes on a machine with a
  key: two JSONs on disk, per-case detection/repair F1 printed, plus means.
- Re-running skips completed cases; `--force` reruns them.
- An aborted case (cap hit) leaves a JSON with `status` and the run continues.
- `uv run pytest` stays green (the lane imports nothing that breaks keyless CI;
  the module is import-safe without a key).

## Priority

Now: it is the receipt for the published "AI analyst" positioning. Full-corpus
run and RESULTS.md integration are a follow-up decision (cost: LLM calls × up
to 1450 cases; the publishable number can be a stratified sample + the 4
external sets, stated as such).
