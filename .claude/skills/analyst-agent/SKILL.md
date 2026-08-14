---
name: analyst-agent
description: Use when the user provides a messy CSV/parquet file, asks what is wrong with a dataset, wants a data file cleaned with an audit trail, or wants questions answered over tabular data with verifiable receipts. Drives the analyst-agent CLI (diagnose / headless clean / interactive REPL) and interprets its artifacts.
---

# Driving analyst-agent

analyst-agent cleans messy tabular data in a sandboxed kernel and ships every
change with executed checks and lineage. You are the orchestrator; it is the
tool. Its trust model is the point — follow it, don't work around it.

## Step 1 — diagnose (always first; free, no API key, read-only)

```bash
uv run python -m analyst_agent diagnose <file> --json
```

Returns findings (each with `disease`, `slug`, `columns`, `evidence`,
`grade`, `confidence`), `clear` (checks that ran and found nothing — absence
here is a claim, not a silence), and `broken` (checks that could not run).
Summarize the findings for the user in plain language, worst first. If the
file is fine, say what was checked, not just "looks good".

## Step 2 — headless clean (needs an API key; writes artifacts)

Requires `DEEPSEEK_API_KEY` (or `ANALYST_PROVIDER=claude` +
`ANTHROPIC_API_KEY`) in the environment or `.env`.

```bash
uv run python -m analyst_agent clean <file> --json
```

Chatter goes to stderr; stdout's last line is one JSON object:

- `fixes`: per finding — `slug`, `grade`, `status` (fixed / skipped / failed)
- `needs_human`: gates the policy refused to decide. Relay these to the user
  verbatim — they are judgement calls (merging near-duplicate names, picking
  a side in contradictory records) that an agent must not make for them.
- `report`: path to the clean report JSON (a sibling `.md` is human-readable)
- `outputs`: the cleaned parquet and its lineage JSON

The default `--policy auto` runs only AUTO-grade fixes. **Never pass
`--policy all` unless the user has explicitly said to approve everything** —
it approves judgement-grade changes unattended.

## Step 3 — what needs a person

If `needs_human` is non-empty, offer the interactive path, where each fix
shows a preview of its consequence before the gate:

```bash
uv run python -m analyst_agent          # then: /clean <var>, /why, /skills
```

## Rules

- Never claim the data is clean beyond what the report says. Quote counts
  from the report, not from your own reading of the file.
- The cleaned parquet in `outputs` is the deliverable; the original file is
  never modified. Lineage JSON links the two — mention it when the user
  cares about auditability.
- If `clean` reports `error` or a fix `failed`, say so plainly and show the
  report path. Do not retry with `--policy all` to force a green result.
- Never run the kernel in Docker on this machine (`--docker`) — use the
  default subprocess mode.
