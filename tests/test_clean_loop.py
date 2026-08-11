"""CLEAN-flow loop tests (P1 AC7): Session.clean() driven with scripted
decisions, stubbed generate(), fake kernel. Covers fixed / skipped /
rejected-then-fixed / verify-fail-revert / fail-at-cap / indicators."""

import json
from typing import ClassVar

import pytest

from analyst_agent import llm
from analyst_agent.events import GateDecision, GateRequest, Notice
from analyst_agent.kernel.client import ExecResult, HelloInfo, StreamOut
from analyst_agent.loop import Session

REG = [{"name": "df", "type": "DataFrame", "shape": [4, 2], "mem_mb": 0.0}]

FIX_A = (
    "<execute>def fix_sentinel_missing(df):\n"
    "    out = df.copy()\n"
    "    out['a'] = out['a'].replace(-999, None)\n"
    "    return out\n"
    "df = fix_sentinel_missing(df)\n"
    "assert df['a'].min() != -999</execute>"
)
FIX_B = FIX_A.replace("-999", "-998")


class FakeClient:
    script: ClassVar[list] = []
    executed: ClassVar[list] = []

    def __init__(self, workspace_dir, transport_argv=None, data_dir=None):
        self.workspace_dir = workspace_dir

    def start(self) -> HelloInfo:
        return HelloInfo(1, "3.12.0", "7.3.0")

    def execute(self, code, timeout_s=120):
        FakeClient.executed.append(code)
        yield from FakeClient.script.pop(0)

    def restart(self):
        pass

    def close(self):
        pass


def ok(value=None, registry=None):
    return ExecResult(status="ok", value=value, registry=registry or REG, exec_count=1)


def err(evalue="boom"):
    return ExecResult(
        status="error",
        error={"ename": "AssertionError", "evalue": evalue, "traceback": [evalue]},
        exec_count=1,
    )


def finding(**kw):
    base = {
        "disease": 4,
        "slug": "sentinel-missing",
        "columns": ["a"],
        "evidence": "-999 appears 12x",
        "stats": {},
        "grade": "AUTO",
        "confidence": 0.97,
        "indicator": False,
    }
    base.update(kw)
    return base


def diag(findings, clear=(1, 2)):
    payload = json.dumps({"findings": findings, "clear": list(clear)})
    return [StreamOut("stdout", payload + "\n"), ok()]


def baseline(cols=("a", "b")):
    return [ok(value=repr(json.dumps(sorted(cols))))]


def saved():
    return [ok(value="'saved 4 rows x 2 cols'")]


def gen(responses):
    queue = list(responses)

    def generate(messages, model=None):
        yield queue.pop(0)

    return generate


def drive(turn, decisions=()):
    decisions = list(decisions)
    events = []
    try:
        event = next(turn)
        while True:
            events.append(event)
            answer = None
            if isinstance(event, GateRequest):
                answer = decisions.pop(0) if decisions else GateDecision("run")
            event = turn.send(answer)
    except StopIteration:
        return events


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("analyst_agent.loop.KernelClient", FakeClient)
    FakeClient.script, FakeClient.executed = [], []
    s = Session(workspace=tmp_path / "ws", data_dir=tmp_path)
    s._registry_prev = {"df": ("DataFrame", "[4, 2]")}
    s._registry = list(REG)
    s.datasets.append(
        {"path": "data/x.csv", "sha256": "abc123", "variable": "df", "loaded_event": 2}
    )
    return s


def report_of(session):
    reports = sorted((session.session_dir / "clean_reports").glob("*.json"))
    assert reports, "clean report must be written"
    return json.loads(reports[-1].read_text())


def test_fixed_path_end_to_end(session, monkeypatch):
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # fix cell
        [ok()],  # verify cell
        baseline(),  # refresh after fixed
        saved(),  # parquet write
    ]
    events = drive(session.clean("df"))

    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["fixed"]
    assert rep["fixes"][0]["fix_source"].startswith("def fix_sentinel_missing")
    gates = [e for e in events if isinstance(e, GateRequest)]
    assert len(gates) == 1 and "sentinel-missing" in gates[0].title
    lineage = session.session_dir / "cleaned" / "df.lineage.json"
    assert lineage.exists()
    assert json.loads(lineage.read_text())["source"]["sha256"] == "abc123"
    assert rep["event_chain"] == sorted(rep["event_chain"])


def test_skip_records_skipped_without_verify(session, monkeypatch):
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    FakeClient.script = [diag([finding()]), baseline()]
    drive(session.clean("df"), decisions=[GateDecision("skip")])

    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["skipped"]
    assert not (session.session_dir / "cleaned").exists()
    assert len(FakeClient.executed) == 2  # diag + baseline only


def test_reject_note_then_fixed(session, monkeypatch):
    monkeypatch.setattr(llm, "generate", gen([FIX_A, FIX_B]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # FIX_B cell
        [ok()],  # verify
        baseline(),
        saved(),
    ]
    drive(
        session.clean("df"),
        decisions=[GateDecision("reject", "wrong sentinel"), GateDecision("run")],
    )
    rep = report_of(session)
    assert rep["fixes"][0]["status"] == "fixed"
    assert rep["fixes"][0]["attempts"] == 2
    assert "-998" in rep["fixes"][0]["fix_source"]


def test_verify_failure_reverts_and_retries(session, monkeypatch):
    monkeypatch.setattr(llm, "generate", gen([FIX_A, FIX_B]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # fix A
        [err("column 'b' changed but was not a fix target")],  # verify fails
        [ok(value="'reverted'")],  # revert
        [ok()],  # fix B
        [ok()],  # verify passes
        baseline(),
        saved(),
    ]
    drive(session.clean("df"))
    rep = report_of(session)
    assert rep["fixes"][0]["status"] == "fixed"
    assert rep["fixes"][0]["attempts"] == 2
    assert any("_clean_backup" in c for c in FakeClient.executed), "revert must run"


def test_fail_at_attempt_cap(session, monkeypatch):
    monkeypatch.setattr(llm, "generate", gen([FIX_A, FIX_A, FIX_A]))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
    ] + [[ok()], [err()], [ok(value="'reverted'")]] * 3  # fix, verify-fail, revert ×3
    events = drive(session.clean("df"))
    rep = report_of(session)
    assert rep["fixes"][0]["status"] == "failed"
    assert rep["fixes"][0]["attempts"] == 3
    assert not (session.session_dir / "cleaned").exists()
    notices = [e for e in events if isinstance(e, Notice) and e.kind == "fix"]
    assert "failed" in notices[-1].text


def test_indicators_get_no_fix_turns(session, monkeypatch):
    monkeypatch.setattr(llm, "generate", gen([]))
    FakeClient.script = [
        diag([finding(disease=15, slug="statistical-outliers", indicator=True)]),
        baseline(),
    ]
    events = drive(session.clean("df"))
    assert not [e for e in events if isinstance(e, GateRequest)]
    rep = report_of(session)
    assert rep["fixes"] == []
    assert rep["indicators"][0]["slug"] == "statistical-outliers"


def test_unknown_variable_notices(session, monkeypatch):
    events = drive(session.clean("nope"))
    notices = [e for e in events if isinstance(e, Notice) and e.kind == "error"]
    assert notices and "unknown variable" in notices[0].text
