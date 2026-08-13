# P5 Usability and Reach — Consequence at the Gate · Surviving Death · Reaching Data

- **Status:** proposed (design session 2026-08-13), following a read of PrimeIntellect-ai/prime-agent (MIT, TypeScript, 15k★) by two research agents plus direct reading of its docs and source.
- **Upstream:** P0–P4 shipped. This phase adds nothing to the cleaning engine; it makes what exists usable by someone who is not its author, and reaches data that is not a local CSV.
- **Framing:** prime-agent is a daemon-backed, 20-provider, multi-client coding platform. Most of its infrastructure answers questions a single-user local data tool does not ask. This spec takes the three patterns that answer questions we *do* ask, adds the two reach items the brief already promised, and records what was rejected and why — the rejections are the more useful half.

## The finding that reordered this spec

prime-agent **does not gate execution at all.** No `requireConfirmation`, no `autoApprove`; the only confirm call sites in the repository are a doc example and one extension confirming session actions. Their trust model is *preview, fast undo, bound the blast radius* — they say plainly that the kernel is "not a security sandbox... use a disposable clone."

So our gate is a genuine differentiator rather than table stakes, and the correct thing to take from them is their **rendering of consequence**, not their philosophy. They became excellent at showing what happened precisely because they cannot ask first. We ask first and show nothing — which is the worst of both.

## What (WRAP)

Make the gate answer the question it actually poses ("what will this do to my data?"), stop misreporting infrastructure death as cleaning failure, let a session survive its terminal, and let data arrive from somewhere other than a local file — without loosening a single guarantee the provenance graph depends on.

## Requirements

**Correctness first — this is a live defect, not a feature (R1)**

- R1. `kernel_died` and `hung` are handled inside `_exec_events`, not only in `run_turn`. Today the check sits at one call site reached via `_execute_cell`; the eight CLEAN-mode paths (`_fix_mini_turn`, `_skill_attempt`, `_harmonize`, `_freeze_case`, `_admit`, `_write_cleaned`, `_snapshot_baseline`, `_replay_mapping`) call `_exec_events` directly and branch only on `status != "ok"`. Reproduced: with a kernel dead on every cell, a `/clean` run reports *"sentinel-missing: failed (3 attempts)"* per finding and never mentions the kernel. A dead substrate must abort the run and say so, not exhaust `CLEAN_MAX_ATTEMPTS` and blame the model. The clean report gains a terminal status distinguishing "we could not run" from "the fix did not work."

**Consequence at the gate (R2–R4)**

- R2. A `diff` module renders a change so a person can see it: unified hunks, absolute row identifiers, dimmed context, and **word-level highlighting inside a changed value** so `'12.0 oz'` → `'12.0'` shows only what moved. `difflib.SequenceMatcher` supplies the primitive; no dependency, no port. It is a standalone renderer usable anywhere an old/new pair exists.
- R3. Before a gate decision is requested, the proposed cell runs against a **scratch copy** of the target variable and the result is rendered as a data diff: which columns changed, how many cells, a truncated before/after sample, an explicit count of untouched columns, and any row-count or dtype delta. Small changes collapse to one line; anything non-trivial expands by default. The preview is discarded — it never becomes the applied state, and a preview that errors is reported as such rather than blocking the gate.
- R4. The same renderer serves skill admission: a human approving a skill sees its effect on the frozen case, not only its source.

**Transparency (R5–R7)**

- R5. Startup prints what is actually in effect: model and provider, sandbox mode, and the skill library by state — including **how many skills are proven and will therefore run unattended on AUTO-grade findings**. A new user currently cannot discover that the agent will silently modify their data without typing `/skills` and knowing what "proven" means. For a project whose claim is that everything is checkable, that is the wrong default.
- R6. Errors render as one line (`ExceptionType: message`) with the full traceback available on request. `repl.py` prints `event.text` unconditionally today, so a pandas traceback floods the terminal.
- R7. A clean run ends with a scannable per-column rollup — what moved and by how much — sourced from the `stats` the report already carries.

**Surviving death (R8–R9)**

- R8. A best-effort kernel-namespace snapshot: walk the user namespace, serialise each top-level variable independently, skip anything unserialisable or over a size cap **rather than failing the whole snapshot**, write atomically, and report as one JSON line — the same stdout-marker idiom `detect_all` and `_freeze_case` already use. Taken after each verified fix. Restore is the mirror, per-name and best-effort. `_restart_and_replay` currently replays only the original load cells, so every fix applied since is lost.
- R9. `Session.resume(session_id)` rebuilds `history`, `datasets`, and `loads` by replaying `transcript.jsonl`, then starts a fresh kernel and restores R8's snapshot. `Transcript.__init__` already reopens an existing log and continues its event numbering — that behaviour is written and unused. Prerequisite: `_exec_events` must log a cell's stdout/stderr alongside its return value, or replay is not faithful. No daemon, no socket, no protocol: a resumed session is a new process reading an old log.

**Reach (R10–R12)**

