"""Driver tests: repl.py and the loop<->driver event contract, no model or kernel.

FakeSession implements events.SessionLike with a scripted run_turn generator;
FakeCard implements events.CardLike. The REPL is driven with scripted input_fn
and a recording print_fn.
"""

from collections.abc import Generator

import pytest

from analyst_agent.events import (
    ArtifactSaved,
    CardLike,
    CardReady,
    Event,
    GateDecision,
    GateRequest,
    Notice,
    SessionLike,
    StreamText,
)
from analyst_agent.repl import GATE_PROMPT, PROMPT, run_repl


class FakeSession:
    """SessionLike test double; run_turn is a real generator scripted per test."""

    def __init__(self, script: list[Event] | None = None) -> None:
        self.script = script or []
        self.sent: list[GateDecision | None] = []  # every gen.send() value received
        self.loads: list[tuple[str, str | None]] = []
        self.questions: list[str] = []
        self.closed = False

    def load(self, path: str, name: str | None = None) -> None:
        self.loads.append((path, name))

    def run_turn(self, question: str) -> Generator[Event, GateDecision | None, None]:
        self.questions.append(question)
        for event in self.script:
            self.sent.append((yield event))

    def close(self) -> None:
        self.closed = True

    @property
    def decisions(self) -> list[GateDecision]:
        return [s for s in self.sent if isinstance(s, GateDecision)]


