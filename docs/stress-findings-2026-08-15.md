# Stress + review findings — 2026-08-15

A deliberate attempt to break the system after the parallel-agent build round.
Approach: throw hostile inputs at every surface and assert nothing crashes
(errors must be results, not exceptions); push the detection engine to scale
and watch for hangs; churn the MCP session registry for leaks.

## One real bug found and fixed

**Session chatter corrupted the MCP stdout channel.** `Session.__init__` and
`load()` print progress ("starting kernel", "loaded ...", the profile) to
stdout. In stdio MCP mode stdout *is* the JSON-RPC wire, so a strict client
parses those lines as corrupt messages — observed live as `Invalid JSON:
starting kernel` during a real handshake. The CLI already redirected for the
whole process; the tool paths did not. Fixed: every Session-touching region in
`open_data`/`clean_file` runs under `_quiet()` (`redirect_stdout -> stderr`),
pinned by a test with a double that prints on construct and load, and verified
live (`67b8479`). This is exactly the class of bug integration testing exists
to catch and unit tests structurally cannot.

## Everything else held

**Keyless surfaces (diagnose, detect, MCP error tools) — no unhandled crash.**
Empty file, header-only, ragged rows, unicode headers, a 200k-char single
value, raw binary, missing path, a directory: `diagnose.report` raises on the
genuinely unreadable ones (empty → `EmptyDataError`, binary → `UnicodeDecodeError`,
both `ValueError` subclasses; missing/dir → `OSError`), and **both real callers
contain it** — the CLI via `except (OSError, ValueError)` prints a clean
"could not read <file>: <reason>" with exit 1, the MCP layer via bare
`Exception` returns an `{"error": ...}` dict. No tracebacks reach a user.

**Detection engine at scale — sub-second, no hangs.** 60-column frames, 5k
long near-duplicate strings (d07's capped O(n²)), regex bait (5k-comma
numerics), 100k high-cardinality free text, wide all-money frames, type-soup
columns: all under 0.35s. The duplicate-index quadratic we fixed earlier holds
at 100k rows on one label (0.07s, was >90s before the fix).

**MCP session registry churn — no leaks, isolation held.** Three concurrent
real-kernel sessions kept distinct variables and independent `/why` chains;
double-close returns `False` not a crash; idle backdating + a lookup evicts and
closes the kernels and empties the registry; `ask` on an evicted id returns a
clean error.

## Code review of the agent-written pieces

- **Epoch date-family guard** (`_date_families`): a lone epoch match with no
  sibling date family ≥5% is deleted, so a ten-digit order-id column never
  becomes "dates as strings". Correct on tiny-contamination edges too.
- **MCP elicitation fails-closed** (`_client_can_elicit`): any failure — no
  capability, no context, an import or check that errors — returns `False` and
  degrades to v1 blanket-policy behavior, never a crash.

## Verdict

350 tests green, lint clean, wheel builds, all pushed. One integration bug
found and fixed; every error-containment promise the layers make holds under
hostile input and scale.
