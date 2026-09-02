# Operating contract for agents working in this repo

Read CLAUDE.md first — ownership and judgment rules live there. This file is
the mechanical contract: the commands, the traps, and the never-list. It
exists because every one of these rules has already burned an agent once.

## Commands

- One test: `uv run pytest tests/test_x.py::test_name -q`
- One file: `uv run pytest tests/test_x.py -q`
- Full suite: `uv run pytest -q` — integration checkpoints only, never in a
  tight edit loop (52s+ and climbing).
- Lint/format: `uv run ruff check --fix <files> && uv run ruff format <files>`
- Bench smoke (once it exists): `uv run python -m bench.run --smoke`

## The traps

- **ruff PostToolUse hook race:** `ruff check --fix` runs after EVERY
  Edit/Write and strips unused imports. Add an import and its first usage in
  the SAME edit, or the import silently vanishes before your next edit.
- **tdd-guard:** a hook rejects adding more than one test at a time. Work
  red-green per test; keep tests dense (parametrize/table-driven) so few are
  needed. New test files start with exactly one test. Pytest fixture errors
  register as ERROR not FAILED — make first failures happen inside test
  bodies. Pausing the guard is Aarmen-gated; do not edit its config.
- **tdd-guard state is GLOBAL and single-slot:** concurrent agents clobber
  each other's recorded red, so rejections may cite tests that aren't yours.
  Protocol: re-run YOUR test file, retry the edit immediately. If a sibling
  runs pytest continuously the window is unwinnable — batch gated edits for
  after it reports, and prefer wave topologies with at most one agent
  running tests during any hot period.
- **Kernel/LLM tests:** the suite is keyless by design; `tests/conftest.py`
  quarantines provider credentials. Never add a test that needs a real key or
  the network — fake providers and tmp files exist for a reason.

## Never

- Docker on this machine (not enough RAM). Container tests are opt-in
  (`uv run pytest -m docker`) and only when the daemon is confirmed up.
- `git add -A`, `git reset`, `git stash`, or any commit/push from a subagent —
  the integrating session owns git. Multiple agents share one working tree.
- Raw dataset rows in any prompt, log, or finding — schema, stats, and
  truncated samples only.
- Editing `data/` originals — immutable; transforms write copies with lineage.
- Touching files outside your assigned deliverables.

## Bench (Proving Ground) special rule

Authors of corruption injectors (`bench/corrupt.py`, `bench/bases.py`) must
NOT read `src/crivo/detect.py` or `src/crivo/autoclean.py` — the bench grades
that code, and an injector written from detector internals grades its own
exam. Plant what the disease taxonomy says the disease IS. The scorer side
may read anything; it drives the public API.

## Ownership line (short form)

`src/crivo/` agent loop, prompts, skill lifecycle/governance, provenance DAG:
Aarmen hand-writes — propose diffs, never rewrite unasked. Tests, bench,
scaffolding, fetch scripts, docs, kernel plumbing: agent-owned. Unsure which
side: ask, one sentence.
