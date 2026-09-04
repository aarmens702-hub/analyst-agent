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
    assert row["model"]["provider"]  # provenance: every row names its model
    assert "skills_dir" in captured, "Session must get an explicit skills_dir"
    assert captured["skills_dir"].startswith(str(tmp_path / "res"))


def _fake_session(tmp_path, monkeypatch, on_init=None):
    """The FakeSession pattern from the isolated-skills-dir test, shared by
    the telemetry tests (T1.5): no model, no kernel, no cleaned output."""
    import pandas as pd

    class FakeSession:
        def __init__(self, **kwargs):
            if on_init is not None:
                on_init()
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


def test_run_case_points_telemetry_at_a_fresh_per_case_file_and_restores_env(
    tmp_path, monkeypatch
):
    """T1.5: CRIVO_TELEMETRY names a per-case JSONL under RESULTS_DIR before
    the Session exists, stale spans from an earlier run are removed first,
    and the previous env value is restored so nothing leaks between cases."""
    import argparse
    import json
    import os

    expected = tmp_path / "res" / "telemetry" / "tele_case.jsonl"
    expected.parent.mkdir(parents=True)
    expected.write_text(json.dumps({"name": "gen_ai.client.call", "dur_s": 9.9}) + "\n")
    seen = {}
    _fake_session(
        tmp_path,
        monkeypatch,
        on_init=lambda: seen.update(env=os.environ.get("CRIVO_TELEMETRY")),
    )
    monkeypatch.setenv("CRIVO_TELEMETRY", "sentinel-before")
    args = argparse.Namespace(
        docker=False, max_events=10, wall_cap=5.0, human_gates="skip"
    )

    row = agent_run._run_case({"name": "tele_case", "diseases": [1]}, args)
    assert seen["env"] == str(expected)
    assert os.environ["CRIVO_TELEMETRY"] == "sentinel-before"
    assert not expected.exists(), "stale telemetry must be removed before the run"
    assert row["calls"] == {"count": 0, "model_wait_s": 0.0, "new_work_tokens": 0}


def test_run_case_ceiling_arm_gets_its_own_telemetry_file_and_unsets_cleanly(
    tmp_path, monkeypatch
):
    """T1.5: the approve arm writes name.ceiling.jsonl so the two arms never
    share spans, and a previously unset env var ends the case unset."""
    import argparse
    import os

    seen = {}
    _fake_session(
        tmp_path,
        monkeypatch,
        on_init=lambda: seen.update(env=os.environ.get("CRIVO_TELEMETRY")),
    )
    monkeypatch.delenv("CRIVO_TELEMETRY", raising=False)
    args = argparse.Namespace(
        docker=False, max_events=10, wall_cap=5.0, human_gates="approve"
    )

    agent_run._run_case({"name": "tele_case", "diseases": [1]}, args)
    assert seen["env"] == str(
        tmp_path / "res" / "telemetry" / "tele_case.ceiling.jsonl"
    )
    assert "CRIVO_TELEMETRY" not in os.environ


def test_call_stats_sums_client_call_spans_and_tolerates_gaps(tmp_path):
    """T1.5: calls per case, model wait, and token sums come only from the
    gen_ai.client.call spans; missing rows, fields, or junk lines degrade to
    zeros/absent keys, never an exception."""
    import json

    tele = tmp_path / "case.jsonl"
    spans = [
        {
            "name": "gen_ai.client.call",
            "dur_s": 1.5,
            "attrs": {
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 10,
                "crivo.cache.hit_tokens": 60,
                "crivo.cache.miss_tokens": 40,
            },
        },
        {"name": "kernel.exec", "dur_s": 9.0, "attrs": {}},
        {
            "name": "gen_ai.client.call",
            "dur_s": 0.25,
            "attrs": {"gen_ai.usage.input_tokens": 50},
        },
        {"name": "gen_ai.client.call"},
    ]
    tele.write_text("\n".join(json.dumps(s) for s in spans) + "\nnot json\n")

    stats = agent_run._call_stats(tele)
    assert stats == {
        "count": 3,
        "model_wait_s": 1.75,
        "input_tokens": 150,
        "output_tokens": 10,
        "cache_hit_tokens": 60,
        "cache_miss_tokens": 40,
        "new_work_tokens": 150 - 60 + 10,
    }
    assert agent_run._call_stats(tmp_path / "absent.jsonl") == {
        "count": 0,
        "model_wait_s": 0.0,
        "new_work_tokens": 0,
    }


def test_main_prints_calls_per_case_and_in_the_aggregate(
    tmp_path, monkeypatch, capsys
):
    """T1.5: the per-case line and the final aggregate carry the calls
    summary (count, new-work tokens) alongside the scores."""
    monkeypatch.setenv(agent_run.KEY_VARS[0], "sk-test")
    monkeypatch.setattr(agent_run, "RESULTS_DIR", tmp_path)
    entry = {"name": "fresh_case", "diseases": [1]}
    monkeypatch.setattr(agent_run.corpus, "SMOKE", [entry], raising=False)
    row = {
        "name": "fresh_case",
        "status": "ok",
        "wall_secs": 3.2,
        "events": 7,
        "scores": {"repair": {"f1": 0.5, "recall": 0.5}},
        "calls": {"count": 4, "model_wait_s": 2.1, "new_work_tokens": 200},
    }
    monkeypatch.setattr(agent_run, "_run_case", lambda entry, args: row)

    assert agent_run.main(["--sample", "1"]) == 0
    out = capsys.readouterr().out
    assert "4 calls" in out
    assert "200 new-work tok" in out
    assert "calls mean 4.0" in out
    assert "new-work tokens mean 200.0" in out


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