- R10. `llm.py` gains the Claude half of the seam it was designed for. `generate(messages, model) -> Iterator[str]` is unchanged; a provider switch selects the client, and `model_info()` stops hard-coding `"provider": "deepseek"`. Two providers, no registry — the brief's plan is smaller than prime-agent's provider machinery, not bigger.
- R11. Remote ingestion arrives as **kernel-resident Python functions**, never as a parallel tool channel. `tax = load_s3("s3://…")` is a gated code cell, logged, and provenance-tracked exactly like `read_csv`. This is also how prime-agent does MCP — their integrations are packages the kernel imports, not new agent tools — so the pattern is validated rather than invented. A tool the model can call outside a cell would produce an action with no code, no gate, and no lineage node, and `/why` would report the resulting claim as trusted because it cannot see what it does not know.
- R12. Remote lineage records more and claims less. `data/` originals are immutable and their sha256 is a fact; an S3 object can be overwritten and a SQL result differs tomorrow. A remote node therefore stores the URI or exact query, the fetch time, the row count, and **a content hash of what actually arrived**, and `/why` says "trusted as of this fetch, content hash X" rather than implying a reproducibility it cannot guarantee. Re-fetching to a different hash is information, not an error.

**Compaction (R13)**

- R13. `self.history` is pruned when it grows past a threshold: keep a recent-turn budget verbatim, summarise older turns into one structured block via `_generate_scoped(msgs, stream=False)` — the primitive the intent check already uses — and always keep the `<dataset variable=…>` profile blocks intact. Scope is narrower than "add compaction": `clean()` already builds throwaway per-finding message lists and the registry is recomputed per call, so only QUERY turns accumulate. Today one oversized history makes **every** subsequent turn in that session fail identically until the process is killed.

## Acceptance criteria

- AC1. A kernel killed mid-`/clean` produces a report naming the kernel death, not N findings marked `failed`. Tested with a fake client returning `kernel_died`.
- AC2. At a gate, the operator sees changed columns, changed cell counts, sampled before/after values, and untouched-column count, for both QUERY and CLEAN. A preview that raises degrades to the code-only gate and says why.
- AC3. The preview never mutates the live variable: after a rejected gate, the frame hashes identically to before.
- AC4. Startup names the model, the sandbox mode, and the count of proven skills that may run unattended.
- AC5. A kernel killed after three verified fixes, then restarted, restores those fixes rather than replaying only the load.
- AC6. `--resume <session>` rebuilds a session whose `/why` chain still resolves and whose datasets are loaded.
- AC7. The same clean run completes against both providers, and the card's `model_info` names the one used.
- AC8. A remote-loaded table produces a lineage node carrying URI, fetch time, row count, and content hash; `/why` marks it trusted-as-of rather than immutable.
- AC9. A QUERY session past the compaction threshold continues to answer instead of failing every turn, and the dataset profile survives compaction verbatim.
- AC10. P0–P4 suites stay green.

## Ownership

Split as CLAUDE.md requires. **Claude:** the diff renderer (R2), startup manifest and error rendering (R5–R7), the snapshot routine (R8), the Claude provider (R10), ingestion functions and their tests (R11). **Aarmen's core, propose-diffs only:** the kernel-death handling in `_exec_events` (R1), the preview's placement in the gate flow (R3), resume (R9), the lineage contract for non-immutable sources (R12), and compaction policy (R13) — every one of these touches the loop, the trust model, or both.

## Deliberately rejected

Each of these is something prime-agent ships and we should not copy.

- **Their no-gate trust model.** Preview plus undo suits a coding agent working in a git checkout. Our gate is the product, and cleaning has no `git checkout .` for a dataframe.
- **Daemon, resident workers, attach/detach, session leases.** Solves multi-client, multi-tenant, survive-the-terminal for a platform. We have one user and one kernel, and R9 buys the only part that bites. It also collides with `--network none`: a reattachable channel would need `docker exec` multiplexing into a container-resident daemon, which is infrastructure, not glue.
- **Goals, heartbeats, schedules, autonomous mode.** These exist so an agent works unsupervised. Every cell we run is gated and every skill is human-admitted; unattended continuation contradicts the trust claim rather than merely costing complexity.
- **The extension/plugin system.** ~2,600 lines for third-party authors around a many-tool agent. We have one tool and no third-party authors. What it does validate is R11: their MCP integrations are kernel-imported packages, not registered tools.
- **A provider registry and custom-streaming contract.** Built for 20+ providers, OAuth, and reconciling native tool-calling shapes. R10 is two providers with no tool-calling in play.
- **Session tree, fork, clone.** For exploring alternate approaches side by side. Our sessions are linear, and data-level undo already exists: originals are immutable and transforms write copies with lineage.
- **Async pub-sub loop, `AbortSignal` threading, parallel tool execution.** Serves N attached clients and concurrent independent edits. We have one driver and one order-sensitive kernel — concurrency here would be actively wrong, not merely unnecessary.
- **A rich TUI.** Their `packages/tui` is a rendering framework: differential redraw strategies, synchronised output, an overlay/focus/IME system. CLAUDE.md rules out frameworks, and our data is tabular — a Styler-highlighted dataframe in the browser is both cheaper and more honest than ANSI art. The terminal stays deliberately plain and gets R2 rendered simply.

## Deferred within P5

Mid-turn interrupt (aborting one slow cell instead of the session) is a real gap — `except KeyboardInterrupt` currently kills `run_repl` entirely — but wiring kernel interrupt through the supervisor is multi-day core work and belongs in its own spec. A cost/context footer waits until the `generate()` seam is known to return usage. Parallel per-slice cleaning in family mode is the one place subagents would genuinely fit, and waits until family mode has users.

## Attribution

Everything above is pattern-level and reimplementable from the idea. The one exception is R8: if the snapshot/restore function bodies are adapted from prime-agent's `kernel/state-snapshot.ts` rather than written fresh, MIT requires the licence notice retained — a source comment citing the file.

## Priority

P5, after P4. R1 is not P5 work and should not wait for this spec to be approved: it is a defect in shipped behaviour, it is reproducible, and it makes the tool lie about why a run failed.
