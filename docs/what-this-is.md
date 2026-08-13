# analyst-agent: what it is and why it is built this way

An AI analyst that cleans messy data, answers questions over it, and can show
its work — where every number came from, which code produced it, and which
checks actually ran. Verified cleaning fixes become reusable skills under a
governance regime, so the agent gets cheaper and faster on data it has never
seen before.

This document explains the three claims that make it different, and what each
one cost to build honestly.

## 1. Nothing is verified because a model said so

The agent writes code; the kernel runs it; only executed assertions count.

A check mark on an answer card is lifted from an `assert` that actually ran to
completion in the kernel. If the model writes an answer without asserting
anything, the card says `unchecked` rather than nothing. If the model claims a
fix worked, that claim is worth exactly zero until the detector that found the
disease re-runs and comes back clean.

Cleaning goes further, because a cleaning fix can silently destroy data while
looking successful. A fix counts as verified only when all of these hold:

- the detection signal that found the disease no longer fires,
- the row count is unchanged (except for diseases whose fix is *supposed* to
  drop rows, where the expected delta is asserted exactly),
- every column the fix did not target hashes identically to before,
- the model's own asserts pass.

Fail any of them and the dataframe is restored from a pre-fix backup and the
failure goes back to the model as an observation. This is not decoration. In a
live run on the Raha beers dataset, a whitespace fix failed three times in a row
— the model kept using `.str.strip()`, which does not remove the non-breaking
spaces actually in those values — and the run recorded it as `failed` and moved
on rather than claiming a fix it could not prove.

## 2. Skills are governed, not just accumulated

The nearest published work, EvoDS (KDD 2026), showed that skills for data
science compound. It also admitted them on "ran once and used at least three
times", with no tests and **no retirement** — the Library-Drift failure mode,
where a library fills with skills that used to work.

Here, a fix earns skill status by passing gates that are executions and humans,
never model judgments:

1. **The frozen case.** When a fix verifies, the rows it changed are frozen
   along with rows it did not. The generalised `fix(df, columns)` is re-run
   against that case and must still trip the detector there (or passing proves
   nothing), must clear it, and must leave the untouched rows byte-identical.
2. **Its own test.** The shipped test runs in the sandbox.
3. **A human.** One yes or no.

The frozen-case gate is the one that matters, and it is easy to get wrong. A
generalisation that simply blanks the whole column *does* clear the detector —
the signal is gone because the data is gone. Only re-running against rows that
were never broken catches it. There is a test that does exactly this.

Admitted skills start on **probation**: retrieved and applied, but always
gated. Promotion to **proven** requires three successes across **two different
datasets**. That second clause is the load-bearing one: a fix generalised from
a single case proves nothing by succeeding on that same case again. Only a
proven skill on an AUTO-grade disease runs unattended — and verification still
runs, because that policy decides who watches, not whether the work is checked.

Two consecutive verification failures retire a skill to `skills/retired/`,
where it stays as evidence rather than being deleted.

## 3. Provenance is a graph, not a log

A claim is trusted when it is reachable from raw bytes **and** every step on
that path carried passing checks. `/why` prints that chain.

The two ways it can fail are different answers and the tool refuses to collapse
them: "not reachable from any raw file — nothing grounds this" is a different
problem from "a step on the path did not pass its checks". A system that
reports both as "untrusted" has thrown away the part you needed.

Building this corrected an assumption worth recording. A *failed* fix does not
taint the output downstream of it, because a failed fix is reverted — the data
genuinely does not contain it. The chain hangs off the last step that actually
held, while the failed attempt still appears in the graph, because hiding it
would be the exact lie the graph exists to prevent.

There is also an intent check before any answer ships: one narrow call that
reads the executed code back, states what it actually computed, and diffs that
against the question. It catches correct code answering the wrong question —
the failure assertions structurally cannot see, because assertions are about
the code that ran, not the question that was asked.

### What governance is worth, simulated

`scripts/ablate_governance.py` runs a synthetic skill population — including
skills that start good and rot when the data they assumed changes — under this
project's rules and under the EvoDS-style rule (admit after N uses, never
retire, no cap). Across seeds:

| | governed | ungoverned |
|---|---|---|
| rounds a rotted skill keeps being used | **1.1 – 1.9** | **27.5** |
| applications served by a bad skill | 1.6 – 2.5% | 9.7% |
| final library size | 13 – 17 | 80 (everything it ever saw) |

The retirement rule is doing the work: it is the difference between noticing a
skill has stopped holding in about two uses and noticing in about thirty.

**And it is not a free win.** Breaking the same run down by how good a skill
actually was, governance keeps only:

| skills whose true rate is | throughput kept vs ungoverned |
|---|---|
| ~0.95 (reliable) | 74% |
| ~0.60 (mediocre but net-positive) | **8%** |
| rotted to ~0.20 | 29% |

The middle row is the cost. "Two consecutive failures" is a harsh rule at a 60%
success rate — such a skill hits two in a row roughly one pair of uses in six —
so governance discards a lot of work that would have been right more often than
wrong. Some reliable throughput goes too, to cap eviction, because ties there
favour dropping the newest and least-proven.

For cleaning specifically I think that trade is correct: a wrong fix corrupts
data that later answers are computed from, and a discarded skill only costs a
model call. But it is a deliberate bias toward false negatives, not a
strictly-better ruleset, and the thresholds are placeholders until real usage
can tune them.

This is a simulation with assumed rates on a synthetic arrival schedule. It
shows the rules behave as designed on a plausible input shape; it is not a
measurement of real skills on real data, and the script says so in its own
output.

## What it does not do

- It does not fix everything. Two of the twenty-two diseases — cross-field
  contradictions and statistical outliers — are **indicators**: detected,
  evidenced, and never auto-fixed, because a spike in a wildfire dataset is
  real data, not an error.
- It does not hide failures. A run that fixes three findings, skips one, and
  fails one says so, and the report names which.
- It has not been used by anyone but its author yet. Every number in this
  document comes from the author's own runs.

## Measured, not asserted

- Detection runs all 21 single-file signals on a 150,000 × 80 frame in **9.5
  seconds**, worst case (a frame where every text column is dirty three ways).
- A live cleaning run on Raha beers scored **P=0.99, R=0.78, F1=0.87** against
  the benchmark's ground truth. The recall gap is fully accounted for: 693
  cells from a fix deliberately skipped at the gate, and 254 from the two
  indicator columns the design refuses to touch.
- The one precision failure — 29 cells — was a fuzzy match merging "American
  Amber / Red Lager" into "American Amber / Red Ale". They are genuinely
  different styles. The detector graded that finding HUMAN precisely because
  fuzzy matches need judgment; a scripted operator approved it anyway. The
  grade was right and the operator was wrong, which is the correct division of
  labour to have discovered.
