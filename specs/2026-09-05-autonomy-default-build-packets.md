# Autonomy-default mode: build packets (P7 / autonomy roadmap)

2026-09-05. **Status: build-packet spec for owner review.** This decomposes
the approved direction in `specs/2026-09-05-autonomy-default-design.md` into
five ordered, self-contained reviewed diffs. It touches core (`policy.py`
default factory, `loop.py` threading and silence), so it runs spec-first as
reviewed packets, the way M1/M2/P7 did. No code until reviewed. Every packet is
grounded in the current code, with file and line citations to what exists today.

## What

An explicit `autonomy` setting whose default is autonomous. In autonomous mode
the AUTO findings that already carry a registered deterministic fixer apply
unattended (no gate is shown), because a standing default policy batches them
through the existing verified apply-verify-revert path (`policy.evaluate`
batched, `_autoclean_attempt` silent). Human approval becomes the opt-in
`careful` mode, and `report-only` is a named diagnose-and-stop level.

This phase does NOT auto-apply GATE findings. The design's open question 2 asked
whether to start with the GATE-with-registered-fixer subset. Reading the code,
that subset is exactly one case (d01's ambiguous number-convention finding), and
its re-check cannot tell a correct resolution from a corruption, so GATE stays on
today's gated path this phase (resolved question 2 carries the full reasoning).
The person-grade forbid and HUMAN-reporting are unchanged in every mode.

The design spec carries the "why" (the ceiling-arm evidence) and the level
definitions. This spec carries the "how", packet by packet.

The load-bearing observation, confirmed by reading the code: in this phase
autonomy is a silence decision (which AUTO-with-fixer findings apply without a
gate), not a loosening of any safety check. The person-grade forbid, the
router's grade rules, the driver-side pre-authorisation guard, and
verify-then-revert all stay exactly as they are. Autonomy changes only whether
the AUTO-with-fixer gate is shown, and it is structurally incapable of running a
person-grade finding unattended because three independent guards each refuse it.
GATE auto-apply is not enabled at all this phase.

## Resolved open questions (recommended, owner to confirm)

The design spec left four open questions. Recommended answers, each labelled for
the owner to confirm before code:

1. **Default reach (recommended, owner to confirm): ship autonomous as the
   default WITH a first-run notice the first time it acts unattended, not a
   silent flip.** Packet 3 builds this notice. It rides the existing
   `events.Notice` channel (`src/crivo/events.py:49-54`, fields `kind` and
   `text`), emitted the first time an autonomous session applies a fix silently
   under the default `autonomy-auto` policy. `events.Notice` is an ephemeral
   yielded event with no persistence, so "once per workspace" is not something
   the notice channel provides on its own: Packet 3 enforces it with a sentinel
   file under the workspace root (`self.workspace_root / ".crivo-autonomy-notice"`),
   firing the notice only when that file is absent and creating it immediately
   after (best-effort). Rationale: the default should be autonomous (the market
   and the measurement both point there), but a person deserves to be told the
   first time software edits their data without asking.

2. **GATE scope (owner to confirm): defer GATE auto-apply this phase; ship
   autonomous on the AUTO-with-fixer majority only.** The design proposed
   starting with GATE findings that have a registered fixer (mirroring the M1
   AUTO rung). Reading the code, that subset is a single case, and it is not
   soundly verifiable:

   - The GATE-with-fixer subset is the intersection of `crivo.autoclean.FIXERS`
     (`autoclean.py:137-146`, disease ids {1, 2, 4, 6, 7, 18, 19, 23}) with the
     diseases `detect.py` can grade GATE. Grepping every `"GATE"` grade in
     `detect.py`, the GATE-capable single-frame diseases are {1, 3, 8, 13, 14,
     21, 22, 24, 25}, plus disease 20 via `detect_family` (`detect.py:2191`, not
     reachable from `detect_all`). Disease 23 grades only AUTO
     (`detect.py:2076`; the `"GATE"` at `:2191` belongs to `detect_family` /
     d20, a different function). So the intersection is exactly {disease 1}, and
     d01 is GATE only in its `ambiguous` branch (`detect.py:537`, guarded by
     `:499-501`: a decimal comma and a thousands comma both present, the same
     digits read two ways).

   - That one case cannot be re-checked for correctness. Its registered fixer
     `_fix_numbers` strips all commas unconditionally (`autoclean.py:33-35`,
     `.str.replace(",", "", regex=False)`), always choosing the thousands
     reading. `verify_cell` re-runs `detect_one(var, disease, cols)`
     (`verify.py:227-231`); after the comma strip the column parses as numbers,
     so d01 goes quiet and verification passes no matter which reading was
     correct ("1,5" becomes 15, a 10x corruption, verifies identically to the
     right answer). Verify-then-revert (R2) still holds mechanically, but the
     re-check confirms "numeric now", not "the convention split was resolved
     correctly", which is exactly the judgement the GATE grade exists to require.

   - So this phase does not build a GATE-with-fixer auto-apply rung: there is no
     GATE-with-fixer case whose re-check validates the resolution. GATE findings
     stay on today's gated path (routed to the model by `router.route`,
     `router.py:40-41`, and deferred by `policy_decision`, `repl.py:271-273`).
     GATE auto-apply waits until a GATE-with-fixer disease exists whose re-check
     validates the resolution itself (a P7-era relational or cross-column GATE
     with a reference check, for instance), at which point R3 is the governing
     rule. Rationale: autonomy pays, safely, on the verifiable-and-revertible
     AUTO majority now; widening to GATE is real work that needs a real
     verifier, not a comma strip.

