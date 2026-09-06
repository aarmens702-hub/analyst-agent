# Code review triage and fix plan

2026-09-06. **Status: triage of a full multi-agent code review, for owner
review.** 56 findings (24 high, 27 medium, 5 low) from seven module-group
reviewers, each finding independently re-checked by an adversarial verifier
that was told to refute by default. Zero findings were refuted. The three
highest-stakes items were additionally confirmed by hand, by reading the code.

Source: two workflow runs (the second filling gaps where agents died on a
session limit). Raw findings: `.claude/jobs/.../all_findings.json`.

## The three themes

1. **The MCP surface is not safe.** Model-authored code reaches the host
   unsandboxed, with API keys in its environment, every gate auto-approved, and
   one client argument is interpolated into a kernel cell unescaped.
2. **The gate is fail-open.** Every gate site, including the HUMAN-grade fix
   gate and the skill-admission gate, defaults a missing or unrecognized answer
   to "run". `skip` does not skip. Rejecting a plan does not withdraw batching.
3. **The receipt that justifies autonomy is defeatable.** Several AUTO fixers
   corrupt data and still pass verification, because the fixer changes the
   column dtype and the detector is silent on the new dtype, so "no finding" is
   read as "verified".

**Gating consequence: the autonomy-default build (thread A of the
2026-09-05 planning set) is blocked on P1 and P2 below.** Its entire safety
argument is that unattended action is safe because every fix is verified and
reverts. That argument does not currently hold.

**Second consequence: the bench numbers are suspect (P5).** The batched arm
resumes from the baseline arm's result files and reports the baseline's numbers
as its own, which is the arm confusion we hit in the M1 vs M2 measurement.
Re-measure after P5 before trusting any M1 or M2 comparison.

---

## P0. Security, ship-blocking

Nothing should be published (PyPI, a public MCP endpoint) until these are done.

1. **`mcp_server.py:265` remote code execution.** The client-supplied `name`
   argument of `clean_file` is interpolated unescaped into the kernel load
   cell, giving the calling client arbitrary un-gated execution in the host
   kernel. Fix: never interpolate client strings into a cell; pass values
   through the kernel as bound variables, and validate `name` against an
   identifier pattern. Proof: a test that a `name` containing a quote and a
   statement neither executes nor reaches the cell text.
2. **`mcp_server.py:55` no sandbox.** `Session(...)` is built with no `docker`
   argument, so `loop.py:158` falls through to a host subprocess with network,
   while the `clean_file` and `open_data` docstrings promise a sandboxed
   kernel. Fix: require an explicit sandbox choice in the server factory and
   default to docker; if docker is unavailable, refuse to start rather than
   silently downgrade. Proof: a test that the factory raises when docker is
   unavailable.
3. **`client.py:91` API keys in the kernel environment.** `KernelClient.start`
   calls `subprocess.Popen` with no `env=`, so the whole host environment,
   including `DEEPSEEK_API_KEY` and `ANTHROPIC_API_KEY` loaded by `load_dotenv`,
   is readable from model-authored code. Fix: pass an explicit minimal `env`.
   Proof: a test that reads `os.environ` inside a kernel cell and asserts no
   key-shaped variable is present.
4. **`mcp_server.py:172` every gate auto-approved.** `_ask` answers every
   `GateRequest` with `GateDecision("run")`, with no grade check, so HUMAN-grade
   fixes and skill admissions are auto-approved. Fix: answer `run` only for
   AUTO; deny person grades and admission, and surface them to the client as
   unresolved. Proof: a test that a HUMAN-grade gate is not executed.
5. **`repl.py:271` `policy_decision("all")` auto-approves person grades.** It
   approves HUMAN findings, HUMAN skill admissions, and the PLAN gate,
   contradicting its own docstring, and `clean_file` exposes that policy as a
   free client argument. Fix: make the policy incapable of covering a person
   grade, and stop exposing it over MCP.
6. **`verify.py:156` and `:176` the preview screen is bypassable.**
   `preview_screen` is the only guard before model code runs ahead of human
   approval, and it misses subscript access to `__builtins__`, `getattr`/`vars`
   with string constants, `pd.read_pickle` (executes pickle), and `df.to_csv`
   (writes files). Fix: treat the screen as advisory, not a boundary; the real
   boundary is the sandbox (item 2). Tighten the AST checks and state honestly
   in the docstring that it is a smell test.
