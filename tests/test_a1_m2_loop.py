"""M2-min plan-first execution (specs/2026-09-04-m2-core-packet.md), behind
CRIVO_PLAN_FIRST. Flag on: one plan approval arms a policy so the AUTO steps
then run silently through M1's batched path. Flag off (default): the M1 flow
is untouched. Driven with the test_clean_loop scripted harness."""

import pytest
from test_clean_loop import (
    REG,
    FakeClient,
    baseline,
    diag,
    drive,
    finding,
    gen,
    ok,
    report_of,
    saved,
)

from crivo import llm
from crivo.events import GateDecision, GateRequest
from crivo.loop import Session

FIX_A = (
    "<execute>def fix_sentinel_missing(df):\n"
    "    out = df.copy()\n"
    "    out['a'] = out['a'].replace(-999, None)\n"
    "    return out\n"
    "df = fix_sentinel_missing(df)\n"
    "assert df['a'].min() != -999</execute>"
)


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("crivo.loop.KernelClient", FakeClient)
    FakeClient.script, FakeClient.executed = [], []
    monkeypatch.delenv("CRIVO_M1", raising=False)  # M1 on
    # Pinned careful: plan-first IS the careful-mode approval unit, and the
    # claim under test is that approving the PLAN is what buys the AUTO steps
    # their silence. Under the autonomy default (packet 3) a standing policy
    # already silences them before any plan is built, so every assertion here
    # would hold for a reason that has nothing to do with the plan.
    s = Session(
        workspace=tmp_path / "ws",
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        preview=False,
        snapshots=False,
        autonomy="careful",
    )
    s._registry_prev = {"df": ("DataFrame", "[4, 2]")}
    s._registry = list(REG)
    s.datasets.append(
        {"path": "data/x.csv", "sha256": "abc123", "variable": "df", "loaded_event": 2}
    )
    return s


def _plan_events(session):
    return [e for e in session.transcript.events() if e.get("kind") == "plan"]


def test_plan_first_off_by_default_emits_no_plan(session, monkeypatch):
    monkeypatch.delenv("CRIVO_PLAN_FIRST", raising=False)
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # autoclean apply (M1 gated path)
        [ok()],  # verify
        baseline(),
        saved(),
    ]
    drive(session.clean("df"), decisions=[GateDecision("run")])
    assert _plan_events(session) == []  # no plan artifact when the flag is off


def test_plan_first_on_approves_once_then_autoclean_runs_silent(session, monkeypatch):
    monkeypatch.setenv("CRIVO_PLAN_FIRST", "on")
    calls: list = []

    def no_model(messages, model=None):
        calls.append(1)
        yield "unreachable"

    monkeypatch.setattr(llm, "generate", no_model)
    FakeClient.script = [
        diag([finding()]),  # one AUTO d4 finding
        baseline(),
        # no kernel cell for the plan itself (build_plan is pure); the plan
        # gate is approved, arming a policy over disease 4
        [ok()],  # autoclean apply cell, now silent (batched by the plan policy)
        [ok()],  # verify cell
        baseline(),  # refresh after fixed
        saved(),
    ]
    events = drive(session.clean("df"), decisions=[GateDecision("run")])

    plans = _plan_events(session)
    assert [p["version"] for p in plans] == [1, 2]  # intent, then outcome
    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["fixed"]
    assert rep["fixes"][0]["origin"] == "autoclean:d04"
    assert calls == []  # AUTO step never reached the model
    gates = [e for e in events if isinstance(e, GateRequest)]
    assert len(gates) == 1  # the plan approval only; the autoclean fix was batched
    assert gates[0].title.startswith("approve plan v1")
    assert session.policies and session.policies[-1].id == "plan-v1"


@pytest.mark.parametrize("answer", [GateDecision("skip"), GateDecision("reject", "no")])
def test_plan_first_declines_the_whole_plan(session, monkeypatch, answer):
    """Anything but "run" declines it. Reject used to return proceed=True,
    which the autonomy packet's seeded policy turned into a silent apply
    (wave 1.5 triage 1); this gate has no revision loop, so the two answers
    mean the same thing here.

    The report assertion changed with wave 1.5 triage 3: it used to read
    `rep["fixes"] == []`, which is how an unapproved plan came to file "0
    fixed · 0 skipped · 0 failed · 0 not attempted" over a frame it had just
    listed findings for. Declining still attempts nothing, which is now
    asserted on the statuses rather than on an empty list."""
    monkeypatch.setenv("CRIVO_PLAN_FIRST", "on")
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        # the plan is declined -> no fixes attempted, report still written
    ]
    events = drive(session.clean("df"), decisions=[answer])

    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["aborted"]
    assert [f["fix_source"] for f in rep["fixes"]] == [None]
    assert len(FakeClient.executed) == 2  # diag + baseline, no fix cell
    assert not session.policies  # a declined plan arms no policy
    assert sum(isinstance(e, GateRequest) for e in events) == 1


def test_plan_lists_the_step_and_its_executor(session, monkeypatch):
    monkeypatch.setenv("CRIVO_PLAN_FIRST", "on")
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [diag([finding()]), baseline()]
    drive(session.clean("df"), decisions=[GateDecision("skip")])

    plan = _plan_events(session)[0]["plan"]
    assert plan["steps"][0]["executor"] == "autoclean"
    assert plan["steps"][0]["disease"] == 4
    assert plan["steps"][0]["grade"] == "AUTO"


def test_trajectory_telemetry_is_emitted_after_a_plan_run(
    session, monkeypatch, tmp_path
):
    """A plan-first run emits one crivo.trajectory span (T2.6, observer-only):
    the AUTO step ran on autoclean as planned, so zero divergences here."""
    import json as _json

    tele = tmp_path / "trace.jsonl"
    monkeypatch.setenv("CRIVO_TELEMETRY", str(tele))
    monkeypatch.setenv("CRIVO_PLAN_FIRST", "on")
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # autoclean apply (batched)
        [ok()],  # verify
        baseline(),
        saved(),
    ]
    drive(session.clean("df"), decisions=[GateDecision("run")])

    rows = [_json.loads(x) for x in tele.read_text().splitlines()]
    traj = [r for r in rows if r["name"] == "crivo.trajectory"]
    assert len(traj) == 1
    assert traj[0]["attrs"]["planned"] == 1
    assert traj[0]["attrs"]["diverged"] == 0


def test_final_plan_records_the_executed_status(session, monkeypatch):
    """The plan artifact tells the truth about the run: after execution a
    second plan version records each step's outcome (M2-min)."""
    monkeypatch.setenv("CRIVO_PLAN_FIRST", "on")
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # autoclean apply (batched)
        [ok()],  # verify
        baseline(),
        saved(),
    ]
    drive(session.clean("df"), decisions=[GateDecision("run")])

    plans = _plan_events(session)
    assert [p["version"] for p in plans] == [1, 2]  # intent, then outcome
    assert plans[0]["plan"]["steps"][0]["status"] == "pending"
    assert plans[1]["plan"]["steps"][0]["status"] == "fixed"