3. **report-only (recommended, owner to confirm): keep it as a named level.** It
   short-circuits the fix loop and writes the report from diagnosis alone. It is
   close to `diagnose()` in effect, but naming it as a third autonomy level
   means the one setting spans the full spectrum (decide everything verifiable /
   decide nothing / gate everything) rather than sending users to a different
   command for the "look but do not touch" case.

4. **careful ergonomics (recommended, owner to confirm): batch through the M2
   plan-first gate by default, per-finding gates a later option.** `careful` is
   today's behavior unchanged, and today's plan-first path
   (`_build_and_approve_plan`, `src/crivo/loop.py:1332-1373`, behind
   `CRIVO_PLAN_FIRST`) is the coherent-unit approval. Per-finding gates remain
   available as they are today; a dedicated toggle is deferred until asked for.

## The hard line (stays in code, cited to where it lives today)

Every packet below preserves these, and each packet restates the two that apply
to it. None of these lines is edited by any packet.

- **The person-grade forbid is enforced in `policy.evaluate`,
  `src/crivo/policy.py:108-111`:**

  ```python
  disease = finding["disease"]
  grade = finding["grade"]
  if grade != "AUTO":
      decision = _deny("grade", f"grade {grade}: person-grade findings never batch")
  ```

  It reads only `"grade"` and `"disease"` (so a model-authored key cannot move
  it) and it is checked before any policy is read (`evaluate` docstring,
  `src/crivo/policy.py:95-107`). The default policy that Packet 2 adds is keyed
  by disease id, and `evaluate` checks grade first, so a disease id appearing in
  the default policy still cannot silence that disease's GATE or HUMAN findings.
  This is why GATE auto-apply cannot be reached by adding ids to the default
  policy, and, since this phase ships no separate GATE rung either (resolved
  question 2), GATE is not auto-applied at all.

- **The router never routes a person grade to auto,
  `src/crivo/router.py:40-43`:** GATE and HUMAN always return the model
  executor. Packet 3 does not edit `router.py` and adds no GATE auto-apply rung;
  GATE and HUMAN still route to the model.

- **The driver-side mirror never approves a person's judgement call on their
  behalf:** `repl.policy_decision` (`src/crivo/repl.py:267-273`) approves AUTO
  and forces skip on every non-AUTO grade; the bench driver
  (`bench/agent_run.py:90-95`) runs non-HUMAN gates and forces skip on
  `grade == "HUMAN"` and on `admit skill` titles. This phase changes neither:
  AUTO-with-fixer is silenced upstream by the seeded default policy (its gate
  never reaches either driver), and no GATE is auto-applied, so no GATE approval
  is added anywhere. HUMAN is never approved.

- **Verify-then-revert is mandatory in every rung and every mode:** an applied
  fix is kept only when `verify.verify_cell` returns status ok, else
  `verify.revert_cell` runs. Live today in `_autoclean_attempt`
  (`src/crivo/loop.py:1433-1457`), `_fix_mini_turn`
  (`src/crivo/loop.py:1215-1231`), and `_skill_attempt`
  (`src/crivo/loop.py:1533-1564`). No packet relaxes this.

- **Skill admission stays HUMAN:** `_propose_skill` gates admission with
  `grade="HUMAN"` (`src/crivo/loop.py:1690-1700`) and `_admit` runs both
  execution gates (`src/crivo/loop.py:1724-1744`). Autonomy never admits a
  skill.

## Requirements (carried from the design spec)

- **R1 Forbid unchanged in code.** Person-grade and skill admission are never
  auto-decided in any mode. The existing forbid test still passes.
