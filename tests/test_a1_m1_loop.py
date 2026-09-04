"""M1 loop behavior (T1.4, specs/2026-09-04-t14-loop-packet.md): the
autoclean rung, policy batching, and the fingerprint short-circuit, driven
with the same scripted harness as test_clean_loop (which pins the
CRIVO_M1=off legacy arm)."""

import json

import pytest
from test_clean_loop import (
    FIX_A,
    FIX_B,
    REG,
    FakeClient,
    baseline,
    case,
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
from crivo.policy import PolicyRecord


@pytest.fixture(autouse=True)
def _m1_on(monkeypatch):
    monkeypatch.delenv("CRIVO_M1", raising=False)


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("crivo.loop.KernelClient", FakeClient)
    FakeClient.script, FakeClient.executed = [], []
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


def _no_model(calls):
    def generate(messages, model=None):
        calls.append(list(messages))
        yield "unreachable"

    return generate


def fp(value):
    """A scripted fingerprint-cell result printing `value`."""
    from crivo.kernel.client import StreamOut

    return [StreamOut("stdout", value + "\n"), ok()]


def test_autoclean_rung_fixes_an_auto_finding_with_zero_model_calls(
    session, monkeypatch
):
    calls: list = []
    monkeypatch.setattr(llm, "generate", _no_model(calls))
    FakeClient.script = [
        diag([finding()]),  # d4, AUTO, fixer registered
        baseline(),
        [ok()],  # autoclean apply cell
        [ok()],  # verify cell
        baseline(),  # refresh after fixed
        saved(),  # parquet write
    ]
    events = drive(session.clean("df"))

    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["fixed"]
    assert rep["fixes"][0]["origin"] == "autoclean:d04"
    assert calls == [], "a routed AUTO finding must cost no model call"
    gates = [e for e in events if isinstance(e, GateRequest)]
    assert len(gates) == 1 and gates[0].title.startswith("autoclean")
    assert any("FIXERS[4]" in c for c in FakeClient.executed)


def test_policy_batched_autoclean_yields_no_gate(session, monkeypatch):
    calls: list = []
    monkeypatch.setattr(llm, "generate", _no_model(calls))
    session.policies = [
        PolicyRecord(
            id="bench-auto",
            disease_ids=(4,),
            approver="test",
            expires="2099-01-01",
            mode="ENFORCE",
            valid_disease_ids={4},
        )
    ]
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # autoclean apply cell, unbothered by any gate
        [ok()],  # verify cell
        baseline(),
        saved(),
    ]
    events = drive(session.clean("df"))

    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["fixed"]
    assert calls == []
    assert not any(isinstance(e, GateRequest) for e in events), (
        "a policy-batched fix must not yield a gate"
    )


def test_gate_grade_never_enters_the_autoclean_rung(session, monkeypatch):
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [
        diag([finding(grade="GATE")]),
        baseline(),
        fp("AAA"),  # pre-fix fingerprint (mini turn)
        [ok()],  # model fix cell
        fp("BBB"),  # post-fix fingerprint, changed
        [ok()],  # verify cell
        case(),
        baseline(),
        saved(),
    ]
    drive(session.clean("df"))

    rep = report_of(session)
    assert rep["fixes"][0]["origin"] == "model"
    assert not any("FIXERS[" in c for c in FakeClient.executed)


def test_noop_fix_cell_short_circuits_verify_and_feeds_back(session, monkeypatch):
    seen_messages: list = []
    queue = [FIX_A, FIX_B]

    def generate(messages, model=None):
        seen_messages.append(json.dumps([m["content"] for m in messages]))
        yield queue.pop(0)

    monkeypatch.setattr(llm, "generate", generate)
    FakeClient.script = [
        diag([finding(grade="GATE")]),
        baseline(),
        fp("AAA"),  # pre-fix fingerprint
        [ok()],  # attempt 1 fix cell runs...
        fp("AAA"),  # ...and changed nothing: verify skipped, attempt counted
        [ok()],  # attempt 2 fix cell
        fp("BBB"),  # changed
        [ok()],  # verify cell, only now
        case(),
        baseline(),
        saved(),
    ]
    drive(session.clean("df"))

    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["fixed"]
    assert "changed nothing" in seen_messages[1], (
        "the model's second attempt must see the structured no-op feedback"
    )
    fingerprints = [c for c in FakeClient.executed if "frame_fingerprint" in c]
    assert len(fingerprints) == 3  # one pre, one per attempt


def test_kill_switch_restores_legacy_flow(session, monkeypatch):
    monkeypatch.setenv("CRIVO_M1", "off")
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [
        diag([finding()]),  # AUTO d4: would route under M1
        baseline(),
        [ok()],  # model fix cell, straight away
        [ok()],  # verify cell
        case(),
        baseline(),
        saved(),
    ]
    drive(session.clean("df"))

    rep = report_of(session)
    assert rep["fixes"][0]["origin"] == "model"
    assert not any("FIXERS[" in c for c in FakeClient.executed)
    assert not any("frame_fingerprint" in c for c in FakeClient.executed)


def test_rejected_autoclean_gate_falls_to_the_model(session, monkeypatch):
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        # reject happens at the autoclean gate, before any apply cell runs
        fp("AAA"),
        [ok()],  # model fix cell
        fp("BBB"),
        [ok()],  # verify cell
        case(),
        baseline(),
        saved(),
    ]
    events = drive(
        session.clean("df"),
        decisions=[GateDecision("reject"), GateDecision("run")],
    )

    rep = report_of(session)
    assert rep["fixes"][0]["origin"] == "model"
    gates = [e for e in events if isinstance(e, GateRequest)]
    assert len(gates) == 2
    assert gates[0].title.startswith("autoclean")
