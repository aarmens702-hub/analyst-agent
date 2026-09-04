"""Terminal REPL driver: rendering and gate input only (P0 R5, P1 R11).

Deliberately dumb. No model logic, no kernel logic; it renders events yielded
by the hand-written session generators (run_turn for questions, clean for
/clean) and feeds gate decisions back in.
"""

import json

from crivo import llm, provenance
from crivo.events import (
    ArtifactSaved,
    CardReady,
    GateDecision,
    GateRequest,
    Notice,
    StreamText,
)

BANNER = (
    "crivo — /load <path> [name] · /clean <var> · "
    "/clean-family <glob> <name> · /skills · /why · ask a question · /quit"
)
PROMPT = "❯ "
GATE_PROMPT = "[r]un / [j]eject / [s]kip: "


# the last multi-line error, recallable via /trace; one driver by design
_LAST_TRACE: list[str] = []


def _manifest(session) -> str:
    """R5: what is actually in effect, before the first prompt. The line that
    matters most is the last one — how many skills will modify data without
    asking. Tolerant reads throughout: a SessionLike double without a library
    still gets a manifest."""
    from crivo import llm

    info = llm.model_info()
    sandbox = (
        "docker --network none"
        if getattr(session, "docker", False)
        else "subprocess (no network isolation)"
    )
    entries = getattr(getattr(session, "library", None), "entries", None) or {}
    states: dict[str, int] = {}
    for entry in entries.values():
        state = entry.get("state", "unknown")
        states[state] = states.get(state, 0) + 1
    proven = states.get("proven", 0)
    skills = ", ".join(f"{n} {s}" for s, n in sorted(states.items())) or "none"
    return (
        f"model {info['model']} ({info['provider']}) · sandbox: {sandbox}\n"
        f"skills: {skills} — {proven} proven skill(s) will run unattended "
        "on AUTO-grade findings"
    )


class _TurnInterrupts:
    """Scope Ctrl-C for one turn (A0 R2): the first press cancels the
    in-flight model call via llm.request_cancel and the turn fails cleanly a
    beat later (CallCancelled through the turn's own error path); the second
    press exits as before. Installed only around a turn, so an interrupt at
    the prompt still means leaving. Off the main thread (no signal access)
    it degrades to today's behavior."""

    def __init__(self, print_fn) -> None:
        self.print_fn = print_fn
        self.pressed = 0
        self._prev = None
        self._installed = False

    def handle(self, signum=None, frame=None) -> None:
        self.pressed += 1
        if self.pressed == 1:
            llm.request_cancel()
            self.print_fn("\n· canceling the model call (Ctrl-C again to exit)")
            return
        raise KeyboardInterrupt

    def __enter__(self):
        import signal

        try:
            self._prev = signal.signal(signal.SIGINT, self.handle)
            self._installed = True
        except ValueError:  # not the main thread
            self._installed = False
        return self

    def __exit__(self, *exc) -> bool:
        if self._installed:
            import signal

            signal.signal(signal.SIGINT, self._prev)
        return False


def run_repl(session, auto_run: bool = False, input_fn=input, print_fn=print) -> None:
    """Drive a session from the terminal until /quit, EOF, or interrupt."""
    print_fn(BANNER)
    print_fn(_manifest(session))
    try:
        while True:
            line = input_fn(PROMPT).strip()
            if not line:
                continue
            if line == "/quit":
                break
            # One turn blowing up must not end the loop: the guard sits
            # inside the while, so the operator keeps their session, their
            # loaded datasets, and their kernel after a failed command.
            # Interrupts still exit — they are the operator leaving, not a
            # turn failing.
            if line == "/trace":
                print_fn(_LAST_TRACE[0] if _LAST_TRACE else "no stored traceback")
                continue
            try:
                with _TurnInterrupts(print_fn):
                    if line.split()[:1] == ["/load"]:
                        _load(session, line, print_fn)
                    elif line.split()[:1] == ["/clean"]:
                        _clean(session, line, auto_run, input_fn, print_fn)
                    elif line.split()[:1] == ["/skills"]:
                        _skills(session, line, print_fn)
                    elif line.split()[:1] == ["/why"]:
                        _why(session, line, print_fn)
                    elif line.split()[:1] == ["/clean-family"]:
                        _clean_family(session, line, auto_run, input_fn, print_fn)
                    else:
                        _drive(session.run_turn(line), auto_run, input_fn, print_fn)
            except (EOFError, KeyboardInterrupt):
                raise
            except Exception as exc:  # noqa: BLE001 — one turn must not strand the rest
                print_fn(f"· error: {type(exc).__name__}: {exc}")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        # close() always runs: skipping it loses the transcript's close event
        # and, in docker mode, leaves the --network none container running
        # with nobody attached.
        session.close()


