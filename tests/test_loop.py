"""Loop tests (AC10): run_turn() driven with scripted gate decisions,
a stubbed generate(), and a fake kernel client. No LLM, no kernel."""

import hashlib
import json
import time
from typing import ClassVar

import pytest

from analyst_agent import llm
from analyst_agent.events import (
    CardReady,
    GateDecision,
    GateRequest,
    Notice,
    StreamText,
)
from analyst_agent.kernel.client import ExecResult, HelloInfo, StreamOut
from analyst_agent.loop import Session, parse_tags


class FakeClient:
    """KernelClient double: replays scripted per-execute event lists."""

    # class-level so the Session-constructed instance sees the test's script
    script: ClassVar[list[list]] = []
    executed: ClassVar[list[str]] = []

    def __init__(self, workspace_dir, transport_argv=None, data_dir=None):
        self.workspace_dir = workspace_dir

    def start(self) -> HelloInfo:
        return HelloInfo(1, "3.12.0", "7.3.0")

    def execute(self, code, timeout_s=120):
        FakeClient.executed.append(code)
        events = FakeClient.script.pop(0)
        yield from events

    def restart(self):
        pass

    def close(self):
        pass


def ok_result(value="42", registry=None, **kw):
    return ExecResult(
        status="ok", value=value, registry=registry or [], exec_count=1, **kw
    )


def scripted_generate(responses):
    queue = list(responses)
    contexts = []

    def generate(messages, model=None):
        contexts.append(list(messages))
        yield queue.pop(0)

    generate.contexts = contexts
    return generate


def drive(turn, decisions=None):
    """Drive a run_turn generator; answer gates from `decisions`; collect events."""
    decisions = list(decisions or [])
    events = []
    try:
        event = next(turn)
        while True:
            events.append(event)
            answer = None
            if isinstance(event, GateRequest):
                answer = decisions.pop(0) if decisions else GateDecision("run")
            if isinstance(event, CardReady):
                return events
            event = turn.send(answer)
    except StopIteration:
        return events


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("analyst_agent.loop.KernelClient", FakeClient)
    FakeClient.script = []
    FakeClient.executed = []
    # previews off: the scripted flows here would otherwise need one extra
    # kernel-cell entry per gate (see test_clean_loop's preview tests)
    return Session(
        workspace=tmp_path / "ws", data_dir=tmp_path, preview=False, snapshots=False
    )


def card_from(events):
    ready = [e for e in events if isinstance(e, CardReady)]
    assert len(ready) == 1, f"expected one CardReady, got events: {events}"
    return ready[0].card


def test_parse_tags():
    assert parse_tags("<execute>x = 1</execute>") == ("execute", "x = 1")
    assert parse_tags("ok\n<answer>done</answer>") == ("answer", "done")
    assert parse_tags("no tags")[0] == "malformed"
    assert parse_tags("<execute>a</execute><answer>b</answer>")[0] == "malformed"


def test_full_turn_produces_card_with_lifted_checks(session, monkeypatch):
    monkeypatch.setattr(
        llm,
        "generate",
        scripted_generate(
            [
                "<execute>result = 6 * 7\nassert result == 42\nresult</execute>",
                "<answer>The answer is 42.</answer>",
            ]
        ),
    )
    FakeClient.script = [
        [ok_result(value="42", registry=[{"name": "result", "type": "int"}])]
    ]

    events = drive(session.run_turn("what is six times seven?"))
    card = card_from(events)

    assert card.answer == "The answer is 42."
    assert card.checks == [{"expr": "result == 42", "passed": True}]
    assert card.cells[0]["status"] == "ok"
    assert card.cells[0]["gate"] == "run"
    assert len(card.lineage["event_chain"]) == 3  # question, exec, card
    assert card.flags == {
        "capped": False,
        "malformed_answer": False,
        "truncated": False,
        "unchecked": False,
        "intent_mismatch": False,
    }

    saved = json.loads((session.session_dir / "cards" / "c001.json").read_text())
    assert saved["answer"] == card.answer
    kinds = [e["kind"] for e in session.transcript.events()]
    for kind in ("session_meta", "user", "model", "gate", "exec", "card"):
        assert kind in kinds


def test_rejection_note_steers_the_model(session, monkeypatch):
    monkeypatch.setattr(
        llm,
        "generate",
        scripted_generate(
            [
                "<execute>result = df.val.mean()</execute>",
                "<execute>result = 4.0\nassert result > 0\nresult</execute>",
                "<answer>median is 4.0</answer>",
            ]
        ),
    )
    FakeClient.script = [[ok_result(value="4.0")]]

    events = drive(
        session.run_turn("median of val?"),
        decisions=[GateDecision("reject", "use median, not mean"), GateDecision("run")],
    )
    card = card_from(events)

    assert card.cells[0]["gate"] == {"rejected": "use median, not mean"}
    assert card.cells[0]["status"] is None
    assert card.cells[1]["status"] == "ok"
    rejected_obs = [
        m for m in session.history if "user rejected the cell" in str(m.get("content"))
    ]
    assert rejected_obs, "rejection note must go back to the model as an observation"


