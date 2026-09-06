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
        a = yield GateRequest(
            code="fix()", iteration=1, grade="HUMAN", title="d12 · person call"
        )
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


def test_repeat_runs_each_case_k_times_with_distinct_files(tmp_path, monkeypatch):
    """--repeat k gives pass^k k rows per case under distinct .rN names, and
    tags each row with its run index (A4)."""
    monkeypatch.setenv(agent_run.KEY_VARS[0], "sk-test")
    monkeypatch.setattr(agent_run, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(
        agent_run.corpus,
        "SMOKE",
        [{"name": "rep_case", "diseases": [4]}],
        raising=False,
    )
    seen: list = []

    def fake_run(entry, args):
        seen.append(entry["name"])
        return {
            "name": entry["name"],
            "status": "ok",
            "wall_secs": 0.1,
            "scores": {"repair": {"f1": 1.0}},
        }

    monkeypatch.setattr(agent_run, "_run_case", fake_run)
    agent_run.main(["--sample", "1", "--repeat", "3"])

    assert seen == ["rep_case"] * 3
    files = sorted(p.name for p in tmp_path.glob("rep_case.r*.json"))
    assert files == ["rep_case.r1.json", "rep_case.r2.json", "rep_case.r3.json"]
    import json as _json

    assert _json.loads((tmp_path / "rep_case.r2.json").read_text())["run"] == 2


def test_policies_auto_mints_one_record_and_reaches_the_session(
    tmp_path, monkeypatch, capsys
):
    """T1.4 bench arm: --policies auto builds the bench-auto ENFORCE record
    over every fixer-backed disease and passes it into the Session."""
    monkeypatch.setenv(agent_run.KEY_VARS[0], "sk-test")
    monkeypatch.setattr(agent_run, "RESULTS_DIR", tmp_path / "res")
    entry = {"name": "pol_case", "diseases": [4]}
    monkeypatch.setattr(agent_run.corpus, "SMOKE", [entry], raising=False)
    captured = {}

    def grab(args_entry, args):
        captured["policies"] = args.policy_records
        return {"name": "pol_case", "status": "error: stub", "wall_secs": 0.1}

    monkeypatch.setattr(agent_run, "_run_case", grab)
    agent_run.main(["--sample", "1", "--policies", "auto"])

    records = captured["policies"]
    assert len(records) == 1 and records[0].id == "bench-auto"
    assert records[0].mode == "ENFORCE"
    assert set(records[0].disease_ids) == set(agent_run.FIXERS)


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


def test_main_prints_calls_per_case_and_in_the_aggregate(tmp_path, monkeypatch, capsys):
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


# --- arm naming and the autonomy arms (autonomy-default packet 5) -----------


def test_arm_suffix_names_every_knob_that_changes_the_arm():
    """The result and telemetry names are also the resume key, so every knob
    that changes what an arm measures has to appear in them. The default arm
    keeps the empty suffix and the ceiling arm keeps `.ceiling`, so the
    results already on disk stay addressable."""
    import argparse

    def suffix(**kwargs):
        base = {"human_gates": "skip", "policies": "none", "autonomy": None}
        return agent_run._arm_suffix(argparse.Namespace(**{**base, **kwargs}))

    assert suffix() == ""
    assert suffix(human_gates="approve") == ".ceiling"
    assert suffix(policies="auto") == ".policies-auto"
    assert suffix(autonomy="autonomous") == ".autonomous"
    assert suffix(autonomy="careful") == ".careful"
    assert suffix(policies="auto", autonomy="autonomous") == (
        ".policies-auto.autonomous"
    )
    # a namespace that predates a knob (the older test fixtures) still names
    # the default arm rather than raising
    assert agent_run._arm_suffix(argparse.Namespace(human_gates="skip")) == ""


@pytest.mark.parametrize(
    "first,second",
    [
        ([], ["--policies", "auto"]),
        ([], ["--autonomy", "autonomous"]),
        (["--autonomy", "careful"], ["--autonomy", "autonomous"]),
        (["--policies", "auto"], ["--policies", "auto", "--autonomy", "autonomous"]),
    ],
)
def test_two_arms_cannot_read_each_others_result_files(
    tmp_path, monkeypatch, first, second
):
    """R5 skips a case whose result file exists. If two arms share a filename
    the second arm resumes the first arm's row and prints the first arm's
    numbers as its own, which is how the batched arm silently reported the
    baseline's score. Each arm writes and reads its own file."""
    import json

    monkeypatch.setenv(agent_run.KEY_VARS[0], "sk-test")
    monkeypatch.setattr(agent_run, "RESULTS_DIR", tmp_path)
    entry = {"name": "arm_case", "diseases": [4]}
    monkeypatch.setattr(agent_run.corpus, "SMOKE", [entry], raising=False)
    ran: list = []

    def fake_run(entry, args):
        ran.append(len(ran))
        return {
            "name": entry["name"],
            "status": "ok",
            "wall_secs": 0.1,
            "arm": len(ran),
            "scores": {"repair": {"f1": float(len(ran))}},
        }

    monkeypatch.setattr(agent_run, "_run_case", fake_run)
    agent_run.main(["--sample", "1", *first])
    agent_run.main(["--sample", "1", *second])

    assert ran == [0, 1], "the second arm resumed the first arm's result file"
    written = sorted(p.name for p in tmp_path.glob("arm_case*.json"))
    assert len(written) == 2, f"arms shared a filename: {written}"
    arms = {json.loads((tmp_path / n).read_text())["arm"] for n in written}
    assert arms == {1, 2}


def test_run_case_records_the_policy_arm_in_the_row(tmp_path, monkeypatch):
    """The saved row named its gate arm but not its policy arm, so a batched
    result was indistinguishable from a baseline one after the fact."""
    import argparse

    _fake_session(tmp_path, monkeypatch)
    args = argparse.Namespace(
        docker=False,
        max_events=10,
        wall_cap=5.0,
        human_gates="skip",
        policies="auto",
        autonomy=None,
        policy_records=[],
    )

    row = agent_run._run_case({"name": "arm_case", "diseases": [1]}, args)
    assert row["policies"] == "auto"
    assert row["human_gates"] == "skip"


def test_run_case_passes_the_autonomy_arm_into_the_session(tmp_path, monkeypatch):
    """The autonomous arm's extra reach comes from the Session seeding its own
    default AUTO policy, not from approving anything a person owns: the arm
    still drives with human_gates skip."""
    import argparse

    seen = {}

    class FakeSession:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.session_dir = tmp_path / "sess"

        def load(self, path, name):
            pass

        def clean(self, var):
            return iter(())

        def close(self):
            pass

    import pandas as pd

    monkeypatch.setattr("crivo.loop.Session", FakeSession)
    df = pd.DataFrame({"a": [1, 2]})
    monkeypatch.setattr(agent_run.corpus, "build", lambda entry: (df, df, None))
    monkeypatch.setattr(agent_run, "RESULTS_DIR", tmp_path / "res")
    args = argparse.Namespace(
        docker=False,
        max_events=10,
        wall_cap=5.0,
        human_gates="skip",
        policies="none",
        autonomy="autonomous",
        policy_records=[],
    )

    row = agent_run._run_case({"name": "auto_case", "diseases": [1]}, args)
    assert seen["autonomy"] == "autonomous"
    assert seen["policies"] == [], "the arm leaves the Session to seed its own"
    assert row["autonomy"] == "autonomous"
    assert row["human_gates"] == "skip"


def test_run_case_without_an_autonomy_arm_pins_careful(tmp_path, monkeypatch):
    """An unset --autonomy is the baseline arm, and the baseline results on
    disk were recorded gating every AUTO finding. The bench pins `careful`
    rather than inheriting the Session's autonomous default, so the empty
    suffix keeps meaning what those files say."""
    import argparse

    seen = {}

    class FakeSession:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.session_dir = tmp_path / "sess"

        def load(self, path, name):
            pass

        def clean(self, var):
            return iter(())

        def close(self):
            pass

    import pandas as pd

    monkeypatch.setattr("crivo.loop.Session", FakeSession)
    df = pd.DataFrame({"a": [1, 2]})
    monkeypatch.setattr(agent_run.corpus, "build", lambda entry: (df, df, None))
    monkeypatch.setattr(agent_run, "RESULTS_DIR", tmp_path / "res")
    args = argparse.Namespace(
        docker=False, max_events=10, wall_cap=5.0, human_gates="skip"
    )

    row = agent_run._run_case({"name": "base_case", "diseases": [1]}, args)
    assert seen["autonomy"] == "careful"
    assert row["autonomy"] == "careful"


def test_run_case_autonomy_arm_gets_its_own_telemetry_file(tmp_path, monkeypatch):
    """Telemetry is per arm for the same reason results are: spans from the
    careful arm must not be counted as the autonomous arm's calls."""
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
        docker=False,
        max_events=10,
        wall_cap=5.0,
        human_gates="skip",
        policies="none",
        autonomy="careful",
        policy_records=[],
    )

    agent_run._run_case({"name": "tele_case", "diseases": [1]}, args)
    assert seen["env"] == str(
        tmp_path / "res" / "telemetry" / "tele_case.careful.jsonl"
    )


