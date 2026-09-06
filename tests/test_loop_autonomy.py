"""Autonomy-default loop threading (Packet 3 of
specs/2026-09-05-autonomy-default-build-packets.md).

Autonomy is a SILENCE decision — which AUTO findings with a registered
deterministic fixer apply without showing a gate — not a widening of what may
be decided. These tests pin that reading: the person grades and skill
admission stay gated in autonomous mode, careful reproduces the gated flow,
report-only touches nothing, and the person is told once per workspace the
first time software edits their data without asking.

Driven with the same scripted harness as test_clean_loop and test_a1_m1_loop.
"""

import json
from typing import ClassVar

import pytest
from test_clean_loop import (
    FIX_A,
    GOOD_PROPOSAL,
    REG,
    FakeClient,
    baseline,
    case,
    counting_generate,
    diag,
    drift_finding,
    drive,
    err,
    family_meta,
    finding,
    gen,
    ok,
    report_of,
    saved,
)

from crivo import library, llm, skills
from crivo.events import GateDecision, GateRequest, Notice, StreamText
from crivo.loop import Session

SENTINEL = ".crivo-autonomy-notice"


@pytest.fixture(autouse=True)
def _arm(monkeypatch):
    """M1 on (the autoclean rung exists), plan-first off (M2 is a separate
    approval unit), and the fake kernel in place of a real one."""
    monkeypatch.delenv("CRIVO_M1", raising=False)
    monkeypatch.delenv("CRIVO_PLAN_FIRST", raising=False)
    monkeypatch.setattr("crivo.loop.KernelClient", FakeClient)
    FakeClient.script, FakeClient.executed = [], []


def make_session(tmp_path, autonomy="autonomous", workspace=None):
    s = Session(
        workspace=workspace or (tmp_path / "ws"),
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        preview=False,
        snapshots=False,
        autonomy=autonomy,
    )
    s._registry_prev = {"df": ("DataFrame", "[4, 2]")}
    s._registry = list(REG)
    s.datasets.append(
        {"path": "data/x.csv", "sha256": "abc123", "variable": "df", "loaded_event": 2}
    )
    return s


def fp(value):
    """A scripted fingerprint-cell result printing `value`."""
    from crivo.kernel.client import StreamOut

    return [StreamOut("stdout", value + "\n"), ok()]


def gate_notes(session):
    return [
        ev.get("note") for ev in session.transcript.events() if ev.get("kind") == "gate"
    ]


def gate_actions(session):
    return [
        ev.get("action")
        for ev in session.transcript.events()
        if ev.get("kind") == "gate"
    ]


def stock_skill(session, tmp_path, disease, name, state="proven"):
    """Put one skill on disk and in the ledger, in the state the test needs."""
    root = tmp_path / "skills"
    skills.save(
        skills.Skill(
            name=name,
            description="A skill the library has a record for.",
            fix_source="def fix(df, columns):\n    return df.copy()\n",
            test_source="def test_fix():\n    assert True\n",
            metadata={"disease": str(disease)},
        ),
        root,
    )
    session.skills_dir = root
    session.library = library.Library(root=root)
    session.library.register(name, disease=disease)["state"] = state
    return session


# --- (a) the point of the whole mode -----------------------------------------


