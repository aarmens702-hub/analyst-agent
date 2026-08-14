"""Driver tests: repl.py and the loop<->driver event contract, no model or kernel.

FakeSession implements events.SessionLike with a scripted run_turn generator;
FakeCard implements events.CardLike. The REPL is driven with scripted input_fn
and a recording print_fn.
"""

import json
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
    """SessionLike test double; run_turn/clean are real generators scripted per test."""

    def __init__(self, script: list[Event] | None = None) -> None:
        self.script = script or []
        self.sent: list[GateDecision | None] = []  # every gen.send() value received
        self.loads: list[tuple[str, str | None]] = []
        self.questions: list[str] = []
        self.cleans: list[str] = []
        self.closed = False

    def load(self, path: str, name: str | None = None) -> None:
        self.loads.append((path, name))

    def run_turn(self, question: str) -> Generator[Event, GateDecision | None, None]:
        self.questions.append(question)
        for event in self.script:
            self.sent.append((yield event))

    def clean(self, var: str) -> Generator[Event, GateDecision | None, None]:
        self.cleans.append(var)
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
    assert "[r]un / [j]eject / [s]kip: " in input_fn.prompts
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


# --- P1 CLEAN extensions: /clean dispatch, gate titles, [s]kip (R8, R11) ---


def test_gate_prompt_offers_run_reject_skip() -> None:
    assert GATE_PROMPT == "[r]un / [j]eject / [s]kip: "


def test_skip_at_gate_sends_skip_decision() -> None:
    fake = FakeSession(script=[GateRequest("df.mean()", 1), CardReady(FakeCard())])
    input_fn = scripted_input("q", "s", "/quit")

    run_repl(fake, input_fn=input_fn, print_fn=RecordingPrint())

    assert fake.decisions == [GateDecision("skip")]
    assert "note: " not in input_fn.prompts, "skip must not prompt for a note"


def test_clean_dispatches_var_and_drives_to_card() -> None:
    title = "fix 1/2 · sentinel-missing · AUTO · conf 0.97 · 143 cells are '-999'"
    fake = FakeSession(
        script=[
            GateRequest("df = fix_sentinel_missing(df)", 1, title=title),
            CardReady(FakeCard("# clean report")),
        ]
    )
    printer = RecordingPrint()
    input_fn = scripted_input("/clean df", "r", "/quit")

    run_repl(fake, input_fn=input_fn, print_fn=printer)

    assert fake.cleans == ["df"]
    assert fake.questions == [], "/clean must not be dispatched as a question"
    assert fake.decisions == [GateDecision("run")]
    assert "# clean report" in printer.output
    assert fake.closed


def test_clean_generator_may_end_without_card() -> None:
    fake = FakeSession(script=[Notice("diagnosis", "no findings")])
    input_fn = scripted_input("/clean df", "/quit")

    run_repl(fake, input_fn=input_fn, print_fn=RecordingPrint())

    assert fake.cleans == ["df"]
    assert fake.sent == [None], "StopIteration returns the REPL to its prompt"
    assert fake.closed


def test_clean_without_var_prints_usage() -> None:
    fake = FakeSession()
    printer = RecordingPrint()

    run_repl(fake, input_fn=scripted_input("/clean", "/quit"), print_fn=printer)

    assert fake.cleans == []
    assert fake.questions == []
    assert "usage: /clean <variable>" in printer.output


def test_titled_gate_renders_header_line_above_code_box() -> None:
    title = "fix 4/12 · sentinel-missing · AUTO · conf 0.97 · 143 cells are '-999'"
    fake = FakeSession(
        script=[GateRequest("df = fix(df)", 1, title=title), CardReady(FakeCard())]
    )
    printer = RecordingPrint()

    run_repl(fake, input_fn=scripted_input("/clean df", "r", "/quit"), print_fn=printer)

    header_at = printer.calls.index((f"── {title} ──", "\n"))
    box_top_at = next(
        i for i, (text, _) in enumerate(printer.calls) if text.startswith("┌")
    )
    assert header_at == box_top_at - 1, "title renders directly above the code box"


