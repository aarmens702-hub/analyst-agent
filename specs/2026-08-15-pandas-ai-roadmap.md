# Roadmap: competing with pandas-ai (strategy C + A)

Decided 2026-08-15. pandas-ai (~18k stars) is "ask my dataframe": wrap a frame,
ask in English, get a number / chart / transformed frame. Fast, broad,
model-agnostic, connectors — and it trusts generated code blindly with no audit
trail and no systematic data-quality work. Our wedge is exactly that gap.

Strategy: **C then A.** Lead with the differentiator (deterministic cleaning,
verified, with receipts — the thing pandas-ai does badly), then extend the query
side to match their breadth (charts, joins, connectors) so we show up where they
are strong too. C is the foundation A builds on; the ordering is one arc, not
two plans.

## What (WRAP)

A `pip install analyst-agent` library that both **cleans your data
trustworthily** (our moat) and **answers questions with charts** (their draw),
where every result carries executed checks and lineage.

## Phases (each ships independently; each ends green + a real demo)

**Phase 1 — the deterministic clean (the wedge).**
- R1. `aa.clean(df, policy="auto") -> (cleaned_df, CleanSummary)`: apply the
  mechanical fixes the detectors already imply — strip/collapse whitespace,
  coerce numbers-as-strings to numeric, sentinels -> NaN, drop constant
  columns, parse single-format dates — with **no LLM and no kernel**. Only
  AUTO-grade diseases auto-fix; GATE/HUMAN are reported in `summary.needs_review`.
  Every fix is verified the same way the agent verifies (the detector re-runs
  clean, untouched columns unchanged), and reverts if not. This is the unique
  thing pandas-ai has no answer to.
- R2. `CleanSummary`: what changed per column, what was left for a human, and a
  `.to_dict()`/`.to_json()`. The cleaned frame is the deliverable; the input is
  never mutated.

**Phase 2 — proof.**
- R3. A benchmark harness over the Raha dirty/clean pairs (`scripts/score_fixes.py`
  exists): precision/recall of our deterministic clean vs. the truth, and vs. a
  pandas-ai baseline where reproducible. Publish the number in the README. This
  is what converts skeptics; a claim without it is a story.

**Phase 3 — charts (adoption table-stakes).**
- R4. `aa.diagnose(df).plot()` -> a data-quality overview (per-column
  finding/clear heat, missingness). Static matplotlib, no new heavy dep.
- R5. Chart output on the query path (Phase 4): when a question implies a chart,
  return the figure alongside the answer card.

**Phase 4 — the light query one-liner (the drawing card) + A breadth.**
- R6. `aa.ask(df, "question") -> Answer`: a library one-liner over the existing
  query loop, **auto-run** (no gate ceremony — the checks attach as receipts
  instead of blocking), returning the answer, the code, executed checks, and
  lineage. This is the pandas-ai `df.chat()` equivalent, but verified.
- R7. Multiple dataframes / joins in one `ask` (pandas-ai has this).
- R8. Connectors: `aa.read_sql` exists; add documented SQL/cloud paths and an
  OpenAI provider behind the existing `generate()` seam (we have DeepSeek +
  Claude; OpenAI is trivial and model-agnosticism is table-stakes).
- R9. Speed pass: the library `ask`/`clean` must feel instant next to
  `df.chat()`; profile and cut ceremony where verification allows.

**Phase 5 — distribution.**
- R10. Publish to PyPI (pick the license — the one blocker), flip the README
  install line, list on the MCP registries, record the demo GIF.

## Acceptance criteria

- AC1. `aa.clean(df)` on a frame with planted diseases returns a frame the
  detectors pass, an untouched input, and a summary naming what was deferred.
- AC2. A published benchmark number on Raha, reproducible from a script.
- AC3. `aa.diagnose(df).plot()` renders without a display and saves to a path.
- AC4. `aa.ask(df, "...")` returns a verified answer in one call, no REPL.
- AC5. Two dataframes joined in one ask; an OpenAI provider run; a SQL source.
- AC6. On PyPI, `pip install analyst-agent` then the 30-second demo works clean.
- AC7. Existing suite stays green throughout.

## Deliberately not competing on

- Their hosted platform/UI (we're a library + MCP server, not a SaaS).
- Warehouse scale — still one in-memory kernel; the honest ceiling, its own
  future spec (DuckDB/chunked path) if a user demands it.
- General analysis breadth beyond tabular Q&A + cleaning.

## Ownership

Phase 1-2 (`aa.clean`, benchmark) and Phase 3 (charts) are library/plumbing —
Claude's. Phase 4's `ask` wraps the agent query loop, which is Aarmen's core:
the wrapper is Claude's, but any change to the loop's trust/gate semantics is
propose-diffs. Phase 5 (license, PyPI, registries) is Aarmen's decisions on a
written runbook.

## Honest effort

~6-8 weeks equivalent. Phase 1 is the highest leverage and mostly independent of
the rest — it makes the library transformative on install, which nothing else on
this list does.