7. **`mcp_server.py:232` unconfined paths.** All three file tools accept an
   absolute client path, so a tool documented read-only and "always safe to call
   first" reads any file the server can read. Fix: confine to a configured root.
8. **`files.py:111` partial bomb guard.** The decompression-bomb guard runs only
   for `.gz` and `.zip`, so `.bz2` and `.xz` expand unbounded despite the
   docstring. Fix: guard all compressed formats, or refuse the ungurarded ones.

## P1. Gate semantics and the person-grade forbid

This is the safety identity. All of these are fail-open.

9. **`loop.py:1149`, `:1701`, `:1407`, `:1355`, `:349` a missing decision means
   run.** Every gate does `if not isinstance(decision, GateDecision): decision =
   GateDecision("run")`. This covers the HUMAN-grade fix gate and the
   skill-admission gate, whose line 1698 comment reads "admission is governance,
   and governance is never unattended" four lines above defaulting it to run.
   Plain iteration (`for ev in session.clean("df"): pass`) executes HUMAN-grade
   fixes. Fix: default to `skip` for anything not graded AUTO, and keep `run` as
   the default only for AUTO. Proof: a test driving the session by plain
   iteration and asserting no HUMAN fix and no admission executed.
10. **`loop.py:354` `skip` does not skip.** `run_turn` handles only `"reject"`;
    there is no `skip` branch, unlike the three other gate sites, so a
    `GateDecision("skip")` falls through to `_execute_cell`. The transcript then
    records skip followed by exec. Fix: handle `skip` as a non-execution path.
11. **`repl.py:188` unrecognized answer means run.** Typing the words the prompt
    itself offers ("skip", "reject") executes the cell. Fix: re-prompt on an
    unrecognized answer; never default to run at a human interface.
12. **`loop.py:1373` rejecting a plan keeps batching armed.**
    `_build_and_approve_plan` returns `proceed=True` on reject and leaves every
    earlier plan policy in `self.policies`, which are only appended and all share
    the id `plan-v1`. A plan approved earlier in the session keeps an ENFORCE
    policy live, so a rejected plan's AUTO steps still run silently. Fix:
    withdraw or supersede prior policies of the same id on reject.
13. **`agent_run.py:94` the bench driver auto-runs GATE.** `_drive` auto-runs
    every gate that is not exactly HUMAN, including GATE-grade judgement calls,
    contradicting the headless orchestrator contract. Fix: auto-run AUTO only.
14. **`autoclean.py:379` a HUMAN finding is silently dropped.** The final
    `needs_review` pass dedupes on `(disease, columns)` ignoring grade, so a
    HUMAN spelling-variant finding is dropped when an AUTO case-collapse finding
    shares the column. Fix: include grade in the dedupe key.
15. **`verify.py:227` verification reads state the cell can rewrite.**
    `detect_one` is re-imported from `crivo.detect`, which a model-authored cell
    can monkeypatch, and the backup and hashes are plain kernel globals. Fix:
    compute verification host-side from values the cell cannot reach.

## P2. Verification holes (these gate the autonomy build)

The shared mechanism: the fixer changes dtype, the detector returns `None` for
the new dtype, and absence of a finding is recorded as a verified fix.

16. **`detect.py:2140` verification by exact column list.** `detect_one` calls a
    fix verified whenever no re-run finding has an exactly equal column list, so
    a partial fix on a multi-column finding, or a fix that renames the diseased
    column, passes while the signal still fires. Fix: verify by column overlap,
    not list equality, and treat a rename as unverified.
17. **`verify.py:243` the untouched-column guard passes on drops and renames.**
    The loop skips any baseline column no longer present, and the per-column hash
    is an order-independent `.sum()` of row hashes, so dropping, renaming, or
    permuting a non-target column passes. Fix: assert the column set is
    unchanged, and use an order-dependent digest.
18. **`autoclean.py:31` `_fix_numbers` silently deletes and truncates.** Values
    `LEADING_NUMBER` cannot parse become NaN, and anything with a leading number
    is truncated to it (`'12-15'` becomes `12.0`). Confirmed: non-null 20 to 16,
    recorded applied and verified, `needs_review` empty. Fix: return the frame
    unchanged when the parse loses non-null values or a source string has
    trailing characters beyond a known unit suffix.
