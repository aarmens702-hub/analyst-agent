# P0 Core Design — Turn Loop · Wire Protocol · Answer Card

- **Status:** approved (design session 2026-08-09)
- **Ownership:** Aarmen hand-writes `loop.py`, `prompts.py`, `llm.py`, `card.py`. Claude builds `kernel/client.py`, `kernel/supervisor.py`, `kernel/bootstrap.py`, `transcript.py`, `profile.py`, `repl.py`, Docker, tests.
- **Upstream (locked, not reopened here):** design spec v1.1 in `~/Desktop/mailo/coop-project/analyst-agent-brief.md` + `analyst-agent-build-research.md` — kernel-as-only-tool, `--network none` Docker with stdio supervisor, schema/profile/samples never raw dumps, one `generate()` seam (DeepSeek default, Claude flag), recursion cap depth 1.
- **Companion:** shareable brief at https://claude.ai/code/artifact/9ae8c196-cb2a-407f-b00e-4784bc6cc61b

## What (WRAP)

The three hand-written pieces of P0's query skeleton: the agent **turn loop**, the **NDJSON protocol** between the loop and the in-container kernel supervisor, and the **answer card**. P0 exit: load a CSV as a kernel variable, ask a natural-language question, watch gated code execute with live output, receive an answer card carrying code + passed checks + lineage.

## Requirements

**Turn loop**

- R1. A turn is a bounded agentic loop, `MAX_ITERS = 6`. Each iteration the model emits exactly one of `<execute>…</execute>` or `<answer>…</answer>`. Repair is not a mechanism: a traceback is an ordinary observation and the loop continues. At the cap, one forced-answer call (prompt demands `<answer>`); the card gets `flags.capped = true`.
- R2. Malformed model output (both tags, neither tag, or nested): the first per turn triggers a corrective reprompt that does not count toward `MAX_ITERS`; each subsequent one consumes an iteration. If the forced-answer call is malformed, retry once; if still malformed, the card ships with `answer` = raw model text and `flags.malformed_answer = true`.
- R3. Tags parse from plain text (regex for one tag pair), identically for DeepSeek and Claude through the `generate(messages) -> token stream` seam. No provider tool-calling.
- R4. Every model-authored cell passes the gate: `[r]un` executes; `[j]eject` prompts for a one-line note that returns to the model as an observation. A `--auto-run` session flag skips gating (dev use). `/load` cells are host-templated, run ungated, and are still transcript events.
- R5. `run_turn()` is a UI-agnostic **generator**. It yields typed events; drivers render and answer them. Gate decisions arrive via `gen.send(GateDecision)`. The P0 driver is `repl.py`; the P3 Streamlit app is a second driver over the same loop (its rerun model requires suspend/resume, which the generator provides). Drivers contain no model or kernel logic.
- R6. Session state: model messages, latest registry, per-dataset profiles, and an append-only `transcript.jsonl` with monotonic integer event ids. Cards cite event ids; later memory/skill edits must too.
- R7. Context assembly per `generate()` call: system prompt (role, tag protocol, gate semantics, mandatory final-cell asserts, slices-never-dumps) + always-present registry block + dataset profiles + session history. Each observation is truncated to ~2,000 chars (head + tail marker) in context; the transcript keeps protocol-capped fullness.
- R8. Kernel death mid-session: loop reports it, offers restart + automatic replay of `/load` cells only. Full provenance-replay re-hydration is P3.

**Wire protocol (v1, `proto: 1`)**