def _drive(gen, auto_run: bool, input_fn, print_fn) -> None:
    """Drive one event generator (run_turn or clean): render, answer via send()."""
    try:
        event = next(gen)
        while True:
            answer = None
            if isinstance(event, GateRequest):
                if event.title:
                    print_fn(f"── {event.title} ──")
                _print_code_box(event.code, event.iteration, print_fn)
                if event.preview:
                    print_fn(event.preview)
                answer = GateDecision("run") if auto_run else _gate_decision(input_fn)
            elif isinstance(event, StreamText):
                print_fn(event.text, end="")
            elif isinstance(event, ArtifactSaved):
                print_fn(f"chart saved: {event.path}")
            elif isinstance(event, Notice):
                text = event.text
                if "\n" in text and event.kind in ("error", "llm_error", "kernel_died"):
                    # R6: one line now, the flood behind /trace. Hiding the
                    # traceback entirely would trade a flood for a silence.
                    _LAST_TRACE[:] = [text]
                    first, rest = text.split("\n", 1)
                    text = (
                        f"{first} (+{rest.count(chr(10)) + 1} more lines · "
                        "/trace shows all)"
                    )
                print_fn(f"· {event.kind}: {text}")
            elif isinstance(event, CardReady):
                print_fn(event.card.to_markdown())
                return
            event = gen.send(answer)
    except StopIteration:
        return


def _gate_decision(input_fn) -> GateDecision:
    choice = input_fn(GATE_PROMPT).strip().lower()
    if choice == "j":
        return GateDecision("reject", input_fn("note: ").strip())
    if choice == "s":
        return GateDecision("skip")
    return GateDecision("run")


def _print_code_box(code: str, iteration: int, print_fn) -> None:
    lines = code.splitlines() or [""]
    label = f" iteration {iteration} "
    width = max(len(label), *(len(line) for line in lines))
    print_fn("┌─" + label + "─" * (width - len(label) + 1) + "┐")
    for line in lines:
        print_fn("│ " + line.ljust(width) + " │")
    print_fn("└" + "─" * (width + 2) + "┘")


def _load(session, line: str, print_fn) -> None:
    parts = line.split()
    if len(parts) < 2:
        print_fn("usage: /load <path> [name]")
        return
    name = parts[2] if len(parts) > 2 else None
    session.load(parts[1], name)


def _clean(session, line: str, auto_run: bool, input_fn, print_fn) -> None:
    parts = line.split()
    if len(parts) < 2:
        print_fn("usage: /clean <variable>")
        return
    _drive(session.clean(parts[1]), auto_run, input_fn, print_fn)


