"""Agent-mode bench lane units (spec: specs/2026-09-03-agent-bench-design.md).

Everything here is keyless and offline: the lane must be import-safe and
testable on CI machines that have no model key (spec acceptance).
"""

import pytest

from bench import agent_run


def test_require_key_exits_without_key(monkeypatch):
    for var in agent_run.KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit):
        agent_run._require_key()


def test_load_dotenv_fills_missing_but_never_overwrites(tmp_path, monkeypatch):
    import os

    env = tmp_path / ".env"
    env.write_text('A_FRESH_VAR="from-file"\nALREADY_SET=from-file\n# comment\n')
    monkeypatch.delenv("A_FRESH_VAR", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from-env")
    agent_run._load_dotenv(env)
    assert os.environ["A_FRESH_VAR"] == "from-file"
    assert os.environ["ALREADY_SET"] == "from-env"
    monkeypatch.delenv("A_FRESH_VAR", raising=False)


def test_mean_ignores_none_and_empty():
    assert agent_run._mean([1.0, None, 3.0]) == 2.0
    assert agent_run._mean([]) is None
    assert agent_run._mean([None]) is None


def test_drive_auto_approves_plain_gates_and_counts_events():
    import time

    from crivo.events import GateDecision, GateRequest

    seen = []

    def gen():
        answer = yield GateRequest(code="df.head()", iteration=1)
        seen.append(answer)
        answer = yield "not-a-gate"
        seen.append(answer)

    n = agent_run._drive(gen(), max_events=10, wall_cap=60.0, t0=time.monotonic())
    assert n == 2
    assert isinstance(seen[0], GateDecision) and seen[0].action == "run"
    assert seen[1] is None


def test_drive_skips_human_gates():
    """Skill admissions and judgement calls are a person's to decide — the
    headless bench must never fake-approve them (events.GateRequest.grade)."""
    import time

    from crivo.events import GateRequest

    seen = []

    def gen():
        answer = yield GateRequest(code="admit_skill()", iteration=1, grade="HUMAN")
        seen.append(answer)

    agent_run._drive(gen(), max_events=10, wall_cap=60.0, t0=time.monotonic())
    assert seen[0].action == "skip"


def test_drive_collects_gate_actions():
    """The lane must record what it decided at each gate, so a case that
    changed nothing can say WHY (skipped judgement calls are a result)."""
    import time

    from crivo.events import GateRequest

    def gen():
        yield GateRequest(code="a", iteration=1)
        yield GateRequest(code="b", iteration=1, grade="HUMAN")

    gates: list = []
    agent_run._drive(
        gen(), max_events=10, wall_cap=60.0, t0=time.monotonic(), gates=gates
    )
    assert gates == ["run", "skip"]


def test_drive_approve_policy_runs_human_gates_but_never_admissions():
    """--human-gates approve is the owner's standing pre-authorisation for
    judgement-call fixes (the ceiling arm). Skill admission is governance and
    stays skipped in every mode."""
    import time

    from crivo.events import GateRequest

    seen = []

    def gen():
        a = yield GateRequest(code="fix()", iteration=1, grade="HUMAN", title="d12 · person call")
        seen.append(a)
        a = yield GateRequest(
            code="admit",
            iteration=1,
            grade="HUMAN",
            title="admit skill fix-x · d3 · reproduces the case it came from",
        )
        seen.append(a)

    gates: list = []
    agent_run._drive(
        gen(),
        max_events=10,
        wall_cap=60.0,
        t0=time.monotonic(),
        gates=gates,
        human_gates="approve",
    )
    assert [d.action for d in seen] == ["run", "skip"]
    assert gates == ["run", "skip"]


def test_drive_event_cap_aborts():
    import time

    def endless():
        while True:
            yield "event"

    with pytest.raises(agent_run.CaseAborted):
        agent_run._drive(endless(), max_events=5, wall_cap=60.0, t0=time.monotonic())


def test_handoff_prefers_parquet_and_records_csv_fallback(tmp_path):
    import pandas as pd

    clean = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    assert agent_run._handoff(clean, tmp_path / "clean") == "parquet"
    assert (tmp_path / "clean.parquet").exists()

    mixed = pd.DataFrame({"c": [1, "x"]})  # arrow refuses mixed-type objects
    assert agent_run._handoff(mixed, tmp_path / "messy") == "csv"
    assert (tmp_path / "messy.csv").exists()


def test_run_case_gives_each_case_an_isolated_skills_dir(tmp_path, monkeypatch):
    """The bench must never touch the repo's live skills/ — a retirement
    during a bench run deleted a real skill (2026-09-03). Sessions get a
    per-case skills dir under the work folder."""
    import argparse

    import pandas as pd

    captured = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.session_dir = tmp_path / "sess"

        def load(self, path, name):
            pass

        def clean(self, var):
            return iter(())

        def close(self):
            pass

    monkeypatch.setattr("crivo.loop.Session", FakeSession)
    df = pd.DataFrame({"a": [1, 2]})
    monkeypatch.setattr(agent_run.corpus, "build", lambda entry: (df, df, None))
    monkeypatch.setattr(agent_run, "RESULTS_DIR", tmp_path / "res")
    args = argparse.Namespace(
        docker=False, max_events=10, wall_cap=5.0, human_gates="skip"
    )

    row = agent_run._run_case({"name": "iso_case", "diseases": [1]}, args)
    assert row["status"] == "no_cleaned_output"
    assert "skills_dir" in captured, "Session must get an explicit skills_dir"
    assert captured["skills_dir"].startswith(str(tmp_path / "res"))


def test_main_skips_cases_already_on_disk(tmp_path, monkeypatch):
    """R5: a finished case is never rerun without --force, never re-billed."""
    import json

    monkeypatch.setenv(agent_run.KEY_VARS[0], "sk-test")
    monkeypatch.setattr(agent_run, "RESULTS_DIR", tmp_path)
    entry = {"name": "done_case", "diseases": [1]}
    monkeypatch.setattr(agent_run.corpus, "SMOKE", [entry], raising=False)
    (tmp_path / "done_case.json").write_text(
        json.dumps(
            {"name": "done_case", "status": "ok", "scores": {"repair": {"f1": 1.0}}}
        )
    )

    def boom(*a, **k):
        raise AssertionError("_run_case called for a finished case")

    monkeypatch.setattr(agent_run, "_run_case", boom)
    assert agent_run.main(["--sample", "1"]) == 0
