# MCP wrapper (v1) — analyst-agent as a tool for other agents

Approved 2026-08-14 after brainstorm. Scope ruling: local stdio server now,
built publishable-shaped (PyPI + registry listings are packaging work later,
not redesign). Architecture ruling: wrapper now — the inner agent keeps
writing fixes — with the toolkit ("verified hands for any agent") named as v2.

**LANDED 2026-08-14.** Built by two forked agents in parallel (server +
tests; docs + skill) with the parent finishing the registration layer after
the implementation fork stalled at 90%. Ported to MCP SDK 2.0 (MCPServer,
not 1.x FastMCP). All four ACs green, 338 tests passing, and the live stdio
handshake verified: six tools listed over the wire, diagnose_file returning
real findings through a spawned server.

## What (WRAP)

A stdio MCP server, `analyst-agent-mcp`, exposing analyst-agent to any MCP
client (Claude Desktop, Claude Code, Cursor). It is a **driver** like
`repl.py`: it drives `Session` and `run_clean_once`, and touches no core code.

## Requirements

- R1. `src/analyst_agent/mcp_server.py` on the official `mcp` Python SDK
  (FastMCP), console entry `analyst-agent-mcp` in pyproject. Stdio transport.
- R2. Stateless tools, each call independent:
  - `diagnose_file(path) -> str` — `diagnose.report(path, as_json=True)`,
    keyless, read-only.
  - `clean_file(path, name=None, policy="auto") -> dict` — `run_clean_once`
    under the grade policy. The tool description must state that
    `policy="all"` approves judgement-grade changes and requires explicit
    human consent relayed by the calling agent.
- R3. Stateful session tools over a persistent kernel in the server process:
  - `open_data(path) -> {session_id, variable, profile}`
  - `ask(session_id, question) -> answer card dict` — drives `run_turn`;
    QUERY gates are auto-approved: the calling agent is the operator (the
    `--auto-run` trust position; sandbox and card checks stand).
  - `why(session_id) -> str` — provenance markdown from `provenance.build`.
  - `close_session(session_id) -> bool`
- R4. Session registry: ids map to live Sessions (subprocess kernels, never
  docker); sessions idle past `IDLE_S` (30 min) are evicted on the next tool
  call; server shutdown closes everything.
- R5. Keys are the client's env (`DEEPSEEK_API_KEY` / `ANALYST_PROVIDER` +
  `ANTHROPIC_API_KEY`); key-needing tools fail with a message naming the
  missing key; `diagnose_file` never needs one.
- R6. Errors return as tool results (message + report path when one exists),
  never as protocol crashes: one bad file must not kill the server.
- R7. Sessions run `preview=False` (no human reads a gate preview here) and
  workspace defaults under the server's cwd, overridable by
  `ANALYST_WORKSPACE`.

## Acceptance criteria

- AC1. Tool functions pass tests against SessionLike doubles (repl idiom):
  clean_file relays `needs_human`, ask returns a card with checks, policy
  "all" reaches the driver only when passed explicitly.
- AC2. One real-kernel test: `open_data` on a real CSV, `ask` with a stubbed
  LLM returns the card, `close_session` shuts the kernel.
- AC3. The server exposes exactly the six tools with docstrings a calling
  model can act on (in-memory MCP client lists and calls them).
- AC4. Suite stays green; server module imports without the `mcp` package
  only failing at entry, not at `analyst_agent` import time.

## Not v1 (recorded so it is a choice)

- MCP elicitation for GATE approvals (v1.5): the server asking the human
  through the client, per gate, replacing blanket deferral.
- v2, the toolkit: expose load/detect/submit-fix/verify/govern as primitives
  so the *outer* model writes fixes against these verification rails. One
  model instead of two; gates via elicitation; the differentiated product.
- Publishing motion: PyPI, README snippet flip to `uvx`, listings on the
  community servers repo, Smithery, PulseMCP. Packaging, not redesign.
- Resources/prompts MCP surfaces; multi-client state; anything hosted.

## Ownership

The server is a driver: Claude's, wholly. No `loop.py` changes exist in this
design; if implementation discovers one is needed, it comes back as a
proposal first.
