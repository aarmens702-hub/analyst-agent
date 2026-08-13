# P3 Trust Layer — Provenance DAG · Intent Gate · Browser UI

- **Status:** written retroactively (2026-08-12), after the work shipped. CLAUDE.md asks for a WRAP spec before a multi-day feature and I built this straight from the brief instead. Recording it now so the requirements are reviewable and the acceptance criteria are checkable, but the ordering was wrong and this document does not pretend otherwise.
- **Upstream:** P0 answer card (code + checks + lineage) and the suspendable generator loop; P1 clean reports; P2 skill origins. Sources: VeriGraph (arXiv 2606.16603) for provenance-as-graph, Talk Less Verify More (arXiv 2601.00224) for the intent gate.
- **Decisions taken during the build:** the graph is derived from artifacts already on disk rather than recorded separately · the two ways trust fails are reported as different answers · the intent check never streams and never re-solves the problem · the browser UI drives the same generators as the REPL, so `loop.py` changes not at all.

## What (WRAP)

Three things that let someone else believe an answer. A provenance graph that says whether a claim is reachable from raw bytes with passing checks the whole way; an intent check that catches correct code answering the wrong question; and a browser UI that drives the existing session generators so the loop stays UI-agnostic in fact, not just in principle.

## Requirements

- R1. `provenance.build(session_dir) -> {nodes, edges}` reads cards, clean reports, and lineage sidecars already written by P0–P2. No new recording: provenance that depends on remembering to log it is provenance you find out you lack at the worst moment.
- R2. Node kinds: `source` (raw file + sha256), `variable`, `fix`, `output`, `claim`. Edges are `derived_from`. Only a *verified* fix advances the chain; a skipped or failed one appears in the graph but the next step hangs off the last step that held.
- R3. `trust(dag, node) -> {trusted, reason, path}`. Trusted means reachable from a `source` **and** no node on the path has `checks_passed is False`. The reason distinguishes "not reachable from any raw file" from "a step did not pass its checks" — collapsing them discards the part the reader needed.
- R4. `to_markdown` renders a claim's chain with a per-step mark, and names the skill behind a fix when one produced it.
- R5. REPL gains `/why [id]`.
- R6. Intent gate: after an answer is drafted and before the card ships, one call restates what the executed code computed and returns `match|mismatch` with a reason. It never re-solves the problem and never judges code quality.
- R7. The intent call does not stream. Rendering it as model output would put the checker's words in the analyst's mouth. An unavailable check yields no verdict rather than a passing one — silence is not assent.
- R8. A mismatch renders on the card above the fold, next to the answer it undermines, and sets `flags.intent_mismatch`.
- R9. `app.py` drives `run_turn`/`clean` via `st.session_state` + `gen.send`, holding the live generator across reruns. All logic that does not call `st.*` is a pure function so it can be tested without a browser. `loop.py`, `events.py`, and `repl.py` are unchanged by it.

## Acceptance criteria

- AC1. A card with passing checks over a loaded file is trusted; one with no lineage is untrusted for being unreachable; one with a failing check is untrusted for that reason, and the two reasons differ in the output.
- AC2. A cleaned parquet's chain names every verified fix behind it, and a reverted fix does not appear in the chain while still appearing in the graph.
- AC3. `/why` prints the chain from real session artifacts with nothing recorded specifically for it.
- AC4. Scripted: a restatement naming a different column than the question sets `intent_mismatch` and appears above `**cells**` on the card; a matching restatement leaves the flag false.
- AC5. `pump()` advances a generator to the next gate and returns rather than blocking; a decision resumes it. Tested without a running Streamlit server.
- AC6. P0/P1/P2 suites stay green.

## Status against those criteria

AC1–AC6 all covered by tests (`tests/test_provenance.py`, `tests/test_loop.py`, `tests/test_card.py`, `tests/test_app.py`, `tests/test_repl.py`), and the DAG has been run over real sessions. Not done: the brief's clickable branching lineage UI — `/why` prints a chain, and the browser view of it is deferred.

## Deliberately deferred

Rendering the graph in the browser · cross-session provenance (the graph is per session) · an intent gate on cleaning fixes, where the finding already states intent.
