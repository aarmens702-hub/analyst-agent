# A2.1 experiment: notes-truncation on deepseek-v4-flash

2026-09-04. One case, one run per model, governed arm. Result JSONs:
`bench/results/agent/notes-truncation.json` (pro baseline, aborted) and
`notes-truncation.flash.json`. n=1: directional, not conclusive.

| | v4-pro | v4-flash |
|---|---|---|
| outcome | aborted at 600s and again at 900s, incomplete | completed traversal in 656s |
| model calls | 8 before abort | 5 total |
| avg model wait per call | ~107s | ~131s |
| gates | run 1 / skip 1 at abort | run 3 / skip 2 |
| net fixes surviving verification | n/a (incomplete) | 0 (no cleaned frame written) |

## Readings

1. Flash was not faster per call here (131s vs 107s). Both models pay the
   same dominant cost, which points at prompt size and thinking time, not
   model weight class. The latency levers are therefore fewer calls
   (plan-first, A1) and cached prefixes (A0 R5), ahead of model choice.
2. Flash finished the traversal in fewer calls but landed zero fixes that
   survived verification: it ran 3 fix gates and every applied fix was
   reverted by the re-check. The verifier protected the dataset from the
   cheaper model exactly as designed.
3. Consequence for A2.2 routing: repositioned as a cost lever, not a latency
   lever, and the escalation path (still-firing check re-routes that finding
   to pro) is mandatory, not optional. This case is the existence proof.
4. The next instrumented run (CRIVO_TELEMETRY set, landed in d766ef5) will
   decompose per-call wait into input-processing vs generation via usage and
   cache counters, replacing this inference with numbers.
