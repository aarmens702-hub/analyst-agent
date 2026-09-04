# Adoption roadmap — model breadth + shareable reports (planning note)

2026-09-03, owner-directed. Two gaps vs the popular tools (pandas-ai,
ydata-profiling, Great Expectations) that we will close after Phase 4 ships.
This is a plan, not a build; each item gets its own WRAP spec before code.

## A. Model breadth

**What.** The analyst currently speaks DeepSeek and Anthropic through the one
`generate()` seam in `llm.py`. "My key is OpenAI" or "I run local models"
currently means "cannot use the analyst at all" — the hardest adoption wall we
have, and the cheapest to remove.

**Shape.** Two providers behind the same seam, no framework:

- OpenAI-compatible chat endpoint (covers OpenAI, Groq, Together, vLLM,
  LM Studio — one client, `OPENAI_API_KEY` + optional `OPENAI_BASE_URL`).
- Ollama local (covers the no-key-at-all crowd; pairs naturally with the
  keyless story: keyless checks, local analyst, cloud analyst — three rungs).

**Acceptance sketch.** Same eval prompt set answered through each provider in
CI-recorded smoke (cassette or skip-if-no-key); README quickstart lists the
env vars; the bench agent lane runs against any provider via env alone.

## B. Shareable report artifact (+ the graphs)

**What.** ydata-profiling wins hearts with one emailable HTML file. crivo's
diagnosis lives in terminal/notebook cards. A standalone `report.to_html()`
would compete directly, and the receipts make ours different: not just
distributions, but what is broken, the grade, what was fixed, and the proof.

**Shape.**

- `DiagnoseReport.to_html(path)` — self-contained file (inline CSS/JS, no
  CDN), sections: dataset profile, the findings with grades (safe / check /
  person), per-column detail, and the receipts for anything cleaned.
- Graphs embedded: the existing `.plot()` data-quality chart plus per-column
  distribution sparklines; the charts the agent already saves onto answer
  cards (`charts.py`) reuse the same rendering.
- Same artifact from CLEAN runs: `summary.to_html()` with before/after and
  the verification trail — the shareable version of `/why`.

**Acceptance sketch.** One command on a messy CSV yields one file a
non-technical teammate can open; renders with no network; every number on it
traces to a check.

## Also noted (no commitment yet)

- Declarative user-authored expectations ("this column is never negative")
  graded and verified like native checks — the Great Expectations muscle.
- Warehouse-scale validation (Snowflake/BigQuery pushdown) — Phase-7-adjacent,
  parked.

## Priority

After P4.2 publish and the agent-mode bench land: A first (days, removes an
adoption wall), then B (about a week, creates the shareable artifact both
halves lack). Neither renumbers the existing phase plan.