def test_malformed_gets_one_free_nudge(session, monkeypatch):
    monkeypatch.setattr(
        llm,
        "generate",
        scripted_generate(
            [
                "I think I'll look at the data first.",  # malformed: no tags
                "<answer>nothing to do</answer>",
            ]
        ),
    )
    events = drive(session.run_turn("hello?"))

    nudges = [e for e in events if isinstance(e, Notice) and e.kind == "nudge"]
    assert len(nudges) == 1
    assert card_from(events).answer == "nothing to do"


def test_answer_after_uncheckd_cell_gets_bounced_for_asserts(session, monkeypatch):
    """R18: a card must not ship unchecked when cells ran — bounce the answer
    once, demanding a final assert-bearing cell."""
    monkeypatch.setattr(
        llm,
        "generate",
        scripted_generate(
            [
                "<execute>result = 42\nresult</execute>",  # no asserts
                "<answer>it is 42</answer>",  # premature — must bounce
                "<execute>assert result == 42\nresult</execute>",
                "<answer>it is 42, verified</answer>",
            ]
        ),
    )
    FakeClient.script = [[ok_result(value="42")], [ok_result(value="42")]]

    events = drive(session.run_turn("what is the answer?"))
    card = card_from(events)

    assert card.checks == [{"expr": "result == 42", "passed": True}]
    assert card.answer == "it is 42, verified"
    bounce = [
        m
        for m in session.history
        if "assert" in str(m.get("content")).lower() and m["role"] == "user"
    ]
    assert bounce, "the answer must be bounced with an asserts demand"


def test_cap_forces_answer_and_flags_card(session, monkeypatch):
    cell = "<execute>x = 1</execute>"
    monkeypatch.setattr(
        llm,
        "generate",
        scripted_generate([cell] * 6 + ["<answer>ran out of budget</answer>"]),
    )
    FakeClient.script = [[ok_result(value=str(i))] for i in range(6)]

    events = drive(session.run_turn("busy question"))
    card = card_from(events)

    assert card.flags["capped"] is True
    assert card.answer == "ran out of budget"
    caps = [e for e in events if isinstance(e, Notice) and e.kind == "cap"]
    assert len(caps) == 1
    assert len([c for c in card.cells if c["status"] == "ok"]) == 6


def test_error_traceback_becomes_observation(session, monkeypatch):
    monkeypatch.setattr(
        llm,
        "generate",
        scripted_generate(
            [
                "<execute>1 / 0</execute>",
                "<answer>that division fails</answer>",
            ]
        ),
    )
    FakeClient.script = [
        [
            ExecResult(
                status="error",
                error={
                    "ename": "ZeroDivisionError",
                    "evalue": "division by zero",
                    "traceback": ["Traceback...", "ZeroDivisionError"],
                },
                exec_count=1,
            )
        ]
    ]

    events = drive(session.run_turn("divide by zero"))
    card = card_from(events)

    assert card.checks == []  # no ok cell → nothing lifted
    obs = [m for m in session.history if "ZeroDivisionError" in str(m.get("content"))]
    assert obs, "traceback must feed back as an observation"


def test_load_records_lineage_profile_and_replay(session, tmp_path):
    csv = tmp_path / "tiny.csv"
    csv.write_text("a,b\n1,2\n3,4\n")
    FakeClient.script = [
        [
            StreamOut("stdout", "# tiny — 2 rows × 2 cols\n"),
            ok_result(
                value=None,
                registry=[{"name": "tiny", "type": "DataFrame", "shape": [2, 2]}],
            ),
        ]
    ]

    session.load(str(csv))

    ds = session.datasets[0]
    assert ds["variable"] == "tiny"
    assert ds["sha256"] == hashlib.sha256(csv.read_bytes()).hexdigest()
    # the replay record must reload THIS file into THIS variable; how it reads
    # it is the loader's business (the cell delegates to diagnose.load, so the
    # sniff/NA/encoding policy cannot drift from the free report's)
    assert session.loads and str(csv) in session.loads[0][1]
    assert "tiny = " in session.loads[0][1]
    assert any("2 rows × 2 cols" in str(m["content"]) for m in session.history)
    assert session.origins["tiny"] == ds["loaded_event"]


def test_registry_block_is_last_context_message(session, monkeypatch):
    fake = scripted_generate(["<answer>hi</answer>"])
    monkeypatch.setattr(llm, "generate", fake)

    drive(session.run_turn("hi"))

    ctx = fake.contexts[0]
    assert ctx[0]["role"] == "system"
    assert "<registry>" in ctx[-1]["content"]