- **R2 Verification unchanged.** Verify-then-revert is mandatory in every mode.
- **R3 GATE auto-apply is conditional on a passing re-check.** This phase ships
  no GATE auto-apply (resolved question 2), so R3 governs the future rung: when
  GATE auto-apply lands it must revert any GATE fix whose re-check fails, and its
  re-check must validate the resolution, not merely that the signal went quiet
  (the d01 lesson).
- **R4 Truthful recording in every mode.** The provenance records the autonomy
  level and every silently-applied fix with its verification, so an autonomous
  run is at least as auditable as a careful one.
- **R5 The mode is explicit and reversible.** This phase makes it reversible
  with one setting via the `--autonomy` flag (default `autonomous`,
  `--autonomy careful` flips it back). The governance `autonomy` key (Packet 1)
  persists the choice in the config file; wiring the runtime to read it is
  deferred (no runtime governance reader exists today, see Packet 3 wiring).

---

## Packet 1: the `autonomy` setting in `governance.py`

Config only. No policy or loop code changes, so the forbid and verify-then-revert
are untouched by construction.

**Files and functions.** `src/crivo/governance.py`: module constants near
`_TOP_LEVEL_KEYS` (`:28-30`); the `Governance` dataclass (`:33-43`);
`DEFAULT_GOVERNANCE` (`:46-50`); `load_governance` (`:53-103`);
`save_governance` (`:106-119`).

**Interface change.**
- Add module constants: `AUTONOMY_LEVELS = ("autonomous", "careful",
  "report-only")` and `_DEFAULT_AUTONOMY = "autonomous"`.
- Add `"autonomy"` to `_TOP_LEVEL_KEYS` (so the fail-closed unknown-key check at
  `:81-83` still accepts a governance file that sets it).
- Add a field `autonomy: str` to `Governance` (the fourth field after
  `policies`, `judge`, `routing`).
- In `load_governance`, after the routing block (`:102`), read
  `autonomy = data.get("autonomy", _DEFAULT_AUTONOMY)` and validate membership,
  raising `ValueError(f"{path}: autonomy {autonomy!r} not one of
  {AUTONOMY_LEVELS}")`, mirroring the `sample_rate` validation shape at
  `:99-100`. Pass `autonomy=autonomy` into the returned `Governance`.
- In `save_governance`, add `"autonomy": gov.autonomy` to the `data` dict
  (`:114-118`) so `load_governance(save_governance(gov))` round-trips.
- Set `autonomy="autonomous"` in `DEFAULT_GOVERNANCE`.

**Acceptance.**
- `load_governance` on `{"autonomy": "careful"}` yields `gov.autonomy ==
  "careful"`; on a file with no `autonomy` key yields `"autonomous"` (the
  missing-key default, like a missing `"policies"` today).
- `load_governance` on `{"autonomy": "yolo"}` raises `ValueError` naming the
  path and the bad value; the load is all-or-nothing (no partial Governance).
- `load_governance(save_governance(gov))` preserves `autonomy` for all three
  levels and for `DEFAULT_GOVERNANCE`.
- The existing fail-closed unknown-key test is updated to keep an actually
  unknown key failing while `"autonomy"` is now accepted. No other governance
  test changes.

**Steps.**
1. Add `AUTONOMY_LEVELS` and `_DEFAULT_AUTONOMY`.
2. Extend `_TOP_LEVEL_KEYS`.
3. Add the `autonomy` field to `Governance`, and set it in `DEFAULT_GOVERNANCE`.
4. Parse and validate it in `load_governance`.
5. Serialize it in `save_governance`.
6. Update the unknown-key test fixture.

**Risk and invariant.** Risk: a governance file written before this packet has
no `autonomy` key; the missing-key default keeps it valid, matching how a
missing `"policies"` is already handled (`:62-63` docstring). Invariant not to
break: fail-closed loading (an unknown top-level key still raises) and
round-trip fidelity. The forbid (`policy.py:108-111`) and verify-then-revert are
untouched because no policy or loop code is edited.

**Wiring note (why this key is not yet read).** This packet lands the config
schema and round-trip only; no runtime code reads `gov.autonomy` yet. The
runtime autonomy source this phase is the `--autonomy` flag (Packet 3). Wiring
`load_governance(...).autonomy` into the Session is a separate, deferred task,
because `load_governance` has zero runtime callers today (confirmed:
`grep -rn load_governance src/ bench/` finds only its own definition and
docstrings) and no governance-file path or CLI-vs-file precedence is defined in
the runtime. The owner may therefore choose to land Packet 1 alongside that
wiring rather than now; it is included here because the design calls for an
`autonomy` key in governance (design `:79`), and the schema plus round-trip are
self-contained, testable work.