def test_untitled_gate_renders_no_header_line() -> None:
    fake = FakeSession(script=[GateRequest("df.mean()", 1), CardReady(FakeCard())])
    printer = RecordingPrint()

    run_repl(fake, input_fn=scripted_input("q", "r", "/quit"), print_fn=printer)

    assert not any(text.startswith("──") for text, _ in printer.calls)


def test_repl_import_hygiene() -> None:
    """R5: drivers contain no model or kernel logic — enforced at source level."""
    import inspect

    import analyst_agent.repl as repl_module

    src = inspect.getsource(repl_module)
    forbidden = ("analyst_agent.kernel", "analyst_agent.llm", "analyst_agent.loop")
    for name in forbidden:
        assert name not in src, f"repl.py must not touch {name}"


def test_main_without_api_key_exits_1(tmp_path) -> None:
    """python -m analyst_agent without a key: friendly exit, not a traceback."""
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = ""  # empty beats .env: load_dotenv never overrides
    proc = subprocess.run(
        [sys.executable, "-m", "analyst_agent"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=tmp_path,
    )
    assert proc.returncode == 1
    assert "DEEPSEEK_API_KEY" in proc.stdout

    from analyst_agent import main

    assert callable(main)


# --- P2: the library view (R16) ----------------------------------------------


class _Lib:
    def __init__(self, entries):
        self.entries = entries


def _stocked_session(tmp_path):
    session = FakeSession()
    session.library = _Lib(
        {
            "fix-sentinel-missing": {
                "name": "fix-sentinel-missing",
                "disease": 4,
                "state": "proven",
                "uses": 4,
                "successes": 4,
                "failures": 0,
            }
        }
    )
    session.skills_dir = tmp_path
    return session


def test_skills_lists_what_each_skill_has_earned(tmp_path) -> None:
    session = _stocked_session(tmp_path)
    printed: list = []
    run_repl(
        session, input_fn=scripted_input(*["/skills", "/quit"]), print_fn=printed.append
    )
    body = "\n".join(str(p) for p in printed)
    assert "fix-sentinel-missing" in body
    assert "proven" in body
    assert "4✓/0✗" in body


def test_skills_on_an_empty_library_says_so() -> None:
    session = FakeSession()
    session.library = _Lib({})
    printed: list = []
    run_repl(
        session, input_fn=scripted_input(*["/skills", "/quit"]), print_fn=printed.append
    )
    assert any("empty" in str(p) for p in printed)


def test_skills_show_prints_the_card_and_the_ledger_row(tmp_path) -> None:
    session = _stocked_session(tmp_path)
    folder = tmp_path / "fix-sentinel-missing"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: fix-sentinel-missing\n---\n")
    printed: list = []
    run_repl(
        session,
        input_fn=scripted_input(*["/skills show fix-sentinel-missing", "/quit"]),
        print_fn=printed.append,
    )
    body = "\n".join(str(p) for p in printed)
    assert "name: fix-sentinel-missing" in body
    assert '"successes": 4' in body


def test_why_prints_the_provenance_chain(tmp_path) -> None:
    """P3: /why answers "where did this number come from" from artifacts on
    disk, without the session having to remember anything extra."""
    session = FakeSession()
    session.session_dir = tmp_path
    cards = tmp_path / "cards"
    cards.mkdir()
    (cards / "c001.json").write_text(
        json.dumps(
            {
                "card_id": "s01-c001",
                "question": "which brewery has the most beers?",
                "answer": "Brewery Vivant",
                "checks": [{"expr": "len(r) == 3", "passed": True}],
                "lineage": {
                    "datasets": [
                        {"path": "data/beers.csv", "sha256": "abc", "variable": "beers"}
                    ]
                },
                "flags": {},
            }
        )
    )
    printed: list = []
    run_repl(session, input_fn=scripted_input("/why", "/quit"), print_fn=printed.append)
    body = "\n".join(str(p) for p in printed)
    assert "✓ trusted" in body
    assert "source: data/beers.csv" in body


def test_the_repl_survives_an_unexpected_failure_and_still_closes(tmp_path) -> None:
    """A generator that raises must not take the session with it. Skipping
    session.close() leaves the transcript without its close event and, in
    docker mode, orphans the --network none container."""

    class Exploding(FakeSession):
        def run_turn(self, question):
            raise RuntimeError("the kernel died")
            yield  # pragma: no cover - makes this a generator

    session = Exploding()
    printed: list = []
    run_repl(
        session,
        input_fn=scripted_input("count the rows", "/quit"),
        print_fn=printed.append,
    )
    assert session.closed, "close() must run even when a turn blows up"
    assert any("kernel died" in str(p) for p in printed), "and say what happened"


def test_startup_names_the_model_sandbox_and_unattended_skills() -> None:
    """P5 R5/AC4: a new user cannot currently discover that proven skills run
    unattended on AUTO findings without typing /skills and knowing what
    'proven' means. For a project whose claim is that everything is
    checkable, silent-by-default is the wrong default — the startup line
    names the model, the sandbox, and exactly how many skills may modify
    data without asking."""
    from types import SimpleNamespace

    session = FakeSession()
    session.docker = False
    session.library = SimpleNamespace(
        entries={
            "fix-a": {"state": "proven"},
            "fix-b": {"state": "probation"},
            "fix-c": {"state": "proven"},
        }
    )
    printed: list = []
    run_repl(session, input_fn=scripted_input("/quit"), print_fn=printed.append)

    text = "\n".join(str(p) for p in printed)
    assert "deepseek" in text or "claude" in text, "the provider is named"
    assert "subprocess" in text, "the sandbox mode is named"
    assert "1 probation" in text and "2 proven" in text
    assert "unattended" in text, "the silent-modification warning is the point"


def test_a_multiline_error_prints_one_line_and_trace_recalls_it() -> None:
    """P5 R6: repl printed event.text unconditionally, so a pandas traceback
    flooded the terminal. An error renders as its first line with a count;
    the full text stays available behind /trace, because hiding it entirely
    would trade a flood for a silence."""
    trace = "ValueError: boom\n" + "\n".join(f"  frame {i}" for i in range(30))
    session = FakeSession(script=[Notice("error", trace)])
    printed: list = []
    run_repl(
        session,
        input_fn=scripted_input("why did it fail", "/trace", "/quit"),
        print_fn=printed.append,
    )

    lines = [str(p) for p in printed]
    notice = next(ln for ln in lines if "ValueError: boom" in ln and "error" in ln)
    assert "frame" not in notice, "the notice itself must be one line"
    assert "/trace" in notice, "and it must say where the rest went"
    assert sum("frame 29" in ln for ln in lines) == 1, "/trace shows everything, once"


def test_the_gate_preview_is_printed_under_the_code_box() -> None:
    """R3's last mile: the loop computes the consequence, the terminal must
    actually show it, or the operator is back to executing pandas in their
    head."""
    gate = GateRequest(
        "df = fix(df)", 1, title="fix 1/1", preview="amount: 2 of 4 cells change"
    )
    session = FakeSession(script=[gate])
    printed: list = []
    run_repl(
        session,
        input_fn=scripted_input("clean it up", "r", "/quit"),
        print_fn=printed.append,
    )

    assert any("2 of 4 cells change" in str(p) for p in printed), printed


def test_one_failed_turn_does_not_end_the_repl() -> None:
    """The test above cannot see whether the loop survived: its scripted /quit
    is simply never consumed when the loop dies early, so it passes either
    way — the test-points-at-the-reproduction failure the findings doc names.
    The invariant is stronger: the turn AFTER the failure must actually run."""

    class ExplodesOnce(FakeSession):
        def run_turn(self, question):
            if not self.questions:
                self.questions.append(question)
                raise RuntimeError("the kernel died")
                yield  # pragma: no cover - makes this a generator
            yield from FakeSession.run_turn(self, question)

    session = ExplodesOnce()
    printed: list = []
    run_repl(
        session,
        input_fn=scripted_input("count the rows", "and the columns", "/quit"),
        print_fn=printed.append,
    )
    assert any("kernel died" in str(p) for p in printed), "the failure is reported"
    assert session.questions == ["count the rows", "and the columns"], (
        "the turn after the failure must run: one bad turn is not a dead REPL"
    )
    assert session.closed