def test_autonomy_arm_never_drives_human_gates_approve(tmp_path, monkeypatch, capsys):
    """R1 at the driver: `approve` runs non-admission HUMAN gates, which is a
    person's authorisation to give. An autonomy arm is measured with them
    skipped, so the combination is refused rather than honoured."""
    monkeypatch.setenv(agent_run.KEY_VARS[0], "sk-test")
    monkeypatch.setattr(agent_run, "RESULTS_DIR", tmp_path)
    entry = {"name": "gate_case", "diseases": [4]}
    monkeypatch.setattr(agent_run.corpus, "SMOKE", [entry], raising=False)
    seen = {}

    def grab(entry, args):
        seen["human_gates"] = args.human_gates
        return {"name": entry["name"], "status": "error: stub", "wall_secs": 0.1}

    monkeypatch.setattr(agent_run, "_run_case", grab)
    agent_run.main(
        ["--sample", "1", "--autonomy", "autonomous", "--human-gates", "approve"]
    )

    assert seen["human_gates"] == "skip"
    assert not list(tmp_path.glob("*.ceiling*.json")), "not the ceiling arm"
    assert (tmp_path / "gate_case.autonomous.json").exists()


def test_autonomy_rejects_report_only_at_the_cli():
    """The bench scores cleaned output and a report-only run cleans nothing,
    so the level is not an arm here."""
    with pytest.raises(SystemExit):
        agent_run.main(["--sample", "1", "--autonomy", "report-only"])