---

## Packet 2: the default policy in `policy.py`

A pure factory that mints the standing ENFORCE policy for the autonomous
default. It does not touch `evaluate`, `_deny`, `PolicyRecord`, or `MODES`. The
forbid at `policy.py:108-111` is not edited.

**Files and functions.** `src/crivo/policy.py`: new module-level function
`default_autonomous_policies`; reuses the existing `PolicyRecord` dataclass
(`:23-71`) and `MODES` (`:20`). `evaluate` (`:92-144`) is read, not changed.

**Interface change.** Add:

```python
def default_autonomous_policies(valid_disease_ids, fixers, expires="2099-01-01"):
    """The standing ENFORCE policy the autonomous default arms over AUTO
    findings with a registered deterministic fixer (mirrors the bench
    `bench-auto` arm and the plan-approval policy). Pure and keyless: the
    taxonomy's valid ids and the fixer registry arrive as parameters, so this
    module still imports neither detect nor autoclean. Returns a one-element
    list. It CANNOT silence a GATE or HUMAN finding: evaluate checks grade
    before disease, so a disease id here still denies its non-AUTO findings."""
    return [
        PolicyRecord(
            id="autonomy-auto",
            disease_ids=tuple(sorted(fixers)),
            approver="autonomy-default",
            expires=expires,
            mode="ENFORCE",
            valid_disease_ids=valid_disease_ids,
        )
    ]
```

This is the shipped-default twin of the bench `bench-auto` record
(`bench/agent_run.py:278-287`) and the in-session plan-approval record
(`src/crivo/loop.py:1362-1372`). Returning a `PolicyRecord` means every id is
validated against the taxonomy at admission (`PolicyRecord.__post_init__`,
`src/crivo/policy.py:42-49`). The policy id `autonomy-auto` is the marker Packet
3's first-run notice keys on to tell an autonomy-default silence from a
user-supplied policy silence.

**Acceptance.**
- `default_autonomous_policies(valid, fixers)` returns one ENFORCE
  `PolicyRecord` whose `disease_ids` equals `tuple(sorted(fixers))`, and every
  id admits without error.
- For an AUTO finding whose disease is in `fixers`, `evaluate(finding, that_list)`
  returns `batched=True, mode="ENFORCE"`.
- For a GATE finding whose disease is the same id, `evaluate` returns
  `batched=False` with `denial["condition"] == "grade"`. Same for a HUMAN
  finding. This is the forbid proving the default policy cannot silence a person
  grade.
- The existing forbid test is unchanged and green.

**Steps.**
1. Add the factory.
2. Add a unit test asserting a GATE finding of a listed disease is still denied
   on grade (the forbid), alongside the AUTO finding being batched.

**Risk and invariant.** Risk: a future edit adds GATE-graded ids to the factory
list expecting it to enable GATE auto-apply; it cannot, because `evaluate`
denies non-AUTO on grade first, and this phase ships no GATE auto-apply rung at
all (resolved question 2). Keep the factory AUTO-only and say so in the
docstring. Invariant not to break: the grade-first forbid in `evaluate`
(`:108-111`) is untouched, and this module still imports no taxonomy and no
fixer registry (they arrive as parameters). Verify-then-revert is unaffected:
`policy.py` runs no fixes.

---

## Packet 3: loop threading by autonomy level (AUTO silence, report-only, first-run notice)

The behavioral core for this phase. It threads the level onto the session, seeds
the default AUTO policy so AUTO-with-fixer applies silently, short-circuits
`report-only`, and emits the first-run notice. It adds NO GATE auto-apply rung
(resolved question 2), does not edit `router.py`, does not edit
`policy.evaluate`, and does not edit `repl.policy_decision`.

**Files and functions.** `src/crivo/loop.py`: `Session.__init__` (`:119-190`,
signature `:119-130`, the `self.preview`/`self.policies` block `:133-136`, the
session-meta append `:169-175`); `_clean` (`:509-585`, the diagnosis-text emit
`:533-542` and `_snapshot_baseline` `:544`, the per-finding routing block
`:562-567`); `_autoclean_attempt` (`:1375-1463`, the silence decision `:1390`,
the gate-note `:1400`, the verified-success branch `:1437-1453`). `router.route`
(`src/crivo/router.py:19-44`) is read and left pure. Outside `loop.py`:
`src/crivo/__main__.py` `main` (arg block `:12-64`, the two Session
constructions `:128-133` and `:155-163`); `src/crivo/mcp_server.py`
`_make_session` (`:49-58`); `src/crivo/query.py` `_make_session` (`:33-40`).

