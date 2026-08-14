# Open findings — 2026-08-13

Two sources, kept in one list because they interact: a max-effort review of
`origin/main...HEAD` (15 findings, 10 finder angles plus a gap sweep), and a
run of `diagnose` against a synthetic transaction file (`scripts/make_transactions.py`).

Every item here was reproduced by running it. **The suite was green for all of
them** — 276 passing, 0 failing. That is the finding behind the findings.

---

## A. The detection engine reports its hardest cases as clean

Found by pointing `diagnose` at a transaction file. This is the one that
decides whether the finance demo is worth showing.

### A1 — the homogeneity gate runs backwards *(design work, not a patch)*

`_d01` requires ≥90% of values to match one money pattern before it will look.
So a column gets **quieter as it gets more damaged**:

```
  0/600 values in a 2nd money format -> d01 fires
 60/600                              -> d01 fires
120/600                              -> d01 SILENT
240/600                              -> d01 SILENT
```

On the transaction file, `balance` (uniformly `$X,XXX.XX`, the easy case) fires
at `match_frac=1.00`. `amount` — the same disease five ways over, `$1,281.08` /
`2,447.35` / `-27,29` / `USD -174.89` / `1681.74` — sits at `0.555` and is
silent. It is reported in `clear`.

Worse on dates: `_date_scan` gates on ≥90% family coverage and **both d02 and
d03 sit behind it**, so d03 — whose entire subject is *multiple date formats in
one column* — is switched off by the column having multiple date formats.

```
posted_at families: {'iso': 0.25, 'slash': 0.25}   covered=0.50  (needs 0.90)
-> d02 AND d03 both skipped, and 2-3 appears in "checked and clean"
```

11 detectors share the threshold shape. The fix is not a threshold tweak:
heterogeneity has to *raise* the signal rather than suppress it, which means
splitting "is this column of kind K" from "does this column agree with itself".

This is the project's own stated line — *"absence is a checked claim here, not
a silence"* — failing on the case that matters most.

**Fixed 2026-08-13** (option 2, approved by Aarmen). The gate now asks the two
questions the old threshold conflated: *is this column number/date-shaped at
all* (the UNION of every family — `NUMBER_FAMILIES` partitions values
first-match so shares sum to the union) and *does it agree with itself* (the
per-family breakdown). Three outcomes replace two: one family ≥0.90 fires AUTO
as before; ≥2 families ≥0.50 union fires with the mix named and the unclaimed
tail stated, GATE when decimal-comma and thousands-comma coexist (same digits,
two readings); the [0.30, 0.90) middle is a HUMAN finding naming both numbers,
never silence. d03 applies its own agreement threshold instead of inheriting
d02's. Invariant tests are monotonicity itself: k = 1..5 money formats and
k = 2..4 date formats must fire at every k. The fixture's `amount` now reads
"5 number formats in one column … the same digits read two ways · fix with a
human check"; `posted_at` fires d03 with the 25% epoch tail named. One
interaction the admission tests caught: d01 counting d04's sentinels as
"match nothing known" reported the same cells twice — d01 now judges
`_present` values, matching the date scan's existing discipline. Epoch stays
unclaimed as a family (a bare-integer column needs more than ten digits to
mean a timestamp); the union design reports it honestly as an unclaimed
share instead of going quiet.

### A2 — ISO 8601 is not in `DATE_FAMILIES` *(cheap)*

```
2024-01-10 14:30:00          -> ['iso']
2024-04-13T14:33:00Z         -> NO FAMILY CLAIMS IT
2024-04-13T14:33:00+00:00    -> NO FAMILY CLAIMS IT
2024-04-13T14:33:00.123Z     -> NO FAMILY CLAIMS IT
1712000020                   -> NO FAMILY CLAIMS IT
```

The `T`/`Z` form is the machine timestamp format in transaction feeds. Missing
pattern, not a threshold — and it is half of why the date scan fell below 0.90
above, so it partly relieves A1 for free.

**Fixed 2026-08-13.** `iso` now takes fractional seconds; a new `iso-zoned`
family claims RFC 3339 zone suffixes on either separator. Zoned is deliberately
a separate family: a naive/zoned mix cannot land in one datetime64 without a
decision, so the mix fires d03 rather than being absorbed into one wide
pattern. Epoch integers stay unclaimed pending A1 — a bare-integer column
needs a "date-shaped at all" kind gate before ten digits may mean a timestamp.
On the fixture, `posted_at` coverage moves 0.50 → 0.75: still below the 0.90
gate, so A2 alone does not un-silence it. A1 finishes the job.