def test_a_hand_built_namespace_cannot_drive_an_autonomy_arm_with_approve(
    tmp_path, monkeypatch
):
    """The guard lived in `main` only, and `_arm_suffix`'s own docstring says
    callers build the namespace by hand. Such a namespace reached `_drive`
    with approve, which runs every non-admission HUMAN gate: a person's
    authorisation to give. The rule belongs at the point of use, and the
    filename has to name the arm that actually ran."""
    import argparse

    seen = {}
    _fake_session(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent_run,
        "_drive",
        lambda gen, *a, human_gates="skip", **kw: seen.update(gates=human_gates) or 0,
    )
    args = argparse.Namespace(
        docker=False,
        max_events=10,
        wall_cap=5.0,
        human_gates="approve",
        policies="none",
        autonomy="autonomous",
        policy_records=[],
    )

    assert agent_run._arm_gates(args) == "skip"
    assert agent_run._arm_suffix(args) == ".autonomous", "not a ceiling arm"
    row = agent_run._run_case({"name": "hand_case", "diseases": [1]}, args)
    assert seen["gates"] == "skip"
    assert row["human_gates"] == "skip", "the row records what ran, not what was asked"


def test_the_ceiling_arm_still_drives_approve_without_an_autonomy_arm():
    """The coercion is scoped to the combination, not to `approve` itself: the
    existing ceiling arm is untouched."""
    import argparse

    args = argparse.Namespace(human_gates="approve", policies="none", autonomy=None)
    assert agent_run._arm_gates(args) == "approve"
    assert agent_run._arm_suffix(args) == ".ceiling"
