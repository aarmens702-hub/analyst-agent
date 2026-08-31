# crivo

An analyst agent that remembers: cleans messy real-world data in a persistent sandboxed IPython kernel, answers questions over it, saves every verified fix as a governed reusable skill, and ships every answer with code + assertions + provenance.

## The contract (read first)

- **Aarmen hand-writes the core:** the agent loop, the skill lifecycle/governance, the provenance DAG, and the prompts. For anything under `src/crivo/` touching those areas: propose diffs and explain tradeoffs — do not rewrite unasked.
- **Claude owns:** tests, scaffolding, dataset fetch scripts, the Streamlit shell, docs, and plumbing (kernel supervisor, Docker, profiling utilities).
- When unsure which side of the line something is on, ask — one sentence, not a menu.

## Commands

- Tests: `uv run pytest`
- Lint/format: `uv run ruff check --fix . && uv run ruff format .`
- Run: `uv run python -m crivo`

## Architecture in one breath

LLM client on the host (DeepSeek default, Claude behind the same `generate()` seam) drives an IPython kernel inside a `--network=none` Docker container over a stdio JSON supervisor. Datasets are kernel variables — the model sees schema/profile/samples, never raw dumps. CLEAN mode: profile → diagnose (22-disease checklist) → fix → verify with task-aware assertions → propose skill. QUERY mode: NL → pandas → answer card (code + checks + lineage). Skills are SKILL.md folders with tests; admission is test-gated + human-gated; contribution-scored; retired when they stop paying rent. Recursion capped at depth 1.

Full spec: `~/Desktop/mailo/coop-project/crivo-brief.md` and `crivo-build-research.md` (stack patterns, gotchas, disease taxonomy, datasets).

## Judgment notes

- Execution beats self-report: nothing "works" until pytest or an assertion says so. Never claim a fix is verified without running it.
- No agent frameworks. Raw API calls + jupyter_client. If glue feels missing, write the 20 lines.
- Match the existing idiom. Plain functions over classes unless state genuinely demands it.
- Never put raw dataset rows in a prompt — schema, stats, and truncated samples only.
- Multi-day features get a short WRAP spec in `specs/` first (What, Requirements, Acceptance criteria, Priority).
- Datasets in `data/` are immutable originals; all transforms write copies with lineage.
