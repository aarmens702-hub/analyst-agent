# analyst-agent

An analyst agent that remembers. It cleans messy real-world data in a
persistent, sandboxed IPython kernel; answers questions over it; and ships
every answer with the code that produced it, the checks that actually ran,
and the lineage from raw file to claim. Cleaning fixes are verified, recorded,
and (from P2) compound as governed, tested, reusable skills.

## Quickstart

```bash
uv sync
echo 'DEEPSEEK_API_KEY=sk-...' > .env       # DeepSeek is the default model
uv run python scripts/fetch_raha.py          # dev datasets (dirty/clean pairs)
uv run python -m analyst_agent
```

In the REPL:

```
/load data/raha/beers/dirty.csv beers        load a CSV as a kernel variable
which brewery has the most beers?            ask anything — code is gated, then runs
/clean beers                                 diagnose 22 diseases, fix one by one
/quit
```

Every model-written cell stops at a gate: `[r]un` executes it, `[j]eject`
sends a steering note back, `[s]kip` (clean mode) leaves a finding unfixed.
`--auto-run` skips gates for development. `--docker` runs the kernel inside a
no-network container instead of a subprocess.

Answers arrive as **cards** (answer + code + passed asserts + sha256 lineage)
under `workspace/<session>/cards/`. Cleaning runs produce **clean reports**,
a dtype-preserving parquet copy, and a lineage sidecar under the session
directory — originals in `data/` are never modified.

## How it works

The LLM never sees your data. It sees a schema/stats profile and a live
variable registry, and writes small code cells against data that stays loaded
in a persistent kernel (subprocess in dev, `--network none` Docker in
sandbox mode), reached over an NDJSON stdio protocol. Tracebacks come back as
observations, so repair is just the next iteration. A check mark can only come
from an assert that executed; a cleaning fix counts as verified only when the
detection signal that found the disease re-runs clean, row and untouched-column
invariants hold, and the model's own asserts pass.

## Development

```bash
uv run pytest                    # full suite (kernel protocol runs containerless)
uv run pytest -m docker          # container end-to-end (needs Docker running)
uv run ruff check --fix . && uv run ruff format .
uv run python scripts/score_fixes.py <cleaned.parquet> beers   # fix P/R vs truth
```

Specs live in `specs/` (P0 core, P1 clean mode). Research and positioning:
`../mailo/coop-project/analyst-agent-brief.md` and
`analyst-agent-build-research.md`.
