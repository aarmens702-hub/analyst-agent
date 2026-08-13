"""Terminal REPL driver: rendering and gate input only (P0 R5, P1 R11).

Deliberately dumb. No model logic, no kernel logic; it renders events yielded
by the hand-written session generators (run_turn for questions, clean for
/clean) and feeds gate decisions back in.
"""

import json

from analyst_agent.events import (
    ArtifactSaved,
    CardReady,
    GateDecision,
    GateRequest,
    Notice,
    StreamText,
)

BANNER = (
    "analyst-agent — /load <path> [name] · /clean <var> · "
    "/clean-family <glob> <name> · /skills · ask a question · /quit"
)
PROMPT = "❯ "
GATE_PROMPT = "[r]un / [j]eject / [s]kip: "


def run_repl(session, auto_run: bool = False, input_fn=input, print_fn=print) -> None:
    """Drive a session from the terminal until /quit, EOF, or interrupt."""
    print_fn(BANNER)
    try:
        while True:
            line = input_fn(PROMPT).strip()
            if not line:
                continue
            if line == "/quit":
                break
            if line.split()[:1] == ["/load"]:
                _load(session, line, print_fn)
                continue
            if line.split()[:1] == ["/clean"]:
                _clean(session, line, auto_run, input_fn, print_fn)
                continue
            if line.split()[:1] == ["/skills"]:
                _skills(session, line, print_fn)
                continue
            if line.split()[:1] == ["/clean-family"]:
                _clean_family(session, line, auto_run, input_fn, print_fn)
                continue
            _drive(session.run_turn(line), auto_run, input_fn, print_fn)
    except (EOFError, KeyboardInterrupt):
        pass
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
                answer = GateDecision("run") if auto_run else _gate_decision(input_fn)
            elif isinstance(event, StreamText):
                print_fn(event.text, end="")
            elif isinstance(event, ArtifactSaved):
                print_fn(f"chart saved: {event.path}")
            elif isinstance(event, Notice):
                print_fn(f"· {event.kind}: {event.text}")
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
