# A0 R1/R3 packet: streaming coverage (done) and steer notes (decision)

2026-09-04. **Status: R1 finding + R3 design decision for the owner.** No
code lands from this packet until you choose an R3 option.

## R1 (streaming coverage): already satisfied, no change needed

The audit assumed silent chunk-joins caused the per-call dead air. They do
not. Every model call in the loop goes through `_generate_scoped` (loop.py
~1740), which yields `StreamText("model", chunk)` per chunk (line ~1758)
and a heartbeat dot for reasoning-only chunks; the REPL's `_drive` renders
these live. QUERY (run_turn), CLEAN fixes (_fix_mini_turn), and summaries
all call it. The two remaining `"".join(stream_parts)` sites (loop.py ~208,
~617) collect kernel profile output, which is ~1s of kernel work, not model
wait. So time-to-first-token is already low on every model-waiting path.
R1 is closed as satisfied; the A0 exit criterion "first visible output
within ~2s" holds today for the paths that matter. (Cancel, R2, landed in
3faf1c0.)

## R3 (steer at the next boundary): the real remaining work, and it needs a call

The goal: during a long model call the user types a note that the agent
picks up at the next step boundary, without a full stop. The hard part is
terminal input: the REPL's `input()` is blocking, so "type while a stream
runs" needs non-blocking stdin, which is a genuine design fork, not a hunk.

### Option A: background stdin reader, true mid-stream steering

A daemon thread reads stdin during a model call and pushes lines onto a
`steer_queue` on the Session; the loop drains the queue at each step
boundary (before the next `_generate_scoped`) and appends the notes as a
user message. Closest to Claude Code's feel.
- Pros: real mid-stream steering, the 2026 table-stakes behavior.
- Cons: raw-terminal handling, thread-vs-`input()` coordination, and it
  fights the REPL's line model; most fragile part of A0. Cost: days, and
  the fragility is permanent maintenance.

### Option B: steer at the gate (recommended)

crivo already stops at every cell with a gate that takes a note. Extend the
gate's reject path so the note is threaded into the model's next-attempt
context verbatim (today a reject nudges but the operator's words are not
always forwarded as first-class steering), and add a third gate verb
`steer` = "run this cell, and also carry this note into the next finding's
context." No non-blocking input, no threads: steering happens at the
boundary the loop already pauses on.
- Pros: no terminal surgery; uses the pause that already exists; the note
  lands exactly where the model reads next; trivially testable with the
  scripted harness.
- Cons: you steer at the next gate, not mid-call. Given gates are frequent
  and cancel (R2) already exists for "stop this now," the wait is bounded.

### Option C: defer R3

Cancel (R2) plus reject-with-note already cover "stop" and "redirect at the
gate." Mark R3 satisfied-enough and spend the days on M2 instead.

## Recommendation

Option B. It delivers the steering intent through the mechanism crivo is
built on (the gate) at a fraction of Option A's cost and risk, and it
composes with the approval-policy work (a steer note on a batched policy
becomes a policy annotation). If you later want true mid-stream steering,
Option A can layer on top without rework, because both drain the same
next-boundary queue.

Decision needed: A, B, or C. On B, I draft the gate-verb hunks (repl + a
small loop change to thread the note) as a follow-up packet.
