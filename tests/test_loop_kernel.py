"""AC6 against a real kernel: a cell that SIGKILLs the kernel mid-turn must
surface kernel_died, restart, replay the loads, and let the turn finish."""

import sys

from analyst_agent import llm
from analyst_agent.events import CardReady, GateDecision, GateRequest, Notice
from analyst_agent.loop import Session

SUBPROCESS_ARGV = [sys.executable, "-m", "analyst_agent.kernel.supervisor"]


def scripted_generate(responses):
    queue = list(responses)

    def generate(messages, model=None):
        yield queue.pop(0)

    return generate


def drive(turn):
    events = []
    try:
        event = next(turn)
        while True:
            events.append(event)
            answer = GateDecision("run") if isinstance(event, GateRequest) else None
            if isinstance(event, CardReady):
                return events
            event = turn.send(answer)
    except StopIteration:
        return events


def test_kernel_sigkill_recovers_and_replays_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(
        llm,
        "generate",
        scripted_generate(
            [
                (
                    "<execute>import os, signal\n"
                    "os.kill(os.getpid(), signal.SIGKILL)</execute>"
                ),
                "<answer>the kernel died and came back</answer>",
            ]
        ),
    )
    csv = tmp_path / "tiny.csv"
    csv.write_text("a,b\n1,2\n3,4\n")

    session = Session(
        workspace=tmp_path / "ws",
        data_dir=tmp_path,
        transport_argv=SUBPROCESS_ARGV,
    )
    try:
        session.load(str(csv))
        assert session.datasets[0]["variable"] == "tiny"

        events = drive(session.run_turn("please crash"))

        died = [e for e in events if isinstance(e, Notice) and e.kind == "kernel_died"]
        assert died, f"expected a kernel_died notice, got {events}"
        cards = [e for e in events if isinstance(e, CardReady)]
        assert cards and cards[0].card.answer == "the kernel died and came back"

        # the replayed load must have restored the dataset variable
        result = list(session.client.execute("'tiny' in dir()"))[-1]
        assert result.status == "ok"
        assert result.value.strip() == "True"
    finally:
        session.close()