**Interface change.**
- Autonomy on the session. `Session.__init__(..., autonomy: str = "autonomous")`,
  storing `self.autonomy = autonomy` next to `self.preview` (`:133`), so it is
  set before the session-meta append (`:169-175`, which Packet 4 stamps). After
  `self.policies = list(policies or [])` (`:136`), when
  `self.autonomy == "autonomous"` and no `policies` were passed (`not
  self.policies`), seed:

  ```python
  if self.autonomy == "autonomous" and not self.policies:
      from crivo.detect import SLUGS
      from crivo import autoclean
      self.policies = policy.default_autonomous_policies(set(SLUGS), autoclean.FIXERS)
  ```

  (the lazy `from crivo.detect import SLUGS` mirrors `_build_and_approve_plan`,
  `:1359`.) This makes AUTO-with-fixer findings run silently through the existing
  `policy.evaluate` batched path (`_autoclean_attempt`
  `silent = verdict["batched"]`, `:1390`), with no change to `evaluate` and no
  forbid change. Seed only when `policies` is falsy, so a caller that passes
  explicit policies keeps its own.
- report-only short-circuit. In `_clean`, after the diagnosis text is emitted
  (`:533-542`) and before `_snapshot_baseline` (`:544`), add:

  ```python
  if self.autonomy == "report-only":
      yield from self._save_report(state)
      return
  ```

  At that point `state` already holds `fixable`, `indicators`, `clear`,
  `broken` (set from the diagnosis at `:529-532`) and the empty
  `records`/`outputs`/`stats`/`admitted` from the caller's init (`:472-482`), so
  `_save_report` (`:740-768`) writes a valid diagnosis-only report and applies
  nothing.
- First-run notice (resolved question 1). In `_autoclean_attempt`, in the
  verified-success branch (`:1437-1453`, where a silently-applied fix has passed
  its re-check), when the winning policy is the autonomy default
  (`verdict["policy_id"] == "autonomy-auto"`) and the workspace sentinel is
  absent, yield one `events.Notice("autonomy", "...")` telling the person
  autonomous mode edited the data without asking, then create the sentinel
  `self.workspace_root / ".crivo-autonomy-notice"` (best-effort, wrapped in
  `try/except OSError`, so a read-only workspace never fails a fix). The sentinel
  makes the notice fire once per workspace root across sessions. This Notice is
  distinct from the existing per-fix `Notice("autoclean", ...)` (`:1438-1442`)
  and from the transcript "gate" note (`:1401`, which records every silent
  apply); the transcript note is not a suppression mechanism, the sentinel file
  is.
- No routing change and no GATE rung. The per-finding routing block in `_clean`
  (`:562-567`) is left exactly as today: `router.route` returns `autoclean` only
  for AUTO-with-fixer, and GATE and HUMAN route to the model. There is no
  `_unattended` helper and no widening of the routing to GATE this phase
  (resolved question 2).
- Wiring (outside `loop.py`).
  - `src/crivo/__main__.py` `main` gains
    `--autonomy {autonomous,careful,report-only}` (default `autonomous`),
    threaded as `autonomy=args.autonomy` into both Session constructions: the
    headless `--clean` path (`:128-133`, which drives `run_clean_once`) and the
    interactive REPL path (`:155-163`). This is the live autonomy source this
    phase.
  - `repl.policy_decision` (`repl.py:267-273`) is UNCHANGED: it approves AUTO and
    forces skip on every non-AUTO grade. It needs no autonomy awareness, because
    AUTO-with-fixer is silenced upstream by the seeded policy (the gate never
    reaches `policy_decision`), and no GATE is auto-applied (resolved question
    2). Giving it GATE approval would auto-run GATE findings that reach it, which
    are precisely the GATE findings with no registered fixer (`policy_decision`
    has only the event and a policy string, `:267-273`, no
    `autoclean.FIXERS` awareness, so it structurally could not restrict itself
    to the registered-fixer subset). Not intended.
  - The governance `autonomy` key (Packet 1) is NOT read at runtime this phase.
    `load_governance` has zero runtime callers today, and no governance-file
    path or precedence is defined in the runtime, so wiring it is a separate
    deferred task; the `--autonomy` flag is the sole runtime source now.