### A3 — duplicate index → quadratic hang *(cheap, high value)*

```
n=2000 rows, varying distinct index labels
  2000 distinct (multiplicity    1) ->   0.023s
   100 distinct (multiplicity   20) ->   0.018s
    10 distinct (multiplicity  200) ->   0.876s
     4 distinct (multiplicity  500) ->  13.745s
     1 distinct (multiplicity 2000) ->  >90s, never returned
```

Hot spot is `check_bool_indexer` → `get_indexer_non_unique`: every `values[mask]`
is a **label-aligned** boolean mask, so pandas does a non-unique lookup per row.
9 sites in `detect.py`. Normalising the index once at the entry point fixes all
of them:

```
duplicate index      :  13.804s
same data, RangeIndex:   0.004s   -> 3,676x faster
```

Trigger is `set_index()` on a low-cardinality column — `currency`, `category`,
`account_type`, a date bucket. Standard first move on a transaction table.

**Pre-existing, not a regression** — `origin/main` takes 15.8s on the same
frame. The review reported this as a `ValueError` crash that `origin/main`
handled fine; neither half reproduces at HEAD. A hang is worse than a crash
here: `detect_all`'s per-detector `except` catches a crash and reports it in
`broken`, and nothing catches a hang.

**Fixed 2026-08-13.** `detect_all` and `detect_one` re-index a shallow copy
when labels repeat (`_flat`), covering all nine mask sites at the two entry
points that dispatch detectors; `detect_family` reads only columns and dtypes
and needs nothing. The invariant test parameterises over the non-unique index
shapes a transaction workflow produces and holds findings equal to the
RangeIndex baseline inside a 5s bound — red at 14.0s before the fix,
milliseconds after.

---

## B. Review findings still open (13)

Two of the review's 15 are fixed — `loop.py:410` (inner `except KernelLost`
swallowed the exception so `_recover` never fired) and `loop.py:391` (the `try`
started one line too late, so a death at diagnosis wrote no report). Both were
the two halves of one bug, consolidated in `6cd99f4` behind a test parameterised
over all 7 kernel touch points.

### Data integrity

| Where | What |
|---|---|
| ~~zero-width unenforceable~~ | **Fixed 2026-08-14**: d06 verification is anchored to the reference repair — the fixed column must equal `_ws_tidy` of the original, which deletes zero-widths by construction, so a word-splitting repair fails layer 1 instead of passing it. Test executes the built cell against both the corrupting and the honest repair. |
| ~~detector crash charged to the skill~~ | **Fixed 2026-08-14**: `verify_cell` wraps the detector re-run and raises with an `uncheckable:` prefix; the loop still reverts (unverified is unverified) but declines to score the skill — retirement evidence must be about the skill. Pinned at both the cell and the ledger. |
| ~~`_slice_var` not injective~~ | **Fixed 2026-08-14**: an unconditional short digest of the raw slice key keeps distinct slices distinct ("only when lossy" is itself a collision surface). Test demands four colliding spellings produce four identifiers, deterministically. |
| ~~`run["cleaned"]` unconditional~~ | **Fixed 2026-08-14**: `clean()` returns whether it ran to completion; the family loop only counts slices that did. The honest fake this needed exposed an existing family test whose bind cells never carried the slice variable — its "cleaned" slices had always early-returned, masked by the unconditional append. |

### Recovery path — consolidated once, still incomplete

- ~~`_restart_and_replay` never calls `_stamp_registry`~~ **Fixed 2026-08-14**:
  the replay now keeps each load's registry and stamps it with the variable's
  original event, so provenance still points at the load the operator saw. The
  test drives the *real* replay (the prior test monkeypatched it away) and runs
  a whole second clean through the recovered session, parameterised over all
  seven death points — red 7/7 before the fix.
- ~~in-flight finding loses its events~~ **Fixed 2026-08-14**: the fix loop
  watermarks the transcript per finding; on a death the handler attributes
  every event past the watermark to the finding that was in flight, so the
  cell that mutated the frame stays reachable from `/why`. (The mutation
  itself no longer survives either death path: recovery replays loads from
  raw, a consequence of the earlier handler consolidation.)
- ~~`admitted` discarded on death~~ **Fixed 2026-08-14**: the admitted list
  lives in the clean's state dict and the ledger is saved the moment each
  admission is granted, not at the end of the pass — a death during the next
  candidate's cells can no longer orphan a human-approved skill. Test kills the
  kernel inside skill B's admission and demands both the ledger entry and the
  report record for skill A.