def test_autonomous_silences_auto_but_never_a_person_grade(tmp_path, monkeypatch):
    """The hard line, measured. All three findings name a disease the seeded
    autonomy-auto policy lists (4, 6 and 7 all have registered fixers), so the
    only thing separating them is the grade — and policy.evaluate reads the
    grade before it reads any policy. The AUTO one applies with no gate; the
    GATE and HUMAN ones are never auto-applied and both reach the human."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A, FIX_A]))
    session = make_session(tmp_path)
    findings = [
        finding(),  # d4 AUTO, fixer registered
        finding(disease=6, slug="whitespace-damage", grade="GATE", columns=["b"]),
        finding(disease=7, slug="encoding-mojibake", grade="HUMAN", columns=["b"]),
    ]
    FakeClient.script = [
        diag(findings),
        baseline(),
        [ok()],  # autoclean apply cell for d4, unbothered by any gate
        [ok()],  # its verification
        baseline(),  # refresh after fixed
        fp("AAA"),  # d6 mini turn: pre-fix fingerprint
        fp("AAA"),  # d7 mini turn: pre-fix fingerprint
        saved(),  # parquet write
    ]
    events = drive(
        session.clean("df"),
        decisions=[GateDecision("skip"), GateDecision("skip")],
    )

    gates = [e for e in events if isinstance(e, GateRequest)]
    assert [g.grade for g in gates] == ["GATE", "HUMAN"], [g.title for g in gates]
    assert not any(g.title.startswith("autoclean") for g in gates), (
        "an AUTO finding with a registered fixer must not show a gate"
    )
    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["fixed", "skipped", "skipped"]
    assert rep["fixes"][0]["origin"] == "autoclean:d04"
    fixer_cells = [c for c in FakeClient.executed if "FIXERS[" in c]
    assert fixer_cells == [c for c in fixer_cells if "FIXERS[4]" in c], fixer_cells
    assert len(fixer_cells) == 1, "only the AUTO finding may reach a fixer"
    assert "policy:autonomy-auto" in gate_notes(session)


def test_autonomous_keeps_verify_then_revert_on_a_silent_fix(tmp_path, monkeypatch):
    """R2 in the silent path: no gate was shown, so the re-check is the only
    thing standing between a bad fixer and the user's data. When it fails the
    frame is reverted and the finding goes to the model."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    session = make_session(tmp_path)
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # the silent autoclean apply
        [err("signal still fires")],  # ... whose verification refuses it
        [ok(value="'reverted'")],
        fp("AAA"),  # handed to the model
        [ok()],  # the model's fix cell
        fp("BBB"),
        [ok()],  # verify
        case(),
        baseline(),
        saved(),
    ]
    drive(session.clean("df"))

    assert any("_clean_backup" in c for c in FakeClient.executed), "must revert"
    rep = report_of(session)
    assert rep["fixes"][0]["origin"] == "model", "an unverified silent fix is not kept"


# --- (b) skill admission stays a human's decision ----------------------------