# --- P3: the intent gate (Talk Less Verify More, 2601.00224) ------------------


def test_intent_gate_flags_correct_code_answering_the_wrong_question(
    session, monkeypatch
):
    """Assertions cannot see this failure: the code ran, the checks passed, and
    it answered a different question than the one asked."""
    FakeClient.script = [[ok_result(value="'42'")]]
    monkeypatch.setattr(
        llm,
        "generate",
        scripted_generate(
            [
                "<execute>result = df['b'].sum()\nassert result > 0</execute>",
                "<answer>the total is 42</answer>",
                (
                    "<restatement>the code summed column b</restatement>"
                    "<verdict>mismatch</verdict>"
                    "<reason>the question asked for column a, not b</reason>"
                ),
            ]
        ),
    )
    card = card_from(drive(session.run_turn("what is the total of column a?")))

    assert card.flags["intent_mismatch"] is True
    assert "column a" in card.intent["reason"]
    assert "summed column b" in card.intent["restatement"]


def test_intent_gate_stays_quiet_when_the_answer_matches_the_question(
    session, monkeypatch
):
    FakeClient.script = [[ok_result(value="'42'")]]
    monkeypatch.setattr(
        llm,
        "generate",
        scripted_generate(
            [
                "<execute>result = df['a'].sum()\nassert result > 0</execute>",
                "<answer>the total is 42</answer>",
                (
                    "<restatement>the code summed column a</restatement>"
                    "<verdict>match</verdict><reason>same quantity</reason>"
                ),
            ]
        ),
    )
    card = card_from(drive(session.run_turn("what is the total of column a?")))

    assert card.flags["intent_mismatch"] is False
    assert card.intent["verdict"] == "match"


def test_a_long_reasoning_phase_shows_progress_instead_of_silence(session, monkeypatch):
    """A reasoning model streams its thinking in a field we deliberately drop,
    so a slow turn renders as nothing at all — indistinguishable from a hang.
    Heartbeats keep it visible without letting thinking reach the parser."""

    def generate(messages, model=None):
        yield from [""] * 40  # reasoning chunks: no content
        yield "<answer>42</answer>"

    monkeypatch.setattr(llm, "generate", generate)
    events = drive(session.run_turn("what is six times seven?"))

    card = card_from(events)
    assert card.answer == "42", "heartbeats must not reach the tag parser"
    ticks = [
        e for e in events if isinstance(e, StreamText) and e.text.strip() in {"·", "."}
    ]
    assert ticks, "a long think must show something"


def test_a_call_that_never_finishes_thinking_is_abandoned(monkeypatch):
    """A read timeout never fires while reasoning chunks keep arriving, so a
    model can think indefinitely. A wall-clock budget ends the turn with an
    error the loop can report, rather than a process that sits there."""
    monkeypatch.setattr(llm, "MAX_CALL_S", 0.05)

    class Endless:
        def __iter__(self):
            return self

        def __next__(self):
            time.sleep(0.02)
            chunk = type("C", (), {})()
            delta = type("D", (), {"content": None, "reasoning_content": "..."})()
            chunk.choices = [type("Ch", (), {"delta": delta})()]
            return chunk

    monkeypatch.setattr(
        llm,
        "_get_client",
        lambda: type(
            "Client",
            (),
            {
                "chat": type(
                    "Chat",
                    (),
                    {
                        "completions": type(
                            "Comp", (), {"create": staticmethod(lambda **kw: Endless())}
                        )()
                    },
                )()
            },
        )(),
    )
    with pytest.raises(TimeoutError, match="thinking"):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_a_remote_frame_becomes_a_lineage_entry_when_it_appears(session):
    """R12: load_url stamps the frame in-kernel; the registry carries that
    stamp up, and the session records it like a load — so cards, reports, and
    /why ground remote data without a /load ever happening. A re-fetch to new
    content is a second entry (both kept); a re-stamp of the same content is
    not a duplicate."""
    remote = {
        "uri": "https://example.org/feed.csv",
        "fetched_at": "2026-08-14T21:00:00+00:00",
        "sha256": "beef" * 16,
        "rows": 2,
    }
    entry = {"name": "feed", "type": "DataFrame", "shape": [2, 2], "remote": remote}
    session._stamp_registry([entry], 7)

    (ds,) = [d for d in session.datasets if d["variable"] == "feed"]
    assert ds["sha256"] == remote["sha256"]
    assert ds["path"] == remote["uri"]
    assert ds["remote"]["fetched_at"] == remote["fetched_at"]
    assert ds["loaded_event"] == 7

    refetched = dict(entry, shape=[3, 2], remote=dict(remote, sha256="feed" * 16))
    session._stamp_registry([refetched], 9)
    assert len([d for d in session.datasets if d["variable"] == "feed"]) == 2

    session._stamp_registry([refetched], 11)
    assert len([d for d in session.datasets if d["variable"] == "feed"]) == 2