- All five Session construction sites (`grep -rn "Session(" src/ bench/`), and
  each one's autonomy source under this packet:
  1. `src/crivo/__main__.py:128` (headless `--clean`): `autonomy=args.autonomy`
     (new flag, default `autonomous`).
  2. `src/crivo/__main__.py:155` (interactive REPL): `autonomy=args.autonomy`.
  3. `bench/agent_run.py:191` (bench): `autonomy=args.autonomy` from the bench
     arm selector (Packet 5).
  4. `src/crivo/mcp_server.py:55` (`_make_session`, which drives
     `run_clean_once` via `_clean_file`, `:61-84`): pass an explicit
     `autonomy="careful"` so the MCP clean surface stays conservative (today's
     behavior) rather than silently inheriting the autonomous constructor
     default; a dedicated MCP autonomy control is out of scope here. Stating this
     explicitly is required so the MCP surface does not flip behavior
     unannounced.
  5. `src/crivo/query.py:37` (`_make_session`, the read-only `ask` path):
     inherits the constructor default but never drives the clean loop (`ask`
     calls `session.run_turn`, `query.py:114`, not `clean`), so autonomy is
     inert there; no behavioral change and no knob needed. Left at the default is
     fine.

**Acceptance.**
- A `/clean` at `autonomous` over a frame with an AUTO finding whose disease is
  in `FIXERS`: the fix applies with no `GateRequest` yielded,
  `verify.verify_cell` runs, and on pass the record is `status == "fixed"`,
  `origin` starting `autoclean:`, with a transcript "gate" event whose note is
  `policy:autonomy-auto`.
- The first such silent apply in a fresh workspace emits one `Notice` (kind
  `autonomy`); a second session under the same workspace root emits none (the
  sentinel exists).
- The same AUTO finding when its re-check is made to fail: `verify.revert_cell`
  runs, the fix is not kept, and the finding is handed to the model (R2 and R3
  for AUTO).
- A GATE finding at `autonomous`: `router.route` returns the model executor
  (unchanged), the finding is not auto-applied, and it is gated or deferred
  exactly as today.
- A HUMAN / person-grade finding at `autonomous`: reported, never auto-applied
  (the forbid at `policy.py:108-111`, the router at `:42-43`, and the driver
  mirror each refuse it).
- A `/clean` at `careful` reproduces today's behavior byte-for-byte: no default
  policy is seeded, so `policy.evaluate` returns `batched=False`,
  `_autoclean_attempt` yields the AUTO gate exactly as today.
- A `/clean` at `report-only` writes a diagnosis-only report and applies
  nothing.
- The forbid test and every existing loop test pass.

**Steps.**
1. Add the `autonomy` param, `self.autonomy` (set before the session-meta
   append), and the default-policy seeding to `__init__`.
2. Add the `report-only` short-circuit in `_clean`.
3. Add the first-run notice and its sentinel to `_autoclean_attempt`'s
   verified-success branch.
4. Add `--autonomy` to `__main__.main` and thread it into the two Session
   constructions; set `autonomy="careful"` explicitly in
   `mcp_server._make_session`.
5. Leave `router.route`, `policy.evaluate`, and `repl.policy_decision`
   unchanged.

**Risk and invariant.** Risk: seeding default policies when a caller passed
explicit policies would override their intent; seed only when `policies` is
falsy. Risk: the first-run notice firing on every silent apply, or a sentinel
write crashing a fix; gate it on the sentinel and the `autonomy-auto` policy id,
and wrap the write in try/except. Invariants not to break: the person-grade and
skill-admission forbid stays enforced (this packet edits neither
`policy.evaluate` nor `router.route`, and seeds an AUTO-only policy that
`evaluate` still denies for non-AUTO on grade first); verify-then-revert stays
mandatory (the apply/verify/revert cells in `_autoclean_attempt` are unchanged);
and GATE is not auto-applied (no rung is added).

---

## Packet 4: provenance records the level and each silent apply

Observer-only. It records the autonomy level and marks each silently-applied
fix. It changes no control flow, so the forbid and verify-then-revert are
untouched; it only reads and writes records.

**Files and functions.** `src/crivo/report.py` `CleanReport` dataclass (`:14`,
constructed in `_save_report` at `src/crivo/loop.py:752-766`) and its
`to_markdown` (`:36`); `src/crivo/loop.py` the `__init__` session-meta append
(`:169-175`), `_save_report` (`:740-768`), and the records returned by
`_autoclean_attempt` (`:1443-1453` for the verified case, `:1415-1424` for the
skipped case); optionally `src/crivo/provenance.py` `_add_report` (`:150-198`).

**Interface change.**
- `CleanReport` gains a field `autonomy: str = "autonomous"` (a dataclass
  default, so existing constructions and report tests still build). `to_markdown`
  gains one line naming the level (alongside the existing header lines,
  `:45-49`).
