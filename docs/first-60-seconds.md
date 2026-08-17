# First 60 seconds

Not just install — this is the shortest path to a cleaned file and an honest
report of what didn't get fixed. Four steps, all copy-pasteable. Nothing
before step 4 needs a model key, a kernel, or Docker.

## 1. Install

```bash
# from source today; `pip install analyst-agent` once published — see the
# README's Publishing status: publish-ready, not published
uv add git+https://github.com/aarmens702-hub/analyst-agent
```

## 2. Diagnose — the 22-check report, as a card

```python
import analyst_agent as aa

df = aa.read("your_data.csv")   # csv, tsv, parquet, xlsx, json, jsonl — reader picks the format
df.aa.diagnose()
```

In a notebook, that line *is* the output: a styled, severity-coloured card,
not a wall of text. `import analyst_agent as aa` registers the `.aa` accessor
on every DataFrame — after that one import, `df.aa.diagnose()` reads like
`df.describe()`. Detection is pure Python: no key, no kernel, no Docker.

Working in a script instead of a notebook, or you just prefer a function
call? `aa.diagnose(df)` (or `aa.diagnose("your_data.csv")` straight from a
path) returns the same `Report` — `print()` it for the text version, or run
`analyst-agent diagnose your_data.csv` from the command line for the same
report in colour, no Python at all.

## 3. Clean — safe fixes applied, the rest deferred honestly

```python
cleaned, summary = df.aa.clean()   # or aa.clean(df, policy="auto")
summary                             # renders as a card: before/after per fix
aa.write(cleaned, "cleaned.xlsx")   # format from the extension
```

`clean` only auto-applies a fix it can verify by re-running the detector that
found the problem in the first place: numbers stored as text, mixed date
formats, sentinel "missing" values, stray whitespace, case variants,
constant columns. Still no model. Anything that deletes rows (duplicates,
near-duplicates) or needs a judgement call — ambiguous money conventions,
contradictions, outliers — is *reported*, not decided; it shows up in
`summary.needs_review` instead of getting silently resolved for you.

## 4. The hard fixes — run the agent

The findings `clean` deferred are exactly the ones worth a second pair of
eyes, human or model. Point the agent at the same file:

```bash
uv sync
echo 'DEEPSEEK_API_KEY=sk-...' > .env   # or ANALYST_PROVIDER=claude + ANTHROPIC_API_KEY
uv run python -m analyst_agent
```

```
/load your_data.csv df
/clean df
```

Every fix the agent proposes stops at a gate — `[r]un` executes it,
`[j]eject` sends a steering note back, `[s]kip` leaves it unfixed — so
nothing judgement-shaped gets decided without you watching it happen. See
[the agent](../README.md#the-agent) for the rest of the REPL, or
[MCP server](../README.md#mcp-server) to drive the same loop from Claude
Desktop, Claude Code, or Cursor instead of the terminal.

---

That's the whole path: a report you can trust from cell one, a clean pass
that only claims what it verified, and an agent for the part that genuinely
needs judgement. No API key until step 4.