- R9. NDJSON over `docker exec -i` stdio (macOS + `--network none` has no TCP path). Requests carry a client-assigned `id`; every event echoes it. Invariant: **every request terminates in exactly one `ev:"result"`; only `execute` emits intermediate events** (`stream`, `display`). Events for different ids may interleave; the client dispatches by id. At most one `execute` in flight; only `interrupt`/`restart`/`shutdown` are legal alongside it.
- R10. Ops: `hello` (readiness barrier; returns `proto`, python/ipykernel versions), `execute(code, timeout_s=120)`, `interrupt`, `restart` (re-bootstraps; returns `registry: []`), `shutdown`.
- R11. Result statuses: `ok · error · interrupted · timeout · hung · kernel_died · bad_request`. The supervisor never crashes on malformed input; unparseable requests get `status:"bad_request"` (with `id: null` if the id itself is unreadable).
- R12. Every `execute` piggybacks a registry probe via `user_expressions`; the result carries a fresh registry (≤ 50 entries + `omitted` count; underscored names, modules, callables, and IPython internals skipped). Entries hold raw facts only: `name, type, shape|len, mem_mb`. The **loop** derives provenance: a name is stamped with the creating cell's event id when it is new or its `(type, shape)` changed versus the previous registry.
- R13. Timeout escalation. Supervisor: at `timeout_s`, auto-interrupt; 10 s grace; if the interrupt bites → `status:"timeout"` (streamed output already delivered); if not → `status:"hung"`, after which further executes return `hung` immediately and only `restart`/`shutdown` are useful. A client-issued `interrupt` op that bites yields `status:"interrupted"` instead — the loop can tell "took too long" from "user aborted". Client: an outer deadline of `timeout_s + grace + 30` with no result line means the supervisor itself is dead → kill the container, surface `kernel_died`.
- R14. Supervisor-side truncation caps, flagged in `result.truncated` and always draining the kernel to idle: 64 KiB total stream text per cell; 8 KiB `value` (head + tail); ≤ 4 display images ≤ 2 MiB b64 each (excess dropped with a stub event); tracebacks first 5 + last 30 lines; ANSI stripped everywhere.
- R15. Images travel b64 inline; the client decodes to `workspace/<session>/artifacts/cell_<exec_count>_<i>.png` and hands paths upward.
- R16. A bootstrap cell runs at kernel start and after every restart: `%matplotlib inline` + Agg, pandas display caps, and the registry probe function (underscore-prefixed).
- R17. Supervisor internals (Claude's side, protocol-invisible): filter every iopub message by `parent_header.msg_id`; completion = matching `status: idle` plus the shell `execute_reply`.

**Answer card**

- R18. The prompt requires the final `<execute>` cell to end with 1–3 plain `assert`s on the result. Checks are lifted by AST-parsing the final `status:"ok"` cell — code that ran is the only source of a ✓; a failed assert is an ordinary `error` observation and the loop iterates. No card ships with zero checks.
- R19. Card schema (§3). Persisted per answer as `workspace/<session>/cards/c<NNN>.json` + a human-readable `.md`; the REPL renders the `.md` form. `lineage` = dataset path + sha256 + variable + `event_chain` (transcript ids question → card).
- R20. Schema is frozen in P0; phases upgrade the *fill*: P1 replaces assert authoring with task-aware generation; P3 grows `lineage` into the provenance DAG and adds the intent gate. Field names chosen so that upgrade adds keys, never renames.

## Acceptance criteria

- AC1. `uv run python -m analyst_agent` starts the REPL; `/load data/<file>.csv [name]` creates the kernel variable, runs the profiler, prints a profile summary; both logged as transcript events.
- AC2. An NL question yields a gated cell; `[r]` executes with live streamed output; `[j]` + note produces a visibly revised next cell.
- AC3. A cell that raises (e.g., wrong column name) leads to an unprompted model retry via the traceback observation, within the iteration cap.
- AC4. Every completed turn ends in an answer card: ≥ 1 passed check lifted via AST from the final ok cell, cells with gate decisions recorded, lineage citing real transcript event ids, persisted as `.json` + `.md`.
- AC5. A chart-producing cell yields a PNG under `workspace/<session>/artifacts/`, referenced by the card.
- AC6. `kill -9` of the kernel process mid-session → loop reports `kernel_died`, offers restart, replays `/load` cells, session continues.
- AC7. A deliberately long cell (`time.sleep(300)`, `timeout_s=5`) ends `status:"timeout"` ≤ ~15 s after the deadline; the session continues without restart. A client-issued `interrupt` during a long cell ends it `interrupted`.
- AC8. `--auto-run` skips all gates.
- AC9. Protocol conformance: a pytest suite drives the real container over stdio with scripted ops (no LLM) covering ok/error/interrupt/timeout/restart/bad_request, truncation flags, and registry piggyback.
- AC10. Streamlit-readiness: a loop test drives `run_turn()` programmatically with scripted `GateDecision`s and a stubbed `generate()`/kernel — proving drivers need only rendering + input.

## Priority

P0, this weekend. Blocks P1 (CLEAN mode reuses the loop unchanged) and P2/P3 (transcript event ids and card schema are load-bearing for skills and the DAG).

---

## 1. Turn loop design

### Events between loop and driver

```python
# loop yields ↓                    driver answers with →
GateRequest(code, iteration)       # gen.send(GateDecision(...))
StreamText(name, text)             # gen.send(None) — render live
ArtifactSaved(path)                # gen.send(None)
Notice(kind, text)                 # gen.send(None) — nudge · cap · kernel_died · restart_offer
CardReady(card)                    # terminal — turn over

GateDecision(action: "run" | "reject", note: str = "")
```

### Algorithm

```python
def run_turn(question, s: Session):
    s.log("user", question)
    iters, nudged = 0, False
    while iters < MAX_ITERS:
        resp = generate(assemble_context(s))
        kind, body = parse_tags(resp)              # execute | answer | malformed
        if kind == "malformed":
            if not nudged:                          # first one is free (R2)
                nudged = True; yield Notice("nudge", ...); continue
            iters += 1; continue                    # later ones consume an iteration
        if kind == "answer":
            yield CardReady(build_card(s, body)); return
        iters += 1
        decision = yield GateRequest(body, iters)   # suspends for driver
        if decision.action == "reject":
            s.observe(f"user rejected: {decision.note}"); continue
        for ev in s.client.execute(body, timeout_s=120):
            if isinstance(ev, StreamOut):   yield StreamText(ev.name, ev.text)
            if isinstance(ev, DisplayItem): yield ArtifactSaved(ev.path)
        s.observe(observation_from(ev))             # ev ends as ExecResult; registry diffed here
    yield Notice("cap", f"iteration cap {MAX_ITERS} hit")
    resp = generate(forced_answer_context(s))       # R2 governs malformed here
    yield CardReady(build_card(s, resp, capped=True))
```

The observation template (what the model sees after a cell): status, truncated `value`, stream tail, trimmed traceback if any, artifact filenames, and a registry delta line (`new: by (DataFrame (9,2))`). Full versions land in the transcript.

### Transcript

`workspace/<session>/transcript.jsonl`, append-only, one JSON object per line: `{ev_id, t, kind, ...}` with `kind ∈ session_meta | user | model | gate | exec | card`. `exec` events store protocol-capped outputs and artifact paths. Event ids are the currency of provenance: cards cite them now; skills and the DAG cite them later.

### Loading

`/load <path> [name]` renders a host-owned template (`pd.read_csv` with `utf-8-sig`, then the profiler), executes it ungated, caches the profile for context assembly. Datasets under `data/` are immutable; all writes go to `workspace/`.

## 2. Wire protocol v1

Transport: container runs idle (`--network none`, `--memory 2g --cpus 1.5 --pids-limit 128 --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp`, `data/` ro + `workspace/` rw mounts); the client attaches `docker exec -i <cid> python -m analyst_agent.kernel.supervisor`. Client tears the container down on exit; stale containers are removed by session label on startup.

### Example exchange

```
→ {"id":7,"op":"execute","code":"...","timeout_s":120}
← {"id":7,"ev":"stream","name":"stdout","text":"..."}            # live, coalesced ~50 ms
← {"id":7,"ev":"display","mime":"image/png","b64":"iVBORw..."}   # or text/plain + "text"
← {"id":7,"ev":"result","status":"ok",
    "value":"report_year\n2019    312500.0\n2023    401000.0",
    "error":null,"exec_count":3,"elapsed_s":1.4,
    "registry":[{"name":"df_tax","type":"DataFrame","shape":[206543,32],"mem_mb":68.4}],
    "truncated":{}}

# interrupt while id 7 runs — events interleave, dispatch by id
→ {"id":8,"op":"interrupt"}
← {"id":8,"ev":"result","status":"ok"}
← {"id":7,"ev":"result","status":"interrupted","error":null, ...}
```

`error`, when present: `{ename, evalue, traceback: [lines]}`.

### Client API (what the loop codes against)

```python
client.start() -> HelloInfo                     # container up · hello ok · proto checked
client.execute(code, timeout_s=120) -> Iterator[KernelEvent]
#   yields StreamOut(name, text) · DisplayItem(mime, path|text) as they arrive;
#   final item is ExecResult(status, value, error, registry, exec_count,
#   elapsed_s, truncated) — PNGs already decoded to workspace/<session>/artifacts/
client.interrupt(); client.restart(); client.shutdown()
```

Iterator (not callback) so the loop generator can re-yield live events to its driver without threads on the hand-written side.

## 3. Answer card

```jsonc
{
  "card_id": "s01-c004",
  "session": "s01",
  "question": "Which zoning district gained the most median land value, 2019 → 2023?",
  "answer": "RS-1 single-family: median land value rose $312,500 → $401,000 (+28.3%) ...",
  "cells": [
    { "event_id": 17, "exec_count": 4, "code": "by = (df_tax[...] ... assert len(result) == 1 ...)",
      "status": "ok",
      "gate": "run",                        // "run" | "auto" | {"rejected": "<note>"}
      "value_preview": "zoning_district\nRS-1    88500.0",
      "display_paths": ["artifacts/cell_4_1.png"] }
  ],
  "checks": [ { "expr": "len(result) == 1", "passed": true } ],
  "lineage": {
    "datasets": [ { "path": "data/property-tax-report.csv", "sha256": "9f3c...41e2",
                    "variable": "df_tax", "loaded_event": 12 } ],
    "event_chain": [12, 14, 15, 17]         // transcript ids, question → card
  },
  "model": { "provider": "deepseek", "model": "deepseek-chat", "temperature": 0 },
  "flags": { "capped": false, "malformed_answer": false, "truncated": false },
  "created": "2026-08-09T21:40:11-07:00"
}
```

- Rejected cells appear in `cells` with their note — steering is part of the record.
- `checks` come only from AST-lifted asserts of the final ok cell (R18). `.md` rendering: answer, checks with ✓, cells with gate chips, code, lineage line, meta line.
- Session ids are sequential (`s01`, `s02`…) per workspace; card ids `c001`… per session.

## 4. Module map

```
src/analyst_agent/
  loop.py             Aarmen   run_turn generator · session state · observations · registry diff
  prompts.py          Aarmen   system prompt · nudge · forced-answer
  llm.py              Aarmen   generate() seam (DeepSeek default, Claude flag)
  card.py             Aarmen   card build · AST check-lifting · renderers
  transcript.py       Claude   append-only JSONL writer · event ids
  repl.py             Claude   P0 driver (rendering + gate input only)
  profile.py          Claude   ~100-line profiler (P0: schema + per-type stats + samples)
  kernel/client.py    Claude   KernelClient · container lifecycle · outer deadline · b64 decode
  kernel/supervisor.py Claude  in-container · jupyter_client · relay · caps · watchdog
  kernel/bootstrap.py Claude   bootstrap cell source (matplotlib · display caps · probe)
docker/Dockerfile     Claude
tests/                Claude   protocol conformance · loop-with-stubs · card/AST · profiler
```

## 5. Deliberately deferred (not P0)

Edit-at-gate · per-cell always-allow (only the session-wide `--auto-run`) · context compaction · standalone registry op · card replay command · intent gate · provenance DAG · skills and governance · Streamlit driver · DB connectors · ydata-profiling integration.