def test_autonomous_never_auto_admits_a_skill(tmp_path, monkeypatch):
    """Admission is governance, and governance is never unattended in any
    mode. The gate still comes out at grade HUMAN, and a skip admits nothing."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A, GOOD_PROPOSAL]))
    session = make_session(tmp_path)
    FakeClient.script = [
        diag([finding(grade="GATE")]),
        baseline(),
        fp("AAA"),
        [ok()],  # the model's fix cell
        fp("BBB"),
        [ok()],  # verify
        case(),  # freeze the case the skill must reproduce
        baseline(),
        [ok()],  # admission cell
        [ok()],  # the skill's own test
        saved(),
    ]
    events = drive(
        session.clean("df"),
        decisions=[GateDecision("run"), GateDecision("skip")],
    )

    admit = [
        e
        for e in events
        if isinstance(e, GateRequest) and e.title.startswith("admit skill")
    ]
    assert admit, [e.title for e in events if isinstance(e, GateRequest)]
    assert admit[0].grade == "HUMAN"
    assert report_of(session)["skills_admitted"] == []
    assert session.library.entries == {}


# --- (c) careful is today's behavior ------------------------------------------


def test_careful_reproduces_the_gated_flow(tmp_path, monkeypatch):
    """The opt-in careful mode arms nothing and gates the same AUTO finding
    autonomous silences."""
    calls: list = []

    def generate(messages, model=None):
        calls.append(list(messages))
        yield "unreachable"

    monkeypatch.setattr(llm, "generate", generate)
    session = make_session(tmp_path, autonomy="careful")
    assert session.policies == [], "careful must seed no standing policy"
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # the apply cell, once the gate is answered
        [ok()],  # verify
        baseline(),
        saved(),
    ]
    events = drive(session.clean("df"))

    gates = [e for e in events if isinstance(e, GateRequest)]
    assert len(gates) == 1 and gates[0].title.startswith("autoclean")
    assert gates[0].grade == "AUTO"
    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["fixed"]
    assert rep["fixes"][0]["origin"] == "autoclean:d04"
    assert calls == []
    assert "policy:autonomy-auto" not in gate_notes(session)


# --- (d) report-only looks and does not touch ---------------------------------


def test_report_only_writes_a_report_and_changes_no_cell(tmp_path, monkeypatch):
    """The diagnosis is the whole deliverable: one detection cell runs, and
    nothing else — no baseline, no fixer, no gate, no cleaned copy."""
    calls: list = []

    def generate(messages, model=None):
        calls.append(list(messages))
        yield "unreachable"

    monkeypatch.setattr(llm, "generate", generate)
    session = make_session(tmp_path, autonomy="report-only")
    assert session.policies == [], "report-only must seed no standing policy"
    FakeClient.script = [
        diag([finding(), finding(disease=6, slug="whitespace-damage", grade="GATE")])
    ]
    events = drive(session.clean("df"))

    assert len(FakeClient.executed) == 1, FakeClient.executed
    assert "detect_all" in FakeClient.executed[0]
    assert not [e for e in events if isinstance(e, GateRequest)]
    assert calls == [], "report-only never pays for a model call"
    shown = "".join(e.text for e in events if isinstance(e, StreamText))
    assert "sentinel-missing" in shown, "the operator must still see the diagnosis"
    rep = report_of(session)
    assert rep["clear"] == [1, 2]
    assert rep["outputs"] == {}
    assert not (session.session_dir / "cleaned").exists()


def test_report_only_writes_down_the_findings_it_did_not_attempt(tmp_path, monkeypatch):
    """The level whose whole job is to say what is wrong must not file a clean
    bill of health. With no records the artifact read "0 fixed · 0 skipped ·
    0 failed · 0 not attempted · 0 flagged · 2 signals clear" over a frame
    holding a GATE and a HUMAN finding, and every headless caller reads its
    summary from exactly this file."""
    monkeypatch.setattr(llm, "generate", gen([]))
    session = make_session(tmp_path, autonomy="report-only")
    findings = [
        finding(disease=6, slug="whitespace-damage", grade="GATE"),
        finding(disease=7, slug="encoding-mojibake", grade="HUMAN", columns=["b"]),
    ]
    FakeClient.script = [diag(findings)]
    drive(session.clean("df"))

    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["aborted", "aborted"]
    assert [f["finding"]["slug"] for f in rep["fixes"]] == [
        "whitespace-damage",
        "encoding-mojibake",
    ]
    assert [f["fix_source"] for f in rep["fixes"]] == [None, None], (
        "recorded as found, not as attempted"
    )
    assert not any(f["unattended"] for f in rep["fixes"])
    md = (session.session_dir / "clean_reports" / "r001.md").read_text()
    assert "0 fixed · 0 skipped · 0 failed · 2 not attempted" in md


def test_the_headless_summary_of_a_report_only_run_is_not_empty(tmp_path, monkeypatch):
    """`--clean --autonomy report-only` printed "0/0 findings fixed" and
    exited 0 over a dirty file, because run_clean_once builds its summary from
    the report and drops the diagnosis StreamText. A CI job saw nothing."""
    from crivo.kernel.client import StreamOut
    from crivo.repl import run_clean_once

    monkeypatch.setattr(llm, "generate", gen([]))
    csv = tmp_path / "dirty.csv"
    csv.write_text("a,b\n1,2\n")
    session = make_session(tmp_path, autonomy="report-only")
    FakeClient.script = [
        [StreamOut("stdout", "profile\n"), ok()],  # the load cell
        diag([finding(), finding(disease=6, slug="whitespace-damage", grade="GATE")]),
    ]
    summary = run_clean_once(session, str(csv), name="df")

    assert [f["slug"] for f in summary["fixes"]] == [
        "sentinel-missing",
        "whitespace-damage",
    ]
    assert [f["status"] for f in summary["fixes"]] == ["aborted", "aborted"]
    assert summary["autonomy"] == "report-only"


# --- the family path ----------------------------------------------------------

FAMILY = ("data/vancouver/*.csv", "tax")


def test_report_only_never_harmonizes_a_family(tmp_path, monkeypatch):
    """Harmonizing renames columns across every slice at once, and it ran
    before the per-slice short circuit could stop it: report-only reported on
    frames it had already rewritten, and the family summary called it a
    success. Nothing may be applied at this level, family path included."""
    gen_stub = counting_generate([])
    monkeypatch.setattr(llm, "generate", gen_stub)
    session = make_session(tmp_path, autonomy="report-only")
    stock_skill(session, tmp_path, 20, "fix-schema-drift-tax")
    FakeClient.script = [
        family_meta(),
        drift_finding(),
        [ok()],  # bind slice 1 — and nothing else may run
        [ok()],  # bind slice 2
    ]
    events = drive(session.clean_family(*FAMILY))

    assert not [e for e in events if isinstance(e, GateRequest)]
    assert gen_stub.calls == [], "no mapping is derived either"
    assert not any("fix(v, [])" in c for c in FakeClient.executed), FakeClient.executed
    assert not any("_family_backup" in c for c in FakeClient.executed)
    summary = json.loads((session.session_dir / "family_tax.json").read_text())
    assert summary["harmonized"] is False
    assert summary["drift_findings"] == 1


def test_replaying_a_confirmed_mapping_asks_first_in_every_mode(tmp_path, monkeypatch):
    """detect_family grades schema drift GATE, and a person grade is never
    decided unattended — not by autonomy, and not by a skill's earned trust
    either, since library.unattended() refuses anything but AUTO. The replay
    used to execute a library skill over every slice with no GateRequest at
    all, which renamed the columns of a whole family with nobody asked."""
    gen_stub = counting_generate([])
    monkeypatch.setattr(llm, "generate", gen_stub)
    session = make_session(tmp_path)  # the autonomous default
    stock_skill(session, tmp_path, 20, "fix-schema-drift-tax")
    FakeClient.script = [
        family_meta(),
        drift_finding(),
        [ok()],  # family baseline — taken only once the gate says run
        [ok()],  # the mapping applied
        [ok()],  # family verification passes
        [ok()],  # bind slice 1
        [ok()],  # bind slice 2
    ]
    events = drive(session.clean_family(*FAMILY))

    gates = [e for e in events if isinstance(e, GateRequest)]
    assert [g.grade for g in gates] == ["GATE"], [g.title for g in gates]
    assert gates[0].title.startswith("replay fix-schema-drift-tax")
    assert "fix(v, [])" in gates[0].code, "the gate must show the cell that runs"
    assert json.loads((session.session_dir / "family_tax.json").read_text())[
        "harmonized"
    ], "an answered gate still replays the mapping for free"


@pytest.mark.parametrize("answer", [None, GateDecision("skip")])
def test_a_mapping_nobody_approved_never_touches_a_slice(tmp_path, monkeypatch, answer):
    """The third hard line, on the one gate site that had no decision to
    coerce. A non-decision and a skip both leave every slice as it arrived,
    and the family summary says harmonizing did not happen."""
    gen_stub = counting_generate([])
    monkeypatch.setattr(llm, "generate", gen_stub)
    session = make_session(tmp_path)
    stock_skill(session, tmp_path, 20, "fix-schema-drift-tax", state="probation")
    FakeClient.script = [
        family_meta(),
        drift_finding(),
        [ok()],  # _harmonize's own baseline: the gated path this falls through to
        [ok()],  # bind slice 1
        [ok()],  # bind slice 2
    ]
    events = drive(session.clean_family(*FAMILY), decisions=[answer])

    assert not any("fix(v, [])" in c for c in FakeClient.executed), FakeClient.executed
    assert sum("_family_backup" in c for c in FakeClient.executed) == 1, (
        "one baseline, _harmonize's — the replay must take none of its own"
    )
    assert "skip" in gate_actions(session)
    notices = [e.text for e in events if isinstance(e, Notice)]
    assert not any("harmonized the family" in n for n in notices), notices
    assert (
        json.loads((session.session_dir / "family_tax.json").read_text())["harmonized"]
        is False
    )


# --- (e) the first-run notice -------------------------------------------------


def _silent_fix_script():
    return [
        diag([finding()]),
        baseline(),
        [ok()],  # the silent autoclean apply
        [ok()],  # its verification
        baseline(),
        saved(),
    ]


def test_the_first_unattended_edit_is_announced_once_per_workspace(
    tmp_path, monkeypatch
):
    """A person deserves to be told the first time software edits their data
    without asking — and to be told once, not on every fix forever. The
    sentinel file is the mechanism, because events.Notice is ephemeral and the
    transcript gate note records every silent apply by design."""
    monkeypatch.setattr(llm, "generate", gen([]))
    workspace = tmp_path / "ws"

    first = make_session(tmp_path, workspace=workspace)
    FakeClient.script = _silent_fix_script()
    events = drive(first.clean("df"))
    notices = [e for e in events if isinstance(e, Notice) and e.kind == "autonomy"]
    assert len(notices) == 1, [e for e in events if isinstance(e, Notice)]
    assert "without asking" in notices[0].text
    assert "careful" in notices[0].text, "the notice must name the way out"
    assert (workspace / SENTINEL).exists()

    second = make_session(tmp_path, workspace=workspace)
    FakeClient.script = _silent_fix_script()
    events = drive(second.clean("df"))
    assert not [e for e in events if isinstance(e, Notice) and e.kind == "autonomy"], (
        "the same workspace must not be told twice"
    )
    assert report_of(second)["fixes"][0]["status"] == "fixed", (
        "the second run still fixes; only the announcement is suppressed"
    )


def test_the_notice_does_not_fire_for_a_caller_supplied_policy(tmp_path, monkeypatch):
    """The notice speaks for the autonomy default, not for any silence. A
    bench or plan policy batching the same finding is the caller's own
    decision, already recorded in the transcript gate note."""
    from crivo.policy import PolicyRecord

    monkeypatch.setattr(llm, "generate", gen([]))
    session = make_session(tmp_path)
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
    FakeClient.script = _silent_fix_script()
    events = drive(session.clean("df"))

    assert not [e for e in events if isinstance(e, Notice) and e.kind == "autonomy"]
    assert not (session.workspace_root / SENTINEL).exists()
    assert "policy:bench-auto" in gate_notes(session)


def test_an_unreadable_sentinel_never_fails_the_fix(tmp_path, monkeypatch):
    """Best-effort: a workspace crivo cannot write to gets the notice again,
    which is a repeated line, not a lost fix."""
    monkeypatch.setattr(llm, "generate", gen([]))
    session = make_session(tmp_path)

    def boom(self, *a, **kw):
        raise OSError("read-only workspace")

    monkeypatch.setattr("pathlib.Path.touch", boom)
    FakeClient.script = _silent_fix_script()
    events = drive(session.clean("df"))

    assert [e for e in events if isinstance(e, Notice) and e.kind == "autonomy"]
    assert report_of(session)["fixes"][0]["status"] == "fixed"


def _proven_skill_script():
    return [
        diag([finding()]),
        baseline(),
        [ok()],  # the skill's fix, applied with no gate shown
        [ok()],  # its verification
        baseline(),
        saved(),
    ]


@pytest.mark.parametrize("autonomy", ["autonomous", "careful"])
def test_a_proven_skill_that_fixes_unattended_announces_itself_too(
    tmp_path, monkeypatch, autonomy
):
    """The library rung runs BEFORE the autoclean rung and applies a proven
    skill to an AUTO finding with no gate shown. Announcing only the autoclean
    rung left the first unattended edit of a fresh workspace unannounced AND
    the sentinel unwritten, so the notice went on to fire later and blame some
    other run's fix for changing the data.

    It fires at careful too, because careful is where an unattended change is
    most surprising — and the way out it offers there is the true one: the
    flag that stops a proven skill is report-only, not careful.
    """
    gen_stub = counting_generate([])
    monkeypatch.setattr(llm, "generate", gen_stub)
    workspace = tmp_path / "ws"
    session = make_session(tmp_path, autonomy=autonomy, workspace=workspace)
    stock_skill(session, tmp_path, 4, "fix-sentinel-missing")
    FakeClient.script = _proven_skill_script()
    events = drive(session.clean("df"))

    assert not [e for e in events if isinstance(e, GateRequest)], "the silence"
    (told,) = [e for e in events if isinstance(e, Notice) and e.kind == "autonomy"]
    assert "without asking" in told.text
    assert "fix-sentinel-missing" in told.text, "name what changed the data"
    assert "--autonomy careful" not in told.text, (
        "careful does not stop a proven skill, so it must not be offered as "
        "the way out of one"
    )
    assert (workspace / SENTINEL).exists(), "the sentinel must be burned here too"
    rec = report_of(session)["fixes"][0]
    assert rec["origin"] == "skill:fix-sentinel-missing"
    assert rec["unattended"] is True
    assert gen_stub.calls == []


def test_a_gated_skill_fix_announces_nothing(tmp_path, monkeypatch):
    """A skill on probation is gated, so somebody was asked and there is
    nothing to announce. The sentinel stays unburned for the run that really
    does change data unattended."""
    gen_stub = counting_generate([])
    monkeypatch.setattr(llm, "generate", gen_stub)
    session = make_session(tmp_path)
    stock_skill(session, tmp_path, 4, "fix-sentinel-missing", state="probation")
    FakeClient.script = _proven_skill_script()
    events = drive(session.clean("df"))

    assert [e for e in events if isinstance(e, GateRequest)], "probation is gated"
    assert not [e for e in events if isinstance(e, Notice) and e.kind == "autonomy"]
    assert not (session.workspace_root / SENTINEL).exists()
    assert report_of(session)["fixes"][0]["unattended"] is False


def test_the_headless_surface_hands_the_notice_to_its_caller(tmp_path, monkeypatch):
    """`--clean` is the default autonomous surface AND the one driver that
    renders no Notice, so the first unattended edit announced itself into a
    void: the sentinel was written, nobody was told, and the notice fires
    once. run_clean_once must carry it out in the summary."""
    from crivo.kernel.client import StreamOut
    from crivo.repl import run_clean_once

    monkeypatch.setattr(llm, "generate", gen([]))
    csv = tmp_path / "dirty.csv"
    csv.write_text("a,b\n1,2\n")
    session = make_session(tmp_path)
    FakeClient.script = [
        [StreamOut("stdout", "profile\n"), ok()],  # the load cell
        *_silent_fix_script(),
    ]
    summary = run_clean_once(session, str(csv), name="df")

    assert any("without asking" in text for text in summary.get("notices", [])), summary
    assert [f["status"] for f in summary["fixes"]] == ["fixed"]
    assert [f["unattended"] for f in summary["fixes"]] == [True], (
        "an agent driving this surface must not have to open the report JSON "
        "to ask which changes nobody approved"
    )
    assert summary["autonomy"] == "autonomous"


# --- (f) the record of it (packet 4) ------------------------------------------


def meta_of(session):
    return next(
        ev for ev in session.transcript.events() if ev.get("kind") == "session_meta"
    )


def test_a_silent_fix_is_recorded_as_the_change_nobody_approved(tmp_path, monkeypatch):
    """R4, end to end. The transcript's first record names the posture, and
    the fix record says a gate was never shown, so "which changes did nobody
    approve?" is answerable from the artifacts, not from remembering the
    flags the run was launched with."""
    monkeypatch.setattr(llm, "generate", gen([]))
    session = make_session(tmp_path)
    FakeClient.script = _silent_fix_script()
    drive(session.clean("df"))

    assert meta_of(session)["autonomy"] == "autonomous"
    rep = report_of(session)
    assert rep["autonomy"] == "autonomous"
    (rec,) = rep["fixes"]
    assert rec["status"] == "fixed"
    assert rec["finding"]["grade"] == "AUTO"
    assert rec["origin"].startswith("autoclean:")
    assert rec["unattended"] is True
    assert rec["verify"]["layer1"] == "pass"


def test_the_same_fix_under_careful_is_recorded_as_approved(tmp_path, monkeypatch):
    """The flag has to distinguish the two runs, or it records nothing. Same
    finding, same fixer, same verification: only the gate differs."""
    monkeypatch.setattr(llm, "generate", gen([]))
    session = make_session(tmp_path, autonomy="careful")
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # the apply cell, once the gate is answered
        [ok()],  # verify
        baseline(),
        saved(),
    ]
    drive(session.clean("df"))

    assert meta_of(session)["autonomy"] == "careful"
    rep = report_of(session)
    assert rep["autonomy"] == "careful"
    assert rep["fixes"][0]["unattended"] is False


def test_a_model_fix_is_never_recorded_as_unattended(tmp_path, monkeypatch):
    """Every model-authored fix passes a gate, in every mode. The flag is
    present and False rather than absent, because a missing key reads as "not
    recorded" and would leave the audit question unanswered."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    session = make_session(tmp_path)
    FakeClient.script = [
        diag([finding(disease=6, slug="whitespace-damage", grade="GATE")]),
        baseline(),
        fp("AAA"),
        [ok()],  # the model's fix cell
        fp("BBB"),
        [ok()],  # verify
        case(),
        baseline(),
        [ok()],  # admission cell
        [ok()],  # the skill's own test
        saved(),
    ]
    drive(
        session.clean("df"),
        decisions=[GateDecision("run"), GateDecision("skip")],
    )

    rec = report_of(session)["fixes"][0]
    assert rec["origin"] == "model"
    assert rec["unattended"] is False


