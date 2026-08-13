"""Streamlit driver tests: the pure event -> render mapping, and the pump
loop that stands in for repl._drive's blocking gate loop (P3; CLAUDE.md
"Claude owns ... the Streamlit shell"; spec
specs/2026-08-09-p0-core-design.md R5).

No Streamlit runtime involved — st.session_state, widgets, and st.rerun all
live below the "Streamlit glue" divider in app.py and are out of scope here.
See tests/test_repl.py for the sibling driver's tests and the
scripted-generator idiom this borrows.
"""

import pytest

from analyst_agent.app import default_var_name, notice_level, pump, render_instruction
from analyst_agent.events import (
    ArtifactSaved,
    CardReady,
    GateDecision,
    GateRequest,
    Notice,
    StreamText,
)


class FakeCard:
    """CardLike test double (see tests/test_repl.py)."""

    def __init__(self, markdown: str = "# answer\nmedian of v is 4.0") -> None:
        self.markdown = markdown

    def to_markdown(self) -> str:
        return self.markdown


def test_render_instruction_stream_text():
    event = StreamText("stdout", "4.0\n")
    assert render_instruction(event) == {
        "kind": "stream",
        "name": "stdout",
        "text": "4.0\n",
    }


def test_render_instruction_gate_request_with_title():
    event = GateRequest("df.mean()", 2, title="fix 1/3 · sentinel-missing")
    assert render_instruction(event) == {
        "kind": "gate",
        "code": "df.mean()",
        "iteration": 2,
        "title": "fix 1/3 · sentinel-missing",
    }


def test_render_instruction_gate_request_defaults_to_empty_title():
    event = GateRequest("df.mean()", 1)
    assert render_instruction(event) == {
        "kind": "gate",
        "code": "df.mean()",
        "iteration": 1,
        "title": "",
    }


def test_render_instruction_artifact_saved():
    event = ArtifactSaved("workspace/s01/artifacts/cell_1_1.png")
    assert render_instruction(event) == {
        "kind": "artifact",
        "path": "workspace/s01/artifacts/cell_1_1.png",
    }


def test_render_instruction_notice():
    event = Notice("cap", "cell budget (6) reached")
    assert render_instruction(event) == {
        "kind": "notice",
        "notice_kind": "cap",
        "text": "cell budget (6) reached",
    }


def test_render_instruction_card_ready_renders_via_to_markdown():
    event = CardReady(FakeCard("# answer\nmedian is 4.0"))
    assert render_instruction(event) == {
        "kind": "card",
        "markdown": "# answer\nmedian is 4.0",
    }


def test_render_instruction_rejects_unknown_event():
    with pytest.raises(TypeError):
        render_instruction(object())


def scripted_gen(events, received=None):
    """Bare SessionLike-shaped generator: yields `events` in order and
    records each gen.send() value into `received` if given. Same idiom as
    FakeSession.run_turn in test_repl.py, minus the SessionLike ceremony
    pump() doesn't need — it only ever sees a live generator, not a session.
    """
    for event in events:
        sent = yield event
        if received is not None:
            received.append(sent)


def test_pump_stops_at_gate_request_without_answering_it():
    sent = []
    gen = scripted_gen(
        [Notice("nudge", "one tag only"), GateRequest("df.head()", 1)], sent
    )

    instructions, pending = pump(gen)

    assert instructions == [
        {"kind": "notice", "notice_kind": "nudge", "text": "one tag only"},
        {"kind": "gate", "code": "df.head()", "iteration": 1, "title": ""},
    ]
    assert pending == GateRequest("df.head()", 1)
    assert sent == [None], "the gate itself must not be answered by pump()"


def test_pump_resumes_with_the_given_decision():
    sent = []
    gen = scripted_gen(
        [GateRequest("df.head()", 1), StreamText("stdout", "ok\n")], sent
    )
    _, first_pending = pump(gen)  # prime to the gate, as the driver does first

    instructions, pending = pump(gen, GateDecision("run"))

    assert first_pending is not None, "setup: must actually reach the gate"
    assert instructions == [{"kind": "stream", "name": "stdout", "text": "ok\n"}]
    assert pending is None
    assert sent == [GateDecision("run"), None]


def test_pump_drains_to_exhaustion_when_no_gate_appears():
    gen = scripted_gen(
        [Notice("skill", "admitted fix-sentinel-missing (on probation)")]
    )

    instructions, pending = pump(gen)

    assert instructions == [
        {
            "kind": "notice",
            "notice_kind": "skill",
            "text": "admitted fix-sentinel-missing (on probation)",
        }
    ]
    assert pending is None


def test_pump_stops_immediately_after_card_ready():
    gen = scripted_gen(
        [CardReady(FakeCard()), StreamText("stdout", "should never be seen")]
    )

    instructions, pending = pump(gen)

    assert instructions == [
        {"kind": "card", "markdown": "# answer\nmedian of v is 4.0"}
    ]
    assert pending is None, "a card ends the turn"


def test_pump_on_empty_generator_returns_no_instructions():
    gen = scripted_gen([])

    instructions, pending = pump(gen)

    assert instructions == []
    assert pending is None


def test_notice_level_maps_kind_to_severity():
    assert notice_level("llm_error") == "error"
    assert notice_level("kernel_died") == "error"
    assert notice_level("error") == "error"
    assert notice_level("cap") == "warning"
    assert notice_level("restart_offer") == "warning"
    assert notice_level("nudge") == "info"
    assert notice_level("fix") == "info"
    assert notice_level("skill") == "info"
    assert notice_level("something_unforeseen") == "info"


def test_default_var_name_sanitizes_the_path_stem():
    assert default_var_name("data/property-tax-report.csv") == "property_tax_report"


def test_default_var_name_falls_back_to_df_for_an_all_underscore_stem():
    assert default_var_name("data/___.csv") == "df"
