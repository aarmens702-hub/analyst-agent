# P5 core proposals — diffs for Aarmen's side of the line

Everything Claude-owned in the P5 spec is shipped and tested (R1–R2 earlier;
R5–R8 routine, R10, R11 on 2026-08-14; 331 tests green). The five items below
touch the loop, the trust model, or both, so per CLAUDE.md they are proposals
with concrete diffs, not landed code. Each names its tests. The building
blocks they consume already exist and are tested.

---

## R3/R4 — the gate shows consequence, on a sampled scratch copy

**LANDED 2026-08-14** (approved by Aarmen). As proposed, plus one discovery
the proposal missed: a preview executes model code *before* the human
approves it, and the scratch copy protects only the data — a cell that
SIGKILLs the kernel or opens files would do so unapproved (found live when
the sigkill recovery test died inside its own preview). `preview_screen`
now AST-checks the cell first: anything importing beyond
pandas/numpy/re/json-tier modules, calling open/exec/eval, or reaching for
dunders degrades the gate to code-only with the reason named. Previews ride
`GateRequest.preview` at the QUERY, fix, and skill gates; the admission gate
shows the skill's effect on the frozen case; `--auto-run` skips them.
336 tests passing, including live preview cells against the real kernel.

*(original proposal below, kept for the record)*

**Ruling already made** (open-findings D): the preview runs on a *sample*,
never the full frame — CLAUDE.md's preview discipline, and a 300k-row frame
copied per gate is real memory.

**Where:** `_fix_mini_turn` and `_skill_attempt`, immediately before
`yield GateRequest(code, ...)`.

**Diff sketch:**

```python
# loop.py — before the gate
preview = yield from self._preview(var, code, finding)
decision = yield GateRequest(code, iteration, title=title, preview=preview)

def _preview(self, var, fix_code, finding):
    """Best-effort: a preview that errors degrades to the code-only gate."""
    cell = verify.preview_cell(var, fix_code, finding["columns"])  # exists to write
    result, stream, _, _ev = yield from self._exec_events(cell, quiet=True)
    if result.status != "ok":
        return f"(preview unavailable: {(result.error or {}).get('evalue', '?')})"
    return stream  # diff.column_change output, rendered kernel-side
```

`verify.preview_cell` (Claude will write it on approval): bind
`_pv = {var}.head(PREVIEW_ROWS).copy()`, apply the fix function to `_pv`,
compute per-column changed counts and up to 3 before/after pairs, and print
`diff.column_change(...)` — `diff` is already importable in the kernel. The
scratch copy is `_pv`; the live variable is never touched, which is AC3 by
construction rather than by revert.

`GateRequest` gains an optional `preview: str = ""` field; `repl._drive`
prints it under the code box when present. R4 falls out for free: `_admit`'s
gate passes the frozen-case before/after through the same renderer.

**Tests:** AC2 (gate output shows changed counts + samples; erroring preview
degrades with a reason), AC3 (frame hash identical after a rejected gate).

**Tradeoff:** one extra kernel cell per gate (~tens of ms on the sample).
Skippable under `--auto-run` since nobody reads it.

---

## R8 wiring — two call sites

**LANDED 2026-08-14.** Per-fix snapshots (the AC5 version), death-tolerant,
with scripted suites opting out via `snapshots=False`. AC5 green against the
real kernel: a SIGKILL after a verified fix on a frame that was never
file-loaded, restored whole from the snapshot alone.

*(original proposal below)*

The snapshot routine is shipped (`snapshot.py`, tested). Wiring:

```python
# loop.py, in _clean's fix loop, after a rec lands with status "fixed":
if rec["status"] == "fixed":
    yield from self._exec_events(
        snapshot.snapshot_cell(str(self.session_dir / "kernel_state.pkl")),
        quiet=True, tolerate_death=True,   # a failed snapshot must not fail the fix
    )

# loop.py, _restart_and_replay, after the load replay + registry stamping:
state_file = self.session_dir / "kernel_state.pkl"
if state_file.exists():
    for _ev in self.client.execute(snapshot.restore_cell(str(state_file)), ...):
        ...  # stamp registry from the final result, as the replay now does
```

**Test:** AC5 — three verified fixes, kernel killed, restart; the frame in the
new kernel carries all three fixes (real subprocess kernel, like
`test_load_kernel.py`).

**Tradeoff/decision for you:** snapshot-after-every-fix serialises the frame
each time (bounded by the 64MB per-variable cap). Alternative: snapshot once
after the fix loop. The per-fix version is what AC5 promises; the once version
loses at most the tail fixes.

---

## R9 — resume

**LANDED 2026-08-14.** `Session(resume=...)` / `--resume s16`; load events
now carry path/sha/variable so the transcript alone rebuilds the session;
profiles lead the rebuilt history; numbering continues from disk. AC6 green
against the real kernel. QUERY observations deliberately not reconstructed.

*(original proposal below)*

`Session.resume(session_id)` as the spec wrote it: rebuild `history`,
`datasets`, `loads` from `transcript.jsonl` (Transcript already reopens and
continues numbering — written and unused), start a fresh kernel, replay loads,
restore the R8 snapshot. The prerequisite the spec names — `_exec_events`
logging stdout alongside results — should land first and is a two-line
transcript.append payload change. `--resume <id>` in `__main__` is Claude
plumbing once the method exists.

**Test:** AC6 — resume a session, `/why` chain resolves, datasets loaded.

---

## R12 — the remote lineage node

`ingest.load_url` already stamps `frame.attrs["remote"]` with URI, fetch time,
row count, content hash. The provenance side (yours): when `_stamp_registry`
sees a new variable whose frame carries `attrs["remote"]`, the source node is
`remote:` not `src:`, stores that dict, and `/why` renders
*"trusted as of 2026-08-14T09:12Z · content sha256 ab12…"* instead of
implying immutability. Re-fetch to a different hash: a new node, both kept.

**Test:** AC8 against a `file://` URL.

---

## R13 — compaction

As specced: `self.history` past a threshold → keep the last N turns verbatim,
summarise older turns into one structured block via
`_generate_scoped(msgs, stream=False)`, always keep `<dataset …>` profile
blocks intact. Only QUERY turns accumulate, which bounds the blast radius.
Suggested constants: trigger at ~60k chars of history, keep last 8 turns.

**Test:** AC9 — a session pushed past the threshold keeps answering and the
profile blocks survive verbatim.

---

*Say which of these to land (any subset, any order) and each will go in
test-first like everything else this week.*