def test_report_only_records_the_level_it_looked_under(tmp_path, monkeypatch):
    """A diagnosis-only run still has a posture worth naming: it is the record
    that nothing was applied on purpose, not that nothing was found."""
    monkeypatch.setattr(llm, "generate", gen([]))
    session = make_session(tmp_path, autonomy="report-only")
    FakeClient.script = [diag([finding()])]
    drive(session.clean("df"))

    assert meta_of(session)["autonomy"] == "report-only"
    assert report_of(session)["autonomy"] == "report-only"


def test_the_provenance_graph_carries_the_unapproved_step(tmp_path, monkeypatch):
    """The DAG is built from the artifacts on disk, so if the report does not
    carry the flag the graph cannot either. An autonomous run has to be at
    least as auditable as a careful one (R4)."""
    from crivo import provenance

    monkeypatch.setattr(llm, "generate", gen([]))
    session = make_session(tmp_path)
    FakeClient.script = _silent_fix_script()
    drive(session.clean("df"))

    dag = provenance.build(session.session_dir)
    (fix,) = [n for n in dag["nodes"].values() if n["kind"] == "fix"]
    assert fix["origin"].startswith("autoclean:")
    assert fix["checks_passed"] is True, "the real verification verdict"
    assert fix["unattended"] is True
    assert fix["autonomy"] == "autonomous"
    assert "unattended" in provenance.to_markdown(dag)