19. **`autoclean.py:44` `_fix_dates` misreads day-first dates.**
    `pd.to_datetime(errors="coerce")` with no format or `dayfirst` infers one
    format from the first value. Confirmed: 12 of 24 cells to NaT and
    `'05-01-2020'` read as May 1, recorded verified. Fix: refuse when the parse
    adds nulls or when `_slot_ambiguity` is true; otherwise parse with an
    explicit format.
20. **`autoclean.py:79` `_drop_constant` drops the wrong column.** With duplicate
    column names it removes the non-constant twin too, then `detect_one` finds no
    such column and calls the destructive drop verified. Fix: drop positionally,
    or refuse when the name is duplicated.
21. **`detect.py:545` d01 grades mixed units AUTO.** A column of heterogeneous
    unit suffixes (kg, lb, oz) is graded AUTO at confidence 1.0, and the fixer
    strips units and merges incompatible magnitudes. Fix: mixed unit families are
    a judgement call; grade GATE or HUMAN.
22. **`autoclean.py:60` and `:74` stringify mixed columns.** `_fix_whitespace`
    and `_fix_case_variants` run `astype(str)` over every non-null cell, so ints,
    floats and bools in an object column silently become strings, and the
    re-checks stringify too, so they cannot see it.
23. **`fingerprint.py:33` fingerprint collides on object columns.** The string
    `"1"` and the int `1` produce identical digests, which is exactly the case the
    docstring claims to handle, and the M1 re-check skip depends on it.
24. **`autoclean.py:108` `_fix_headers` docstring is false.** It promises the
    rename reverts on a frame with header-repeat rows. It does not, because the
    residual finding has empty columns and the equality test passes.

## P3. Receipts that cannot be false

25. **`card.py:126` fake green checks.** `lift_checks` walks the AST and stamps
    every `assert` `passed=True`, including asserts inside `if False`, inside
    `try/except AssertionError`, and inside function bodies that never ran;
    `passed` is never set false anywhere. These satisfy the R18 unchecked bounce.
    Fix: lift only asserts in the module body, or instrument the cell so the host
    records which asserts actually executed.
26. **`decompose.py:29` null categories vanish, receipt stays true.** `groupby`
    defaults to `dropna=True`, so null-category rows leave the totals while the
    receipt only re-adds the module's own numbers. Reported delta 0 against a true
    delta of 30. The receipt also flips false on correct decompositions from float
    accumulation. Fix: `dropna=False`, and compare against the source sums with a
    relative tolerance.
27. **`rowdiff.py:295` partition receipt true by construction.** Still tautological
    after the reconcile fix pass that claimed to make it a real invariant. Fix:
    assert against independently computed key sets.
28. **`run.py:37` vacuous bench invariants.** The oracle and no-op scorer checks
    are structural tautologies that cannot fire, yet the run stamps
    `"invariants": "ok"` into the results and RESULTS.md.
29. **`loop.py:1291` intent check coerces to match.** Any verdict other than the
    literal "mismatch" is recorded as match, so an unparseable reply becomes a
    passing check that never produced a verdict.
30. **`loop.py:507` `clean()` returns True on a failed diagnosis.** Including the
    early return where no report was written and nothing was checked, which
    `_family_body` then counts as a cleaned slice.

## P4. PII escapes shareable artifacts

31. **`htmlreport.py:51`** the PII gate masks only its own section, while
    `_findings_table` prints evidence verbatim and `detect._samples` embeds raw
    cell values, so raw PII travels in the shareable HTML on a page stating it
    never carries personal data.
32. **`api.py:171`** `Report.to_html`'s no-raw-PII claim is false for the same
    reason, via the embedded notebook card.
33. **`rowdiff_report.py:87`** `reconcile_report` writes raw before/after and key
    values with no PII gate and no length cap.
34. **`pii.py:94`** with a duplicated column name `df[col]` is a DataFrame, the
    dtype checks return False, and the column is silently skipped, so the gate
    reports "none detected" on a frame full of emails.
35. **`notebook.py:214`** renders before/after example cells raw, unmasked and
    uncapped.

Fix for the group: route every output surface through one masking helper, and
make the PII scan duplicate-name safe (positional iteration).

## P5. Measurement integrity

36. **`agent_run.py:299` arms collide on disk.** The `--policies` arm is absent
    from result and telemetry filenames, so the batched arm resumes from the
    baseline arm's files and reports the baseline's numbers as its own, and the
    saved row records no policy field. This is the arm confusion from the M1 vs
    M2 measurement. Fix: put the arm in the filename and record it in the row.
