# Phase 3 core diffs for review (2026-09-02)

Three propose-diffs into Aarmen-owned files (`prompts.py`, `loop.py`). Nothing
here is applied; each block is the exact change, ready to accept, edit, or
reject. Rationale is one paragraph each — sources: the P3 roadmap items
("plain-English paragraph on the answer card", "charts on answers") and the
approved prime-agent backlog §4.

## 1. prompts.py — plain-English paragraph in the answer (P3 table stakes)

Genie-style trust for non-technical readers: the answer explains what the code
did and what the checks verified, in words. The card already carries the
answer text, so this lands on every card with zero host changes.

```diff
 <answer>
 Your final answer to the user's question, in plain language with the key numbers.
+Then ONE short paragraph starting "How this was computed:" saying, in plain
+English, what the executed code did and what the assert checks verified —
+for a reader who will never open the code.
 </answer>
```

(Optionally mirror one line into `FORCED_ANSWER_PROMPT`: budget-exhausted
answers should still say what ran.)

## 2. prompts.py — the two prime-agent doctrines (backlog §4)

Appended to SYSTEM_PROMPT's Rules, adapted to data work:

```diff
 - Answer only from what actually executed. If you could not verify something,
   say so in the answer.
+- Never hold a cell open waiting: no time.sleep loops, no polling, no
+  blocking reads. Long work is not a query-mode job — if a computation
+  cannot finish inside one cell's timeout, say so in the answer instead.
+- The kernel copy is the workbench, not the source of truth: when the
+  question is about a system the dataset came from (a database, an API),
+  answer about the loaded snapshot and say it is a snapshot — never install
+  packages or reach for the network to interrogate the origin.
```

CLEAN_PROMPT already forbids network/sampling; no change needed there.

## 3. loop.py — `Session.peek()`: the bounded export that unlocks Answer.plot()

The system prompt already requires the final cell to bind `result`, but the
card carries text outputs only, so `crivo.ask()`'s Answer has no data to
chart and query.py has no public way to fetch any (the kernel client is
Session-private — correctly). Smallest addition that keeps every boundary:
one public, bounded, read-only export on Session. The wrapper then builds
`Answer.plot()` host-side; no raw rows enter any prompt (the export goes to
the caller's process, same trust position as `crivo.read`).

```python
# loop.py, class Session — proposed addition
PEEK_ROWS_CAP = 200

def peek(self, name: str = "result", rows: int = 50) -> dict:
    """Bounded, read-only export of one kernel variable for host-side
    rendering (charts on answer cards). Never enters a prompt. Caps rows
    at PEEK_ROWS_CAP; returns {"kind": "frame"|"series"|"scalar",
    "data": <records/list/value>, "truncated": bool} or raises KeyError
    if the name is not in the kernel namespace."""
    rows = min(rows, PEEK_ROWS_CAP)
    code = (
        f"import json as _j\n"
        f"_v = {name}\n"
        f"print(_j.dumps(_export(_v, {rows})))"  # _export: tiny helper cell,
    )                                            # installed at bootstrap
    ...  # execute via the existing kernel client, parse the single stdout line
```

If preferred, the alternative is a bounded `result` payload written into the
card by the loop at answer time — richer, but it changes the card schema;
peek() changes nothing existing and stays optional.

With peek() accepted, the wrapper side (query.py, Claude-owned) is:
`Answer.plot()` renders the exported table in the `charts.py` idiom (bar for
categorical aggregates, line for time-indexed, single-value stat for
scalars), returns the Axes, raises a clear message when the result shape
isn't chartable. Tested against the faux provider like test_ask.py does.

## Also queued behind these (no review needed, noted for transparency)

- query.py `_required_key()` currently hardcodes deepseek/claude; once the
  generic `openai` provider lands it will be rewired to require
  CRIVO_BASE_URL/CRIVO_MODEL instead of a key for that provider (wrapper
  file, Claude-owned).