def test_a_reverted_silent_fix_leaves_no_unapproved_change_on_the_record(
    tmp_path, monkeypatch
):
    """Verify-then-revert means the silent attempt changed nothing that
    survived, so the record an auditor reads is the model's gated fix, flagged
    approved. The applied-and-undone attempt lives in the transcript."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    session = make_session(tmp_path)
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # the silent autoclean apply
        [err("signal still fires")],  # ... refused by its own re-check
        [ok(value="'reverted'")],
        fp("AAA"),
        [ok()],  # the model's fix cell
        fp("BBB"),
        [ok()],  # verify
        case(),
        baseline(),
        saved(),
    ]
    drive(session.clean("df"))

    (rec,) = report_of(session)["fixes"]
    assert rec["origin"] == "model"
    assert rec["unattended"] is False
    assert "policy:autonomy-auto" in gate_notes(session), (
        "the attempt nobody approved is still visible in the transcript"
    )


# --- the constructor contract -------------------------------------------------


def test_an_unknown_autonomy_level_is_refused_at_construction(tmp_path):
    """A typo must not quietly pick a posture. 'report-onlyy' falling through
    to the gated flow would still let a driver that answers gates apply fixes,
    which is precisely what the level was chosen to prevent."""
    with pytest.raises(ValueError, match="report-onlyy"):
        make_session(tmp_path, autonomy="report-onlyy")


def test_the_autonomous_default_arms_only_the_fixer_diseases(tmp_path):
    from crivo import autoclean

    session = make_session(tmp_path)
    (record,) = session.policies
    assert record.id == "autonomy-auto"
    assert record.disease_ids == tuple(sorted(autoclean.FIXERS))
    assert record.mode == "ENFORCE"


def test_explicit_policies_are_not_overridden_by_the_seed(tmp_path):
    from crivo.policy import PolicyRecord

    mine = PolicyRecord(
        id="mine",
        disease_ids=(4,),
        approver="test",
        expires="2099-01-01",
        mode="ENFORCE",
        valid_disease_ids={4},
    )
    session = Session(
        workspace=tmp_path / "ws",
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        preview=False,
        snapshots=False,
        policies=[mine],
    )
    assert [p.id for p in session.policies] == ["mine"]


# --- the CLI surface ----------------------------------------------------------


class _RecordingSession:
    """Stands in for loop.Session so main() can be driven with no kernel."""

    seen: ClassVar[list] = []

    def __init__(self, **kwargs):
        _RecordingSession.seen.append(kwargs)
        self.datasets = []
        self.session_dir = None

    def close(self):
        pass


@pytest.fixture
def cli(monkeypatch):
    # the shell's provider choice must not decide which key main() demands
    monkeypatch.setenv("CRIVO_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr("crivo.loop.Session", _RecordingSession)
    monkeypatch.setattr("crivo.repl.run_repl", lambda session, auto_run=False: None)
    monkeypatch.setattr(
        "crivo.repl.run_clean_once",
        lambda session, path, name=None, policy="auto": {"fixes": []},
    )
    _RecordingSession.seen = []
    return _RecordingSession


@pytest.mark.parametrize("level", ["autonomous", "careful", "report-only"])
@pytest.mark.parametrize("argv", [["--clean", "x.csv"], []])
def test_the_flag_reaches_every_session_the_cli_builds(cli, monkeypatch, argv, level):
    """Both construction sites, or the flag silently does nothing on one of
    them: the headless --clean path and the interactive REPL."""
    from crivo.__main__ import main

    monkeypatch.setattr("sys.argv", ["crivo", "--autonomy", level, *argv])
    assert main() == 0
    assert [k["autonomy"] for k in cli.seen] == [level]


@pytest.mark.parametrize("argv", [["--clean", "x.csv"], []])
def test_the_cli_default_is_autonomous(cli, monkeypatch, argv):
    from crivo.__main__ import main

    monkeypatch.setattr("sys.argv", ["crivo", *argv])
    assert main() == 0
    assert [k["autonomy"] for k in cli.seen] == ["autonomous"]


def test_an_unknown_level_is_refused_at_the_cli(cli, monkeypatch):
    from crivo.__main__ import main

    monkeypatch.setattr("sys.argv", ["crivo", "--autonomy", "yolo"])
    with pytest.raises(SystemExit):
        main()