37. **`score_fixes.py:39` wrong digits score as a perfect repair.** `_norm` rounds
    to 12 significant digits; the shipped scorer has the same hole at a looser
    tolerance.
38. **`score.py:229` recall is inflated.** `score_detection` counts true positives
    over findings but false negatives over truth corruptions, so the two do not
    sum to the truth count.
39. **`run.py:79` survival rests on a hidden denominator.** `survived_rate_mean`
    drops every dataset where crivo attempted nothing and publishes no
    denominator.
40. **`agent_run.py:151` a paid run can report zero cost.** `new_work_tokens` is
    emitted as a hard 0 when no token span attribute is found.
41. **`score.py:63`** the bool guard misses `np.bool_`, so a numpy boolean equals
    1 and the "True and 1 are not the same fix" promise fails.

**Re-run the bench after 36 to 41 before trusting any prior comparison.**

## P6. Detector honesty and correctness

42. **`detect.py:1148`** d12's scan is silently capped (20 dependents, 12
    determinants, 16 pairs above 20k rows) yet 12 is listed in `clear`, so a real
    contradiction beyond the cap is reported as a checked absence.
43. **`detect.py:1681`** d19 calls a column with `null_frac >= 0.995` empty at
    AUTO confidence 1.0, so autoclean drops columns still holding real values.
44. **`detect.py:695`** d04's text path grades sentinel dates and the word "none"
    AUTO, while the numeric path grades 0 and -1 HUMAN for the same ambiguity.
45. **`detect.py:639`** d02 and d01 evidence count every value as matching even
    when up to 10 percent match no known shape.
46. **`detect.py:1336`** d14 counts NaN coordinates as out of range because
    `~Series.between()` is true for NaN.
47. **`detect.py:108`** d08's single-character mojibake markers fire on legitimate
    Portuguese, Spanish and Icelandic text.
48. **`excel_structure.py:69`** a one-cell title directly above the header is
    absorbed and reported "clean, header on row 1" at AUTO.

## P7. Remaining correctness

49. **`loop.py:1361` plan expiry local versus UTC.** Expiry is minted from the
    local date but `policy.py:86` compares the UTC date, so between 17:00 and
    midnight in UTC-7 an approved plan is born expired and arms nothing. The
    reviewer reproduced `tests/test_a1_m2_loop.py::
    test_plan_first_on_approves_once_then_autoclean_runs_silent` failing at 19:39
    PDT. It passes in the morning. This is a time-bomb test, not a flake.
50. **`ingest.py:42`** `load_url` accepts `file://` and `ftp://` and stamps a local
    file as remote lineage; the read truncates silently with no flag.
51. **`llm.py:327`** `_watched` clears the one-shot cancel event on entry, so a
    cancel landing during the provider call is discarded after the REPL already
    said it was cancelling.
52. **`loop.py:579`** the R8 snapshot cell uses the host path even when
    `docker=True`, unlike the two sibling call sites.
53. **`skills.py:67`** `_scalar` does not escape newlines and `validate` does not
    reject them, so a multi-line description writes a SKILL.md that cannot be
    parsed back; the admitted skill is unreadable.
54. **`api.py:202`** `Report.suggest()` raises on duplicate column names, exactly
    the dirty shape `diagnose()` reports on.
55. **`governance.py:93`** the fail-closed, uniform-ValueError contract is false:
    a non-object judge raises TypeError, and unknown keys inside judge or routing
    are accepted silently.
56. **`__main__.py:83`** every non-parquet file is parsed as CSV, so a JSON or
    JSONL file gets findings and a clear receipt over a mangled frame, disagreeing
    with the library entry point on the same file.

## Suggested order of work

1. **P0** as one security packet, before any publish or public endpoint.
2. **P1** as one packet: make every gate fail-closed and add the driving test
   that plain iteration executes nothing person-grade.
3. **P2** as small per-fixer packets, each with the corrupting input as a
   regression test. This unblocks the autonomy build.
4. **P5**, then re-run the bench, because later decisions read those numbers.
5. **P3** and **P4** together, since both are honesty of output surfaces.
6. **P6** and **P7** as ordinary bug work.

## Non-goals

This plan does not redesign the sandbox, does not change the grading taxonomy,
and does not touch the autonomy or P7 detector designs beyond noting that the
autonomy build waits on P1 and P2.