def _skills(session, line: str, print_fn) -> None:
    """Show the library: what it holds, what each skill has earned (R16)."""
    parts = line.split()
    entries = getattr(session, "library", None)
    entries = dict(entries.entries) if entries else {}
    if len(parts) > 2 and parts[1] == "show":
        entry = entries.get(parts[2])
        if not entry:
            print_fn(f"no skill named {parts[2]!r}")
            return
        path = session.skills_dir / parts[2] / "SKILL.md"
        if path.exists():
            print_fn(path.read_text(encoding="utf-8"))
        print_fn(json.dumps(entry, indent=2))
        return
    if not entries:
        print_fn("the library is empty — skills are born from verified fixes")
        return
    print_fn(f"{'skill':<32}{'disease':>8}  {'state':<10}{'record':<10}uses")
    for name, e in sorted(entries.items(), key=lambda kv: kv[0]):
        record = f"{e['successes']}✓/{e['failures']}✗"
        print_fn(
            f"{name:<32}{e['disease']:>8}  {e['state']:<10}{record:<10}{e['uses']}"
        )


def _clean_family(session, line: str, auto_run: bool, input_fn, print_fn) -> None:
    """/clean-family <glob> <name>: harmonize a family, then clean each slice."""
    parts = line.split()
    if len(parts) < 3:
        print_fn('usage: /clean-family "<glob>" <name>')
        return
    pattern = parts[1].strip("\"'")
    _drive(session.clean_family(pattern, parts[2]), auto_run, input_fn, print_fn)


def _why(session, line: str, print_fn) -> None:
    """/why [claim id]: where an answer came from, and whether it holds up."""
    parts = line.split()
    dag = provenance.build(session.session_dir)
    node_id = None
    if len(parts) > 1:
        wanted = parts[1]
        node_id = next(
            (n for n in dag["nodes"] if n.endswith(wanted) or n == wanted), wanted
        )
    print_fn(provenance.to_markdown(dag, node_id))


def policy_decision(event: GateRequest, policy: str) -> GateDecision:
    """The human's pre-authorisation, applied by grade. An orchestrator must
    never approve a judgement call on a person's behalf, so anything that is
    not AUTO is skipped and reported rather than decided."""
    if policy == "all" or event.grade == "AUTO":
        return GateDecision("run")
    return GateDecision("skip")


def run_clean_once(
    session,
    path: str,
    name: str | None = None,
    policy: str = "auto",
    decide=None,
):
    """Headless one-shot clean, for agents and scripts (no gate operator).

    Loads the file, drives the clean under `policy_decision` — or under
    `decide`, a per-gate callback (v1.5: an MCP server with an
    elicitation-capable client asks the human gate by gate instead of
    applying the blanket policy). A skip whose decision carries a note
    (declined, elicitation unavailable) surfaces the note in needs_human.
    Returns a machine-readable summary: what ran, what was deferred to a
    human, and where the durable artifacts landed. Tolerant of SessionLike
    doubles, like every other driver in this module.
    """
    session.load(path, name)
    datasets = getattr(session, "datasets", None) or []
    var = datasets[-1]["variable"] if datasets else name
    if not var:
        return {"file": path, "error": "load failed; nothing to clean"}

    needs_human: list[str] = []
    turn = session.clean(var)
    try:
        event = next(turn)
        while True:
            answer = None
            if isinstance(event, GateRequest):
                if decide is not None:
                    answer = decide(event)
                else:
                    answer = policy_decision(event, policy)
                if answer.action == "skip":
                    title = event.title or event.code.splitlines()[0]
                    if answer.note:
                        title = f"{title} ({answer.note})"
                    needs_human.append(title)
            event = turn.send(answer)
    except StopIteration:
        pass

    summary = {"file": path, "variable": var, "needs_human": needs_human}
    session_dir = getattr(session, "session_dir", None)
    reports = sorted(session_dir.glob("clean_reports/*.json")) if session_dir else []
    if reports:
        report = json.loads(reports[-1].read_text())
        summary["report"] = str(reports[-1])
        summary["fixes"] = [
            {
                "disease": rec["finding"]["disease"],
                "slug": rec["finding"]["slug"],
                "grade": rec["finding"]["grade"],
                "status": rec["status"],
            }
            for rec in report["fixes"]
        ]
        summary["outputs"] = report.get("outputs", {})
        summary["skills_admitted"] = report.get("skills_admitted", [])
    return summary