def scripted_input(*values: str):
    """iter(...).__next__-style input_fn double that accepts and records the prompt."""
    it = iter(values)
    prompts: list[str] = []

    def input_fn(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(it)

    input_fn.prompts = prompts
    return input_fn


class RecordingPrint:
    """print_fn double recording (text, end) pairs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, text: object = "", end: str = "\n") -> None:
        self.calls.append((str(text), end))

    @property
    def output(self) -> str:
        return "".join(text + end for text, end in self.calls)


def test_gate_decision_rejects_invalid_action() -> None:
    with pytest.raises(ValueError):
        GateDecision("edit")


def test_quit_closes_session() -> None:
    fake = FakeSession()
    session: SessionLike = fake
    printer = RecordingPrint()

    run_repl(session, input_fn=scripted_input("/quit"), print_fn=printer)

    assert fake.closed
    assert fake.questions == []
    assert printer.calls, "banner should print before the prompt loop"
    assert "analyst-agent" in printer.calls[0][0]


def raising_input(exc: type[BaseException]):
    """input_fn double that raises exc on any call."""

    def input_fn(prompt: str = "") -> str:
        raise exc

    return input_fn


def test_eoferror_closes_session() -> None:
    fake = FakeSession()

    run_repl(fake, input_fn=raising_input(EOFError), print_fn=RecordingPrint())

    assert fake.closed


def test_keyboard_interrupt_at_prompt_closes_session() -> None:
    fake = FakeSession()

    run_repl(fake, input_fn=raising_input(KeyboardInterrupt), print_fn=RecordingPrint())

    assert fake.closed


def test_blank_line_reprompts_without_dispatch() -> None:
    fake = FakeSession()
    input_fn = scripted_input("", "   ", "/quit")

    run_repl(fake, input_fn=input_fn, print_fn=RecordingPrint())

    assert fake.questions == []
    assert fake.loads == []
    assert input_fn.prompts == [PROMPT, PROMPT, PROMPT]


def test_load_dispatches_with_and_without_name() -> None:
    fake = FakeSession()

    run_repl(
        fake,
        input_fn=scripted_input(
            "/load data/tax.csv", "/load data/tax.csv df_tax", "/quit"
        ),
        print_fn=RecordingPrint(),
    )

    assert fake.loads == [("data/tax.csv", None), ("data/tax.csv", "df_tax")]
    assert fake.questions == []


def test_load_without_path_prints_usage() -> None:
    fake = FakeSession()
    printer = RecordingPrint()

    run_repl(fake, input_fn=scripted_input("/load", "/quit"), print_fn=printer)

    assert fake.loads == []
    assert fake.questions == []
    assert "usage: /load <path> [name]" in printer.output


class FakeCard:
    """CardLike test double."""

    def __init__(self, markdown: str = "# answer\nmedian of v is 4.0") -> None:
        self.markdown = markdown

    def to_markdown(self) -> str:
        return self.markdown


def test_question_drives_turn_rendering_notice_and_card() -> None:
    card: CardLike = FakeCard()
    fake = FakeSession(script=[Notice("nudge", "one tag only"), CardReady(card)])
    printer = RecordingPrint()

    run_repl(fake, input_fn=scripted_input("median of v?", "/quit"), print_fn=printer)

    assert fake.questions == ["median of v?"]
    assert "· nudge: one tag only" in printer.output
    assert "# answer\nmedian of v is 4.0" in printer.output
    assert fake.sent == [None], "informational events are answered with gen.send(None)"
    assert fake.closed


def test_full_turn_renders_box_stream_artifact_and_card() -> None:
    code = 'out = df["v"].median()\nprint(out)'
    fake = FakeSession(
        script=[
            GateRequest(code, 1),
            StreamText("stdout", "4."),
            StreamText("stdout", "0\n"),
            ArtifactSaved("workspace/s01/artifacts/cell_1_1.png"),
            CardReady(FakeCard()),
        ]
    )
    printer = RecordingPrint()
    input_fn = scripted_input("median of v?", "r", "/quit")

    run_repl(fake, input_fn=input_fn, print_fn=printer)

    # gate: code box with iteration number, then the gate prompt
    assert "iteration 1" in printer.output
    assert '│ out = df["v"].median()' in printer.output
    assert "│ print(out)" in printer.output
    assert "┌" in printer.output and "└" in printer.output
    assert "[r]un / [j]eject: " in input_fn.prompts
    assert fake.decisions == [GateDecision("run")]

    # stream chunks render live, without added newlines, in order
    assert ("4.", "") in printer.calls
    assert ("0\n", "") in printer.calls
    stream_first = printer.calls.index(("4.", ""))
    assert printer.calls[stream_first + 1] == ("0\n", "")

    # artifact line, then the card markdown
    assert "chart saved: workspace/s01/artifacts/cell_1_1.png" in printer.output
    assert "# answer\nmedian of v is 4.0" in printer.output
    box_at = printer.output.index("┌")
    chart_at = printer.output.index("chart saved:")
    card_at = printer.output.index("# answer")
    assert box_at < stream_first_pos(printer) < chart_at < card_at

    # send-protocol: gate decision, then None per informational event, none for CardReady
    assert fake.sent == [GateDecision("run"), None, None, None]
    assert fake.closed


def stream_first_pos(printer: RecordingPrint) -> int:
    return printer.output.index("4.0\n")


def test_reject_at_gate_sends_note() -> None:
    fake = FakeSession(script=[GateRequest("df.mean()", 1), CardReady(FakeCard())])
    input_fn = scripted_input("mean of v?", "j", "use median", "/quit")

    run_repl(fake, input_fn=input_fn, print_fn=RecordingPrint())

    assert fake.decisions == [GateDecision("reject", "use median")]
    assert "note: " in input_fn.prompts


def test_auto_run_never_prompts_at_gate() -> None:
    fake = FakeSession(script=[GateRequest("df.head()", 1), CardReady(FakeCard())])
    input_fn = scripted_input("peek?", "/quit")  # no gate answers available on purpose

    run_repl(fake, auto_run=True, input_fn=input_fn, print_fn=RecordingPrint())

    assert fake.decisions == [GateDecision("run")]
    assert GATE_PROMPT not in input_fn.prompts


def test_gate_default_answer_runs() -> None:
    fake = FakeSession(script=[GateRequest("df.mean()", 1), CardReady(FakeCard())])

    run_repl(
        fake, input_fn=scripted_input("q", "x", "/quit"), print_fn=RecordingPrint()
    )

    assert fake.decisions == [GateDecision("run")]


def test_repl_import_hygiene() -> None:
    """R5: drivers contain no model or kernel logic — enforced at source level."""
    import inspect

    import analyst_agent.repl as repl_module

    src = inspect.getsource(repl_module)
    forbidden = ("analyst_agent.kernel", "analyst_agent.llm", "analyst_agent.loop")
    for name in forbidden:
        assert name not in src, f"repl.py must not touch {name}"


def test_main_without_core_exits_1_pointing_at_spec() -> None:
    """python -m analyst_agent runs before loop.py exists: friendly exit, not a crash."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "analyst_agent"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "loop.py" in proc.stdout
    assert "specs/2026-08-09-p0-core-design.md" in proc.stdout

    from analyst_agent import main

    assert callable(main)
