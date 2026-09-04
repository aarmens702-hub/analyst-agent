# T1.4 review packet: wiring M1 into the CLEAN loop

2026-09-04. **Status: awaiting owner review.** Four hunks against loop.py
(yours). Nothing lands until you approve; on approval Claude applies exactly
these hunks and adds the test plan below. Everything they call is already
merged and green (router.py, fingerprint.py, policy.py, 603 tests).

How to review: Hunk B is the heart and mirrors `_skill_attempt` line for
line; if B reads right, A and D are wiring and C is a short-circuit.

## Invariants preserved

- Skills still run first ("the library first"); autoclean is the new second
  rung; the model stays the last resort.
- Every applied fix, autoclean included, passes the same
  `verify.verify_cell` layer and reverts via `verify.revert_cell` on
  failure. No new trust is granted to anything.
- GATE and HUMAN findings never reach `_autoclean_attempt` (the router
  refuses them; its tests pin that) and policy can only silence gates for
  AUTO findings (policy.py's forbid pins that). Admissions untouched.
- With no policies configured and CRIVO_M1 unset, the only behavior change
  is AUTO findings with registered fixers getting a deterministic attempt
  (still gated) before a model call.
- `CRIVO_M1=off` restores today's flow exactly (the bench legacy arm).

## Hunk A: the second rung in `_clean` (3 lines)

In the per-finding loop, between the skill attempt and the mini turn
(current lines ~521-527):

```python
            rec = yield from self._skill_attempt(
                var, finding, i, len(fixable), baseline_cols
            )
            if rec is None and _m1_enabled():
                routed = router.route(finding)
                if routed["executor"] == "autoclean":
                    rec = yield from self._autoclean_attempt(
                        var, finding, i, len(fixable), baseline_cols
                    )
            if rec is None:
                rec = yield from self._fix_mini_turn(
                    var, finding, i, len(fixable), baseline_cols
                )
```

(`_autoclean_attempt` returns None when its fix fails verification, so the
finding falls through to the model exactly as a failed skill does.)

## Hunk B: `_autoclean_attempt` (new method, mirrors `_skill_attempt`)

```python
    def _autoclean_attempt(
        self, var: str, finding: dict, i: int, n: int, baseline_cols: list[str]
    ):
        """The taxonomy's own fixer before paying a model call (M1, T1.4).

        Only reachable for AUTO findings whose disease has a registered
        deterministic fixer (router.route pins that). A standing policy may
        batch the gate; otherwise the gate yields exactly like a skill's.
        Verification and revert are the same cells every fix passes."""
        disease = finding["disease"]
        fix_source = (
            "from crivo.autoclean import FIXERS\n"
            f"fix = FIXERS[{disease}]"
        )
        code = verify.skill_apply_cell(var, fix_source, finding["columns"])
        decision_note = ""
        verdict = policy.evaluate(finding, self.policies)
        silent = verdict["batched"]
        t0 = time.monotonic()
        evs: list[int] = []
        title = (
            f"autoclean {i}/{n} · d{disease:02d} {finding['slug']} · "
            f"{finding['grade']} · {finding['evidence'][:60]}"
        )

        if silent:
            decision_note = f"policy:{verdict['policy_id']}"
            evs.append(
                self.transcript.append("gate", action="run", note=decision_note)
            )
        else:
            pv = yield from self._preview(var, code)
            decision = yield GateRequest(
                code, 1, title=title, preview=pv, grade=finding["grade"]
            )
            if not isinstance(decision, GateDecision):
                decision = GateDecision("run")
            evs.append(
                self.transcript.append(
                    "gate", action=decision.action, note=decision.note
                )
            )
            if decision.action == "skip":
                return {
                    "finding": finding, "status": "skipped", "attempts": 0,
                    "fix_source": fix_source, "verify": {},
                    "transcript_evs": evs,
                    "elapsed_s": round(time.monotonic() - t0, 1),
                    "origin": f"autoclean:d{disease:02d}", "case": {},
                }
            if decision.action == "reject":
                return None  # straight to the model with the human's note lost
                # unless you prefer threading decision.note into the mini turn;
                # flagged as open question 3.

        result, _, _, ev_id = yield from self._exec_events(code, quiet=True)
        evs.append(ev_id)
        if result.status == "ok":
            self._stamp_registry(result.registry, ev_id)
            vres, _, _, v_ev = yield from self._exec_events(
                verify.verify_cell(var, finding, baseline_cols), quiet=True
            )
            evs.append(v_ev)
            if vres.status == "ok":
                yield Notice(
                    "autoclean",
                    f"d{disease:02d} {finding['slug']} fixed with no model call"
                    + (f" ({decision_note})" if silent else ""),
                )
                return {
                    "finding": finding, "status": "fixed", "attempts": 0,
                    "fix_source": fix_source,
                    "verify": {"layer1": "pass", "by": f"autoclean:d{disease:02d}"},
                    "transcript_evs": evs,
                    "elapsed_s": round(time.monotonic() - t0, 1),
                    "origin": f"autoclean:d{disease:02d}", "case": {},
                }

        _, _, _, rev_ev = yield from self._exec_events(
            verify.revert_cell(var), quiet=True
        )
        evs.append(rev_ev)
        yield Notice(
            "autoclean",
            f"d{disease:02d} deterministic fix did not verify — handing to the model",
        )
        return None
```

## Hunk C: the fingerprint short-circuit in `_fix_mini_turn`

Once per mini turn, before the attempt loop (after `case: dict = {}`):

```python
        fp_cell = (
            "from crivo.fingerprint import frame_fingerprint\n"
            f"print(frame_fingerprint({var}))"
        )
        _, fp_stream, _, _ = yield from self._exec_events(fp_cell, quiet=True)
        pre_fp = fp_stream.strip().splitlines()[-1] if fp_stream.strip() else ""
```

Then, immediately after the fix cell's `_exec_events` succeeds and BEFORE
`verify.verify_cell` runs:

```python
            if pre_fp:
                _, after_stream, _, _ = yield from self._exec_events(
                    fp_cell, quiet=True
                )
                after_fp = after_stream.strip().splitlines()[-1]
                if after_fp == pre_fp:
                    msgs.append({
                        "role": "user",
                        "content": (
                            f"attempt {attempts}/{CLEAN_MAX_ATTEMPTS} not "
                            "verified: your cell changed nothing in "
                            f"{var} (identical content fingerprint). Edit the "
                            "data before finishing, or explain why no change "
                            "is needed."
                        ),
                    })
                    continue
```

(Reverted failures restore the frame, so `pre_fp` stays valid across
attempts. Two extra kernel round-trips per model attempt, milliseconds each,
against a skipped verify pass and a wasted model retry.)

## Hunk D: wiring

- `Session.__init__` gains `policies=None`; `self.policies = list(policies or [])`.
- Module top of loop.py: `from crivo import policy, router` (both import-safe,
  no core imports; router's own test pins that) and
  `def _m1_enabled(): return os.environ.get("CRIVO_M1", "on") != "off"`.

## Test plan (added with the hunks, not before)

Mirroring tests/test_clean_loop.py's harness: autoclean attempt fixes an AUTO
finding with zero model calls (faux provider asserts no generate call);
failed verification reverts and falls through to the model; GATE finding
never enters the autoclean path even with CRIVO_M1 on; a policy-batched
finding yields no GateRequest and records gate note policy:id; skip and
reject records match the skill shapes; fingerprint short-circuit turns a
no-op fix cell into structured feedback without a verify cell exec;
CRIVO_M1=off restores the legacy event sequence.

## Bench arm (after landing)

`--policies auto` flag in bench/agent_run.py mints one ENFORCE policy over
all AUTO diseases with fixers (id bench-auto, approver bench) so the M1 exit
run measures the batched arm; the governed baseline rerun measures gated
autoclean; CRIVO_M1=off gives the legacy arm. Three-way compare via
bench/compare.py.

## Open questions

1. Rung order: skills before autoclean (as drafted, matching "the library
   first") or autoclean before skills for determinism? Drafted order
   recommended: skills are dataset-proven and self-improving.
2. Policy source for interactive sessions: M1 leaves Session(policies=[])
   default (arming UX is M2's coherent-unit work). Bench-only for now. OK?
3. On a gated autoclean reject, fall to the model losing the reject note (as
   drafted, matching skill behavior) or thread the note into the mini turn's
   opening context?
