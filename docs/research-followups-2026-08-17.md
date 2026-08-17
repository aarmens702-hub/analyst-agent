# Research follow-ups — GitHub deep-dive, 2026-08-17

Backlog surfaced by the three research agents (Strike patterns, Strike
issues-as-gotchas, best-in-class notebook output). These are **not** in the
Phase-1 notebook build (`specs/2026-08-16-notebook-output-design.md`) — they are
adjacent wins and hardening logged here so they are not lost. Ordered by value.

## Adjacent usability (small, wedge-sharpening)

- **`aa.load_example()`** — a bundled tiny messy DataFrame (built in code, no
  file) so the very first command in the README needs zero user data. Makes the
  "First 60 seconds" block copy-paste-real. *Effort S.*
- **`diagnose --json` with documented exit codes** (0 clean / 1 findings / 2
  error) — turns diagnose into a CI data-quality gate. We already have
  `clean --json`; mirror it. *Effort M.*
- **"Why not just ydata-profiling / great-expectations / pandas-ai" section** —
  extend the existing "How it compares" table into prose that names the
  alternatives head-on. Sharpens the wedge (deterministic 22-check + verified
  fixes + provenance). *Effort S–M.*
- **Provider/MCP docs tables** — `provider | default model | env var`, and the
  MCP tool namespace + per-tool contract published explicitly. *Effort S.*

## Hardening (from Strike's real bugs)

- **MCP stdout is a protocol channel** (#216/#264/#793): in server mode force all
  logging/warnings to stderr; add a test asserting a clean tool call emits **zero
  non-protocol bytes on fd 1**. (We already fixed the Session-chatter leak with a
  stdout→stderr redirect; this adds the regression test + maps exceptions to a
  structured `{code, message, retryable}` instead of raw tracebacks.)
- **Recoverable output caps** (#1199): cap what reaches the model *and* the MCP
  transport, but spill the remainder to a session scratch file the agent can
  re-read by offset; truncation marker names the tool + how to re-fetch. Never
  emit an unbounded `df.to_string()` into a prompt/tool result.
- **Versioned + atomic persisted artifacts** (#803): `schema_version` on every
  provenance record, skill manifest, and session log; temp-file + atomic rename
  (never in-place partial writes — matters with a long-lived kernel that can die
  mid-write); reject unknown-newer versions with a clear operator error.
  *Touches the hand-written provenance/skill core → propose, don't implement.*
- **One-function secret redaction on every egress** (#796): route session
  persist, provenance, saved skills, answer cards, and the HTML card through a
  single redaction helper; keep provider creds as env references, never
  serialized into a skill or lineage record; extend the "no raw rows to the LLM"
  rule to secret-shaped values. Overlaps the roadmap's PII-detection cross-cut.
- **Cross-platform case-collision guard** (#1128): no two files in the
  wheel/package (module names *and* `data/` fixtures) may differ only by case;
  add a Linux-runnable test that fails on a case-insensitive path collision.
- **Lazy capability probing** (#1098): Docker/kernel availability probed on first
  tool call, not at import/startup; fail-closed at the tool boundary with an
  actionable message. (Import stays side-effect-free — see the spec.)

## Provider path (feeds P3.5 / OpenRouter)

- Env-var indirection for `api_key`/`base_url` (`{env:VAR}`) (#319); don't require
  enumerating every model for a compatible endpoint — overlay refines a catalog
  default (#344); a config-named install target is a hint, never auto-installed.
- Offline/echo/compatible endpoint without `/models` returns `[]` gracefully, not
  a 502 (#1129) — our `--network=none` container hits this constantly.
- Honor `Retry-After` (HTTP-date and delay-seconds) over local backoff; keep
  waits cancelable; **never replay a completed mutating tool call** on retry
  (directly relevant to the fix→verify loop) (#1034).

## Headless / recursion (agent core)

- Headless one-shot must assert no TTY prompt, no first-run wizard, no onboarding
  write blocks execution (#1092).
- Depth-1 subagents inherit trust/permission mode from the parent **explicitly**
  and tested — a mode that silently defaults on spawn is a bug even when it fails
  safe (#1093).

## Governance (propose-only, hand-written core)

- **Content-digest skill admission** (Strike Agent-Plugins model): pin admitted
  skills by a SHA-256 digest of canonical sorted file payloads; any payload change
  auto-invalidates trust and forces review-before-update; pin git commits (no
  silent branch-following); split the admission bar by class (a passive
  deterministic fix vs a skill that executes code). Maps onto our test-gated +
  human-gated skill lifecycle.
- Register-time capability scan for skills/MCP/plugins (unexpected
  network/FS/credential-shaped tools), fail-closed under a strict preset,
  home-anchored path markers so a skill can't spoof a first-party location (#889).

## Config DX (if/when config grows)

- Publish a JSON Schema; tolerate JSONC comments with a documented round-trip
  (preserve vs strip) (#762/#873). Deterministic layered merge (global → project
  → subdir, deepest-last), defined and tested, not first-match-wins (#1130).