- `_save_report` passes `autonomy=self.autonomy` into the `CleanReport(...)`
  call (`:752-766`).
- The `__init__` session-meta append (`:169-175`) gains `autonomy=self.autonomy`,
  so the transcript's first record carries the level (Packet 3 sets
  `self.autonomy` before this point).
- The record dicts returned by `_autoclean_attempt` gain `"unattended": silent`
  (the local `silent`, `:1390`): True when the fix was applied without a
  `GateRequest` shown to a human. The record already carries `"origin"`
  (`autoclean:dNN`), `"verify"` (`{"layer1": "pass"|"fail", "by":
  "autoclean:dNN"}`), and the `"finding"` with its `"grade"`, and the transcript
  "gate" event carries the `policy:autonomy-auto` note, so a silently-applied
  AUTO fix is fully described: what ran, that no gate was shown, which policy
  silenced it, and how it verified.
- Optional: `provenance._add_report` (`:150-198`) reads `rec.get("unattended")`
  onto the fix node's meta (it already stamps `origin` and
  `checks_passed=verified` at `:176-186`), and reads `report.get("autonomy")`
  onto the variable or source node.

**Acceptance.**
- After an autonomous `/clean` that silently applies an AUTO-with-fixer finding:
  the transcript `session_meta` record carries `autonomy == "autonomous"`; the
  `CleanReport` JSON carries `autonomy` and a `fixes[]` record with `grade ==
  "AUTO"`, `origin` starting `autoclean:`, `unattended == True`, and
  `verify["layer1"] == "pass"`.
- An AUTO fix whose re-check failed appears with `verify["layer1"] == "fail"`, is
  reverted, and is absent from `outputs`.
- `provenance.build` over that session (`src/crivo/provenance.py:26`) produces a
  fix node with `origin` autoclean and `checks_passed` matching the real
  verification verdict. An autonomous run is at least as auditable as a careful
  one (R4).
- Existing report and provenance tests pass (the new `CleanReport` field has a
  default).

**Steps.**
1. Add `autonomy` to `CleanReport` and a line to `to_markdown`.
2. Pass `autonomy` in `_save_report`.
3. Add `autonomy` to the session-meta append.
4. Add `"unattended"` to the `_autoclean_attempt` records.
5. Optional: surface `unattended` and `autonomy` in `provenance._add_report`.

**Risk and invariant.** Risk: adding a `CleanReport` field without a default
would break every existing construction and report test; give it a default.
Invariant not to break: recording is observer-only and must not alter control
flow, so the forbid (`policy.py:108-111`) and verify-then-revert stay enforced;
and the recorded verification verdicts must be the real ones lifted from
`verify.verify_cell`, never fabricated. Skill admission is still recorded as
HUMAN (`_propose_skill`, `loop.py:1690-1700`), unchanged.

---

## Packet 5: a bench arm comparing autonomous vs careful

Reuses the existing agent bench. Adds one arm selector and a result suffix so
the two arms are scored the same way and do not collide with each other or with
the existing `.ceiling` results. It adds no new driver logic and does not weaken
the HUMAN skip.

**Files and functions.** `bench/agent_run.py`: `_drive` (`:61-101`, the
grade-based approval at `:90-95`); `_run_case` (`:159-232`, the `Session(...)`
construction at `:191-199` and the `.ceiling` telemetry suffix at `:184-185`);
`main` (`:240-352`, the argparse block and the `.ceiling` file suffix at `:299`);
it reuses `score_end_to_end` (`:213`) and the summary print (`:329-337`).

**Interface change.**
- Add `--autonomy {autonomous,careful}` (no `report-only`: the bench scores
  cleaned output, and a report-only run cleans nothing). Default: unset (today's
  behavior).
- Map each arm to existing knobs, no new driver logic:
  - `autonomous`: build the `Session` with `autonomy="autonomous"` (available
    after Packet 3, which seeds the default `autonomy-auto` policy so
    AUTO-with-fixer applies silently) and drive with `human_gates="skip"`. Leave
    `--policies` at `none` so the session seeds its own default (the seeding
    fires only when `policies` is falsy, and `_run_case` passes the empty
    `args.policy_records`, `:198`).
  - `careful`: build with `autonomy="careful"` and drive with
    `human_gates="skip"` (today's governed arm, no default policy seeded, so
    AUTO-with-fixer yields a gate that the driver runs).
  - Both arms use `human_gates="skip"`, so `_drive` runs non-HUMAN gates and
    forces skip on HUMAN and `admit skill` titles (`:90-95`), unchanged. Do not
    combine `--autonomy` with the legacy `--human-gates approve` (the ceiling
    arm); when `--autonomy` is set the arm drives with `skip`.
