"""Terminal REPL driver for the turn loop: rendering and gate input only (R5).

Deliberately dumb. No model logic, no kernel logic; it renders events yielded
by the hand-written turn loop and feeds gate decisions back in.
"""

from analyst_agent.events import (
    ArtifactSaved,
    CardReady,
    GateDecision,
    GateRequest,
    Notice,
    StreamText,
)

BANNER = "analyst-agent — /load <path> [name] · ask a question · /quit"
PROMPT = "❯ "
GATE_PROMPT = "[r]un / [j]eject: "


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
            _drive_turn(session, line, auto_run, input_fn, print_fn)
    except (EOFError, KeyboardInterrupt):
        pass
    session.close()


def _drive_turn(session, question: str, auto_run: bool, input_fn, print_fn) -> None:
    """Drive one run_turn generator: render events, answer via gen.send()."""
    gen = session.run_turn(question)
    try:
        event = next(gen)
        while True:
            answer = None
            if isinstance(event, GateRequest):
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
    if input_fn(GATE_PROMPT).strip().lower() == "j":
        return GateDecision("reject", input_fn("note: ").strip())
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
