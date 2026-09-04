<div align="center">

<img src="docs/assets/banner.svg" alt="crivo" width="100%">

<br>

**An AI analyst for messy data. It diagnoses, cleans, and answers questions over your tables, and every fix and answer is re-checked before it counts.**

[![ci](https://github.com/aarmens702-hub/analyst-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/aarmens702-hub/analyst-agent/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.12-0e7a84)
&nbsp;
![verified fixes](https://img.shields.io/badge/verified_fixes-0b2b30)
![sandboxed](https://img.shields.io/badge/sandboxed-0b2b30)
![keyless mode](https://img.shields.io/badge/keyless_mode-0b2b30)

[What it does](#what-it-does) · [How it works](#how-it-works) · [The analyst](#the-analyst) · [MCP server](#mcp-server)

</div>

Most tools will chat with your dataframe. None of them check whether what came
back was right. crivo does. It reads a messy table, tells you what is broken,
fixes what is safe to fix, answers your questions, and re-runs the check on
every step before it keeps it. When all you need is the diagnosis, that half is
plain Python and runs without a key.

## Start here

Not on PyPI yet, so install it from the repo:

```bash
pip install git+https://github.com/aarmens702-hub/analyst-agent
```

Set a model key and you have an analyst in your terminal:

```bash
echo 'DEEPSEEK_API_KEY=sk-...' > .env       # or ANTHROPIC_API_KEY
python -m crivo
```

```
/load data/messy.csv sales      load a file as a variable
which region grew the most?     ask in plain english; the code is gated, then runs
/clean sales                    diagnose, then fix one at a time
/why                            where an answer came from, and whether it holds
/skills                         the fixes it has learned and kept
```

No key handy? The library half works without one, straight from Python:

```python
import crivo as cv

df = cv.read("transactions.csv")        # any format, sentinel-safe
report = cv.diagnose(df)                # runs the checks, no key, no kernel
report                                  # prints the list, or a card in a notebook
report.suggest()                        # starter questions for this dataset

cleaned, summary = cv.clean(df)         # applies the safe fixes, re-checks each one
summary.cells()                         # the receipts: exactly what changed, per fix
summary.needs_review                    # the ambiguous ones it will not guess on
cv.write(cleaned, "clean.parquet")      # your data back out, any format
```

`crivo.read` handles the formats you actually get data in: csv, tsv, parquet, xlsx,
json, jsonl, feather, orc, compressed files (`.gz`, `.zip`, `.bz2`), parquet
folders, a database connection, or a JSON API.

## What it does

- Answers plain-english questions over your data and ships every answer with
  the code it ran, the checks that passed, and where the numbers came from.
- Runs 22 checks on any table and grades each finding: safe to fix, fix with a
  check, or needs a person.
- Fixes the safe ones and re-checks every fix. If the check still fires, it
  throws the fix out and reports it instead of keeping a bad one.
- Saves a fix that worked as a reusable skill, but only after it passes its own
  test and you say yes.
- Reads from files, compressed files, folders, databases, and JSON APIs, all
  through one `crivo.read`.
- Shows up as a card in a notebook, and a data-quality chart with `.plot()`.
- Runs as an MCP server, so Claude Desktop, Cursor, or Claude Code can call it.
- Has a headless mode for CI, where only the safe fixes run and the rest are
  reported.

## How it works

**Two halves.** The analyst half is the model: it writes and runs the code that
answers your questions and lands the harder fixes, but it never sees your raw
rows, only the schema and a few samples, and every step waits for your OK. The
library half (`diagnose`, `clean`, `read`) is plain Python: no key, no kernel,
nothing to trust yet.

**One rule for both.** A fix counts only when the check that found the problem
stops firing, and the rows and untouched columns still line up. If it does not
clear, the fix is reverted and sent back to you. Nothing is trusted because a
rule ran. It is trusted because the signal went quiet.

**A trail on every answer.** An answer is trusted only if you can trace it back
to the raw bytes through checks that passed at each step. `/why` prints that path,
and says how it broke when it breaks.

Every finding gets one of three grades:

| grade | means | what happens |
| --- | --- | --- |
| safe | mechanical, no judgement | fixed and re-checked |
| check | one plausible fix, worth confirming | fixed, then flagged |
| person | a real judgement call | reported, never decided for you |

## The analyst

The analyst is the main event: it turns a question into pandas, runs it, and
hands back an answer card with the code, the checks that passed, and the lineage
behind every number. It earns that with a strict contract. Every line of
model-written code stops at a gate: you run it, send a note back, or skip it.
The kernel is a subprocess by default, or a `--network=none` container in
sandbox mode.

## Models

DeepSeek is the default; the same seam speaks to Anthropic and to any
OpenAI-compatible endpoint, local models included:

```bash
DEEPSEEK_API_KEY=sk-...                       # default provider
CRIVO_PROVIDER=claude ANTHROPIC_API_KEY=...   # Anthropic
CRIVO_PROVIDER=openai \
  CRIVO_BASE_URL=http://localhost:11434/v1 \
  CRIVO_MODEL=qwen3:32b python -m crivo       # Ollama, vLLM, OpenRouter, ...
```

`CRIVO_MODEL` overrides the model on any provider; local servers need no
key (`CRIVO_API_KEY` if yours does). Slow local endpoints can raise the
stream budgets with `CRIVO_STALL_S` / `CRIVO_MAX_CALL_S`.

## MCP server

Any MCP client can drive crivo as tools. The gates follow the same rule
as headless mode: the safe fixes run, the judgement calls come back for a person,
and the calling agent never decides them.

| tool | what it does |
| --- | --- |
| `diagnose_file` | the 22-check report on a file. free, keyless, read-only |
| `clean_file` | headless clean; judgement calls are reported, not decided |
| `open_data` | load a file into a session and get a profile |
| `ask` | answer a question over a session, with checks and lineage |
| `why` | the trail behind a session's answers |
| `close_session` | shut the session down |

## In one sentence

It is the plain-english analysis of pandas-ai and the one-line report of
ydata-profiling, but every fix and answer comes with a receipt, and the checks
still run when there is no key at all.

<details>
<summary><b>The 22 checks</b></summary>

<br>

Numbers stored as text, mixed date formats, fake missing values (`N/A`, `-`, `null`
typed into a cell), whitespace and encoding damage, spelling and case variants of
the same category, duplicate rows, near-duplicate rows, constant columns, columns
that are almost entirely empty, values that contradict each other, outliers,
schema drift across a set of files, and more. Each one is a named detector that
either fires with evidence or reports the column clean. Absence is a checked
claim, not a silence.

</details>

<details>
<summary><b>How the analyst runs code safely</b></summary>

<br>

The model runs on the host. It drives an IPython kernel that lives inside a
container (a subprocess in dev, `--network=none` in sandbox mode) over a small
stdio protocol. Your datasets are variables in that kernel. The model sees the
schema, some statistics, and truncated samples, never the raw rows. It writes
short pandas cells; each one stops at a gate; a traceback comes back as the next
thing it reads and works from. A cleaned dataset is always a copy with its lineage
recorded. Recursion is capped at one level.

</details>

<details>
<summary><b>How a fix becomes a reusable skill</b></summary>

<br>

After a clean run, each fix the model wrote is rewritten as a general
`fix(df, columns)` and has to earn its place. It has to trip the detector on the
original frozen case, clear it, and leave the never-broken rows alone. Its own
shipped test has to pass. Then a person says yes. New skills start on probation
(retrieved and applied, but always gated). Three wins across two different
datasets promote a skill; two failures in a row retire it. No model decides
admission; the sandbox and a person do.

</details>

## In CI — a linter for data

`crivo diagnose` exits like a linter: `0` clean, `1` findings at or above
`--fail-on` (default `GATE`, "anything needing a person"), `2` broken input.
As a GitHub Action:

```yaml
- uses: aarmens702-hub/crivo@main
  with:
    path: data/latest-export.csv
    fail-on: GATE
```

Or anywhere else: `crivo diagnose file.csv --fail-on AUTO --json`.

## Benchmarks

<!-- bench:start -->
**Deterministic mode baseline** (2026-09-03, 1450 synthetic + 4 external datasets, cell-level scoring, 0 labels):

| metric | value |
|---|---|
| detection micro-F1 (mean, silence = 0) | 0.732 |
| repair F1, fully-fixable datasets | 0.983 |
| survived-verification rate | 1.000 |

Full tables: `bench/RESULTS.md`.
<!-- bench:end -->

## Where it's at

409 tests pass. The analyst half needs a model key and a kernel. The library
half does no network, subprocess, or Docker work when you import it, so it is
safe to use in any notebook cell. MIT licensed. Not on PyPI yet; publishing is
the next step.

## Development

```bash
uv run pytest                    # the full suite
uv run pytest -m docker          # the container tests, opt in
uv run ruff check --fix . && uv run ruff format .
```

Specs live in `specs/`. The design notes for each phase are there, newest last.