- `_run_case` passes `autonomy=args.autonomy` into `Session(...)` (`:191-199`).
- Result and telemetry suffix: extend the existing `.ceiling` computation
  (`:184-185` and `:299`) so it is `.ceiling` for the legacy approve arm, else
  `.autonomous` / `.careful` when `--autonomy` is set, else empty. Arms then
  write distinct files and the existing `.ceiling.json` results are left in
  place. Because the autonomous and careful arms both drive with
  `human_gates="skip"`, neither triggers the `.ceiling` branch, so there is no
  suffix collision.
- The comparison reuses the existing summary print (repair F1, recall, calls,
  new-work tokens, `:329-337`); no new scoring code.

**Acceptance.**
- `python -m bench.agent_run --sample N --autonomy autonomous` and `--autonomy
  careful` each produce scored rows under distinct suffixes (`.autonomous`,
  `.careful`).
- The autonomous arm applies AUTO-with-fixer findings silently, so its
  driver-visible `gates.run` count for AUTO drops toward zero, while the careful
  arm shows those AUTO gates (they reach the driver and are run). Repair F1 is
  comparable between the arms, since both apply the AUTO fixes; the measured
  difference is the reduction in gates shown, which is the autonomy claim ("the
  agent you can leave alone").
- In BOTH arms, HUMAN gates and `admit skill` titles are `skip` (the existing
  guard at `:90-95` is not weakened; no approve-HUMAN path is added).
- The run prints repair F1 for both arms, so the change is measured, not
  asserted (design Acceptance).
- No regression on the deterministic bench or on the existing `.ceiling`
  results (untouched files).

**Steps.**
1. Add the `--autonomy` argument and extend the result/telemetry suffix.
2. Map `autonomous` to `(Session autonomy="autonomous", human_gates="skip",
   policies none)` and `careful` to `(autonomy="careful", human_gates="skip")`.
3. Pass `autonomy` into `Session` in `_run_case`.
4. Run both arms and compare the printed numbers.

**Risk and invariant.** Risk: an autonomous arm that auto-approved HUMAN gates
would violate the forbid. This is exactly why the arm maps to
`human_gates="skip"` and NOT to `human_gates="approve"`: with approve,
`_drive` computes `approved = human_gates == "approve" and not
event.title.startswith("admit skill ")` and then `action = "run" if event.grade
!= "HUMAN" or approved else "skip"` (`:91-94`), so a non-admit-skill HUMAN gate
would run, and `_fix_mini_turn` yields HUMAN findings to the driver with
`grade="HUMAN"` (`loop.py:1146-1148`), breaching R1. The autonomous arm's reach
comes from `Session(autonomy="autonomous")` seeding the default `autonomy-auto`
policy (silent AUTO), not from approving person grades. Invariant not to break:
HUMAN and skill admission stay `skip` in every bench arm (the driver mirror of
the forbid), and verify-then-revert is exercised, not bypassed (the bench reads
cleaned output only after the loop's own verification has kept or reverted each
fix).

## Ordering and dependencies

Packet 2 (default policy factory) is independent. Packet 3 (loop threading)
depends on Packet 2: it seeds `default_autonomous_policies`. Packet 3 does NOT
depend on Packet 1: autonomy is sourced from the `--autonomy` flag, not the
governance key, this phase. Packet 4 (provenance) depends on Packet 3 (it records
the `autonomy` level and the `unattended` flag the silent path produces). Packet
5 (bench arm) depends on Packet 3 (it constructs a Session with `autonomy=`) and
is best read against Packet 4 (it inspects the recorded records). Packet 1
(governance schema) is independent forward config with no runtime consumer this
phase; land it any time, or defer it until the governance-read wiring is
scheduled. Recommended land order: 2, 3, 4, 5, with 1 alongside or deferred.

## Non-goals (this phase)

Not loosening the forbid. Not auto-deciding person-grade or HUMAN findings. Not
auto-admitting skills. Not removing or weakening verification. Not shipping GATE
auto-apply: the only GATE finding with a registered fixer is d01's ambiguous
case, whose re-check cannot validate the resolution (resolved question 2), so
GATE stays gated and a GATE rung waits on a soundly-verifiable case. Not wiring
the runtime to read the governance `autonomy` key (deferred; the `--autonomy`
flag is the live control this phase). Not a per-finding careful toggle (deferred,
resolved question 4). Not a new approval UI: this reuses the existing gate,
policy, governance, and bench surfaces. Not model-authored replanning (that
remains M2-full's separate track).
