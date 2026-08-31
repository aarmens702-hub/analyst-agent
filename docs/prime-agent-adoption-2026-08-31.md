# Prime-agent adoption backlog (approved 2026-08-31)

Four workstreams Aarmen approved after the full teardown of
PrimeIntellect-ai/prime-agent (MIT). File references are into their repo at
commit `c382f09`. Each item lands only with tests, per the house rules; the
prompt items are propose-diffs (prompts are Aarmen's).

## 1. Hardening quick wins (do first, small)

- **Audit our kernel output clipping for head-cut.** Their bug: a blunt
  64KB head slice means a failing run shows the build header and cuts the
  assertion failure at the end. Correct shape is head + rolling tail, drop
  the middle (their `bash.py` `_BoundedBuffer`: first 512KiB + 1.5MiB tail).
  Check `kernel/supervisor.py` and `loop.py`'s clipping and fix to head+tail
  with a named truncation marker.
- **Port the context-overflow detector.** `packages/ai/src/utils/overflow.ts`:
  ~20 provider-specific patterns each documented with a real example string,
  exclusion patterns (throttling is not overflow), and two silent-overflow
  detections (oversized input accepted; input truncated with output=0).
  Adapt for DeepSeek/Anthropic into `llm.py` error classification.
- **Credential quarantine for tests.** Their `test.sh`: move auth aside
  (restore via EXIT trap), unset every provider key var, then run the suite —
  so a key in the shell can never produce a falsely green run. Ours: a small
  wrapper or conftest fixture asserting no provider env leaks into keyless
  tests. Generate the var list from one source of truth (their mistake:
  two hand-maintained lists drifted).
- **Faux provider.** `packages/ai/src/providers/faux.ts`: a fake LLM behind
  the same seam, scriptable per test. Ours: a `generate()` double registered
  behind the same signature in `llm.py`, so loop tests are deterministic and
  free. (We stub today; a first-class faux provider makes gate/retry paths
  testable.)

## 2. Kernel snapshot revival

Upgrade `snapshot.py` + `kernel/client.py` with their design
(`prime-agent-runtime/src/rlm/repl.py` snapshot/restore,
`src/core/kernel/state-snapshot.ts`):

- Per-variable dill pickling: one unpicklable object (open file, socket) is
  skipped and reported, never aborts the snapshot.
- Caps: 16MiB per variable, 256MiB aggregate; atomic tmp+rename write with a
  JSON manifest.
- On resume: restore before bootstrap so live handles beat stale pickles;
  inject a model-visible note listing restored and failed names.
- Their compaction-prompt trick, adapted: after context summarization, tell
  the model its kernel variables survived and to record the names worth
  reusing.

## 3. Engineering practices

- **AGENTS.md operating contract** for agents working in this repo: exact
  single-test invocation, a NEVER-run list (Docker on this Mac, full-suite
  in tight loops), the guard-pause/tdd-guard notes, parallel-agent git rules
  (no `git add -A`, no reset/stash in shared trees). Model: their 254-line
  AGENTS.md.
- **Issue-numbered regression tests**: `tests/regressions/<issue>-<slug>.py`
  naming for bugs found by review passes, so the ledger is browsable.
- **Changelog fragments** (later, pre-1.0): per-PR `.changes/*.md` folded at
  release; CI fails a src-touching PR without one.

## 4. Prompt doctrines (propose-diffs to prompts.py, Aarmen decides)

- **Never hold the turn open polling**: start long work, record the handle,
  end the turn; no `time.sleep()` loops. Pairs with giving `bash`-like cells
  a handle API if we ever need long profiling jobs.
- **The REPL is not the universe**: evaluate an external system through its
  own interface; never install a project's deps into the kernel to force it
  to import there; failures from the native environment are the result.
  Adapted to data work: the dataset's own source (DB, API) is authoritative,
  the kernel copy is the workbench.

## Explicitly not adopting

- Their trust model (no sandbox, no approval gates) — ours is the product.
- Schema-only skill admission — our test+human gate stays.
- Raw tool output in compaction summaries — our no-raw-rows rule stays.
- A curl|sh installer — pip is our installer (setup decision: option A,
  wizard + config only; see the setup design spec when written).
