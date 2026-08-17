# Master roadmap — analyst-agent to a usable, adopted library

Consolidates everything discussed 2026-08-15: the pandas-ai competition strategy
(C+A), the usability levers, the ingestion gap, and the reach/platform bets, into
one phased plan. Supersedes `2026-08-15-pandas-ai-roadmap.md` (its phases live on
as tracks below).

**North star:** `pip install analyst-agent` gives a developer a library that
(1) cleans their data trustworthily with receipts — our wedge, (2) reads from
wherever their data lives, (3) answers questions with charts — the draw, and
(4) feels native in a notebook. Verification and lineage are the throughline
nobody else has.

**Where competitors are:** pandas-ai (~18k stars) = fast ask-my-dataframe with
charts, joins, connectors, model-agnostic, but zero verification or audit trail.
Julius/Code Interpreter = broad + instant, zero receipts. Great Expectations/dbt
= warehouse-scale quality but you write every rule. Our gaps to close, in order:
one-call clean (unique), one-call ask + charts (theirs), ingestion breadth,
notebook delight, a published benchmark.

---

## Phase 1 — Transformative install (the wedge + delight)

The single most important phase: make `pip install` pay off in one line, on the
thing nobody else does well. **DONE 2026-08-17** — the whole phase shipped; see
`specs/2026-08-16-notebook-output-design.md`.

- P1.1 `aa.clean(df) -> (cleaned_df, CleanSummary)` — deterministic fixes for
  AUTO-grade diseases (numbers-as-strings, sentinels, whitespace, constant
  columns, dup rows, single-format dates), no LLM, each verified via `detect_one`
  and reverted if it does not clear; GATE/HUMAN deferred to `summary.needs_review`.
  Input never mutated. **✓ done**
- P1.2 **Notebook-native output** — `_repr_html_` on `Report` and `CleanSummary`:
  a self-contained dark "verified ledger" card (inline-only, survives
  GitHub/nbconvert), colored by grade, with before→after samples on a clean.
  **✓ done** (`notebook.py`).
- P1.3 **pandas accessor** — `df.aa.diagnose()`, `df.aa.clean()`, registered on
  import with the non-idempotent-warning guard. **✓ done** (`accessor.py`).
- P1.4 **Styler before/after** — folded into the clean card's inline diff, plus
  `summary.diff()` returning a highlighted pandas Styler (opt-in; needs jinja2).
  **✓ done**.

## Phase 2 — Ingestion everywhere (data lives in more than files)

One `aa.read(source)` that dispatches on extension **or** URI scheme, keeping the
sentinel-safe/sniff/encoding discipline throughout. Heavy connectors are optional
extras (`pip install analyst-agent[sql,cloud]`).

- P2.1 Files, expanded: + `.gz/.zip/.bz2` compression, parquet directories,
  `.feather`, `.orc`.
- P2.2 Databases via SQLAlchemy: Postgres, MySQL, SQLite, SQL Server, DuckDB —
  `aa.read("postgresql://…", query=…)`.
- P2.3 Cloud object storage: `s3://`, `gs://`, `az://` (via fsspec) — extends the
  existing `load_url` content-hash lineage to these.
- P2.4 Google Sheets (public + service-account).
- P2.5 Warehouses as optional extras: Snowflake, BigQuery.
- P2.6 REST/JSON APIs: `aa.read("https://…json")` with a records path.
  Every remote source keeps R12's trusted-as-of lineage (URI, fetch time, hash).

## Phase 3 — Query parity + charts (pandas-ai's turf, verified)

- P3.1 `aa.diagnose(df).plot()` — a data-quality overview figure (matplotlib,
  no heavy dep).
- P3.2 `aa.ask(df, "question") -> Answer` — a one-liner over the query loop,
  auto-run, returning the answer + code + executed checks + lineage. The
  `df.chat()` equivalent, but verified.
- P3.3 Chart output on `ask` — when the question implies a chart, return the figure.
- P3.4 Multiple dataframes / joins in one `ask`.
- P3.5 Model-agnostic: an OpenAI provider behind the `generate()` seam (we have
  DeepSeek + Claude). **OpenRouter is the concrete path** — it is an
  OpenAI-compatible endpoint, so this is one provider (`base_url` +
  OpenRouter key) that unlocks every model, plus fallback routing. Ship it as an
  *optional* provider only: native DeepSeek/Claude stay the default trust-path,
  because routing adds a party to the prompt hop (pin no-logging/provider prefs)
  and can weaken native prompt-caching economics — both documented, not waved
  away.
- P3.6 Speed pass — the library calls must feel instant next to `df.chat()`.

## Phase 4 — Proof + distribution

- P4.1 Benchmark on the Raha dirty/clean pairs: precision/recall of `aa.clean`
  vs. truth, and vs. a pandas-ai baseline where reproducible. **Publish the
  number.** `scripts/score_fixes.py` is the seed.
- P4.2 Publish to PyPI (pick the license — the one blocker), flip the README
  install line to `pip install analyst-agent`.
- P4.3 Demo GIF (recorded terminal: diagnose → clean catching a bad fix).
- P4.4 List on MCP registries (community servers, Smithery, PulseMCP, Glama) +
  a docs site (mkdocs) with recipes.
- P4.5 A GitHub Action — `diagnose --json` as a CI gate that fails a PR on data
  quality regression. Drop-in distribution.

## Phase 5 — Reach: more than a data-analyst agent

The bigger strategic bets, each worth its own go/no-go on Phase 1–4 evidence.

- P5.1 **v2 toolkit** — "verified hands for any agent": expose detect/verify/
  govern as MCP primitives so the outer model writes fixes against our rails.
  Spec: `2026-08-15-v2-toolkit-design.md`.
- P5.2 **RAG grounding layer** — position and package analyst-agent as the
  clean-verify-ground step before data feeds a RAG system; a documented
  integration + example.
- P5.3 **Agent messaging / extensibility** — let other agents call and compose
  analyst-agent (the "act as more than a data agent" ask); scope after v2.

---

## Ownership

Library, ingestion, charts, notebook output, accessor, benchmark, distribution
are Claude's (plumbing over existing seams). `aa.ask` wraps the agent query loop;
the wrapper is Claude's, but any change to the loop's trust/gate semantics is
propose-diffs. v2/RAG/agent-messaging are strategic — Aarmen's calls, spec-first.
License and PyPI/registry submissions are Aarmen's decisions on a written runbook.

## Sequencing note

Phase 1 is highest-leverage and mostly standalone — it makes the library
transformative on install, which nothing else does. Phase 2 (ingestion) and
Phase 3 (charts/ask) are parallel-friendly once Phase 1's `clean` + notebook
output exist. Phase 4 is gated only on a license decision. Phase 5 waits on real
adoption signal from 1–4.