- ~~`loop.py:570` — `yield from` inside a generator's `finally`~~ **Fixed
  2026-08-14**: the family summary write is now a pure function called from the
  `finally` (never yields), and the human-facing summary line is yielded only on
  the normal path. Test closes the generator midway and demands the summary.

### Operator-facing

- ~~`repl.py:56` — blanket `except` outside the `while`~~ **Fixed 2026-08-14**:
  the per-turn guard sits inside the loop; interrupts still exit. The new test
  requires the turn *after* a failure to actually run — the old test's scripted
  `/quit` was never consumed, so it passed either way.

### Noticed during the loader fix, not yet addressed

- `LOAD_TEMPLATE` reads with pandas' default NA handling, while
  `diagnose.load` deliberately sets `keep_default_na=False` — so in agent
  mode, `read_csv` coerces `N/A`/`NA` to NaN at load time and silently repairs
  part of d04's evidence before diagnosis ever runs. The free `diagnose`
  report and a live `/clean` of the same file can legitimately disagree about
  sentinel counts. Fixing it means deciding dtype policy for QUERY mode
  (keep_default_na=False strings every column that carries one token), so it
  is a design call, not a patch.

### Found by the live run (2026-08-14, both fixed by config)

- Admitted skills each ship `scripts/test_fix.py`; two same-basename files
  broke repo-wide pytest collection. `testpaths = ["tests"]` — skill self-tests
  are the admission gate's to run, in-kernel, never the host suite's.
- Skill artifacts use a `fix` name injected by the admission harness, so host
  lint rules structurally cannot apply: `skills/` joined ruff's exclude list.
- ~~`_clip` truncates before the diff~~ **Fixed 2026-08-14**: diff first, then
  bound the rendering — unchanged runs squeeze to their ends, marked runs cap
  at 60, and a change past the matcher's own 2,000-char bound is *said*
  ("change beyond preview bound") instead of omitted. Test plants the edit at
  character 70 and demands markers.
- ~~`broken[...]` unsanitised~~ **Fixed 2026-08-14**: same collapse and cap as
  `_finding`'s evidence. Test raises a multi-line, 900-char exception through a
  detector and demands one bounded line.
- ~~Unicode scan + `map(lambda)`~~ **Fixed 2026-08-14**: `_UNICODE_SPACES` is a
  literal (import self-time 65ms → 2.7ms) with a parity test that recomputes
  the Zs category from `unicodedata` — the scan moved from every kernel start
  into the suite. The per-character lambda became one vectorised
  `str.contains` on a character class built from the same table.

---

## C. The pattern worth naming

Roughly a third of the review's list is damage from the *previous* round of
fixes. The mechanism, from the earlier retrospective:

> **Silence is spelled the same as success.** Every defect is a thing that did
> not happen being recorded as a thing that happened cleanly. The architecture
> prevents this for the *dataset* — `clear` is a checked claim — while every
> internal seam encodes "found nothing" and "did not check" identically.

A1 is that same defect, now inside the detection engine's own thresholds rather
than in the plumbing.

The contributing cause is where tests point. A test written against the
*reproduction* ("a death in the fix loop writes a report") passes forever while
five sibling paths stay broken. A test written against the *invariant* ("a
death at **any** kernel touch point tells the operator, writes a report, and
restarts"), parameterised over all seven, failed 7/7 on first run — including
both paths that already had green tests.

**Rule going forward: parameterise over every path the invariant claims to
cover, not the one path that broke.**

---

## D. Two decisions still owed

- **CLAUDE.md line 7** reserves the agent loop — *"propose diffs and explain
  tradeoffs — do not rewrite unasked"* — and `specs/2026-08-13-p5-...md:65`,
  added in the same diff, restates it naming the exact function. The diff then
  restructured `loop.py` around it: `clean` split four ways, `clean_family`
  three, a new `KernelLost` type, a new `_exec_events` parameter. Every
  judgement call — catch-per-entry-point, `tolerate_death`, the fifth status,
  the `persisted` key, where the `try` boundary sits — was made rather than
  proposed. Keep, or redo as a proposal before more lands on top?
- **Two `_probe` calls** need a ruling: `_d06`'s sampled presence gate (a
  probabilistic false CLEAR) and `_d17`'s sample-scaled counts (reports 3,231
  where the true count is 42,000).
