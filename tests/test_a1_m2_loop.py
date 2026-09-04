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
    s = Session(
        workspace=tmp_path / "ws",
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        preview=False,
        snapshots=False,
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


def test_plan_first_skip_declines_the_whole_plan(session, monkeypatch):
    monkeypatch.setenv("CRIVO_PLAN_FIRST", "on")
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        # plan gate is skipped -> no fixes attempted, report still written
    ]
    events = drive(session.clean("df"), decisions=[GateDecision("skip")])

    rep = report_of(session)
    assert rep["fixes"] == []  # declining the plan attempts nothing
    assert not session.policies  # a skipped plan arms no policy
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
