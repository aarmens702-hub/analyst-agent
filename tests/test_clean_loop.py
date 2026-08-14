"""CLEAN-flow loop tests (P1 AC7): Session.clean() driven with scripted
decisions, stubbed generate(), fake kernel. Covers fixed / skipped /
rejected-then-fixed / verify-fail-revert / fail-at-cap / indicators."""

import json
from typing import ClassVar

import pytest

from analyst_agent import library, llm, skills
from analyst_agent.events import GateDecision, GateRequest, Notice, StreamText
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


def case():
    """The P2 cell that freezes the case a verified fix came from."""
    payload = json.dumps({"rows": 4, "sick": 2, "healthy": ["2", "3"]})
    return [ok(value=repr(payload))]


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
    # skills_dir must be per-test: the default is the repo's live library,
    # and a test that writes a skill there leaks into every later test
    s = Session(
        workspace=tmp_path / "ws", data_dir=tmp_path, skills_dir=tmp_path / "skills"
    )
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
        case(),  # freeze the case for skill admission
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
        case(),
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
        case(),
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


# --- P2: the library in the CLEAN flow ---------------------------------------


@pytest.fixture
def stocked(session, tmp_path):
    """A session whose library holds one skill for disease 4, on disk."""
    root = tmp_path / "skills"
    skills.save(
        skills.Skill(
            name="fix-sentinel-missing",
            description="Replace sentinel tokens with NaN when a column holds 'N/A'.",
            fix_source="def fix(df, columns):\n    return df.copy()\n",
            test_source="def test_fix():\n    assert True\n",
            metadata={"disease": "4"},
        ),
        root,
    )
    session.skills_dir = root
    session.library = library.Library(root=root)
    return session


def counting_generate(responses):
    """A stub that records how many times the model was actually consulted."""
    calls = []

    def generate(messages, model=None):
        calls.append(messages)
        yield responses.pop(0) if responses else "<execute>pass</execute>"

    generate.calls = calls
    return generate


def test_proven_skill_on_an_auto_finding_costs_no_model_call(stocked, monkeypatch):
    """AC2 + AC4: the compounding payoff. No gate, no generate()."""
    gen_stub = counting_generate([])
    monkeypatch.setattr(llm, "generate", gen_stub)
    entry = stocked.library.register("fix-sentinel-missing", disease=4)
    entry["state"] = "proven"
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # skill's fix applied
        [ok()],  # P1 verification still runs
        baseline(),
        saved(),
    ]
    events = drive(stocked.clean("df"))

    assert gen_stub.calls == [], "a proven skill must not consult the model"
    assert not [e for e in events if isinstance(e, GateRequest)]
    rep = report_of(stocked)
    assert rep["fixes"][0]["origin"] == "skill:fix-sentinel-missing"
    assert rep["fixes"][0]["status"] == "fixed"
    assert stocked.library.entries["fix-sentinel-missing"]["successes"] == 1


def test_a_skill_on_probation_still_stops_at_the_gate(stocked, monkeypatch):
    """AC4: earning silence takes a track record; a new skill has none."""
    monkeypatch.setattr(llm, "generate", counting_generate([]))
    stocked.library.register("fix-sentinel-missing", disease=4)
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],
        [ok()],
        baseline(),
        saved(),
    ]
    events = drive(stocked.clean("df"))

    gates = [e for e in events if isinstance(e, GateRequest)]
    assert len(gates) == 1
    assert "probation" in gates[0].title


def test_a_skill_that_fails_verification_hands_back_to_the_model(stocked, monkeypatch):
    """AC5: one wasted cell, not a corrupted dataset."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    stocked.library.register("fix-sentinel-missing", disease=4)
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # skill applies
        [err("signal still fires")],  # ... but verification refuses it
        [ok(value="'reverted'")],
        [ok()],  # the model's fix
        [ok()],  # verifies
        case(),
        baseline(),
        saved(),
    ]
    drive(stocked.clean("df"), decisions=[GateDecision("run"), GateDecision("run")])

    entry = stocked.library.entries["fix-sentinel-missing"]
    assert entry["failures"] == 1
    assert any("_clean_backup" in c for c in FakeClient.executed), "must revert"
    rep = report_of(stocked)
    assert rep["fixes"][0]["status"] == "fixed"
    assert rep["fixes"][0]["origin"] == "model", "the model rescued the finding"


def test_a_skill_applied_fix_never_spawns_another_skill(stocked, monkeypatch):
    """AC7: the depth-1 recursion cap. Skills do not breed skills."""
    gen_stub = counting_generate([])
    monkeypatch.setattr(llm, "generate", gen_stub)
    entry = stocked.library.register("fix-sentinel-missing", disease=4)
    entry["state"] = "proven"
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],
        [ok()],
        baseline(),
        saved(),
    ]
    drive(stocked.clean("df"))

    assert gen_stub.calls == [], "no proposal pass may run for a skill-applied fix"
    assert report_of(stocked)["skills_admitted"] == []


@pytest.fixture
def stocked_two(session, tmp_path):
    """A session whose library holds two skills for disease 4, on disk —
    for proving retrieval tries the ranking, not just the top hit."""
    root = tmp_path / "skills"
    skills.save(
        skills.Skill(
            name="fix-sentinel-missing",
            description="Replace sentinel tokens with NaN when a column holds 'N/A'.",
            fix_source="def fix(df, columns):\n    return df.copy()\n",
            test_source="def test_fix():\n    assert True\n",
            metadata={"disease": "4"},
        ),
        root,
    )
    skills.save(
        skills.Skill(
            name="fix-sentinel-missing-alt",
            description="Replace an alternate sentinel token with NaN.",
            fix_source="def fix(df, columns):\n    return df.copy()\n",
            test_source="def test_fix():\n    assert True\n",
            metadata={"disease": "4"},
        ),
        root,
    )
    session.skills_dir = root
    session.library = library.Library(root=root)
    return session


def test_second_candidate_rescues_when_the_first_fails_verification(
    stocked_two, monkeypatch
):
    """R11: a failed skill must not waste the library's other candidates."""
    gen_stub = counting_generate([])
    monkeypatch.setattr(llm, "generate", gen_stub)
    stocked_two.library.register("fix-sentinel-missing", disease=4)
    stocked_two.library.register("fix-sentinel-missing-alt", disease=4)
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # first skill applies
        [err("signal still fires")],  # ... but fails verification
        [ok(value="'reverted'")],  # reverted before the next candidate is tried
        [ok()],  # second skill applies
        [ok()],  # second skill verifies
        baseline(),  # refresh after fixed
        saved(),
    ]
    events = drive(
        stocked_two.clean("df"),
        decisions=[GateDecision("run"), GateDecision("run")],
    )

    assert gen_stub.calls == [], "a rescue by the library must not consult the model"
    assert [e for e in events if isinstance(e, GateRequest)]
    rep = report_of(stocked_two)
    assert rep["fixes"][0]["status"] == "fixed"
    assert rep["fixes"][0]["origin"] == "skill:fix-sentinel-missing-alt"
    assert stocked_two.library.entries["fix-sentinel-missing"]["failures"] == 1
    assert stocked_two.library.entries["fix-sentinel-missing-alt"]["successes"] == 1


def test_when_every_candidate_fails_the_model_gets_the_finding(
    stocked_two, monkeypatch
):
    """Both candidates take a scored failure before the model is asked."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    stocked_two.library.register("fix-sentinel-missing", disease=4)
    stocked_two.library.register("fix-sentinel-missing-alt", disease=4)
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # first skill applies
        [err("signal still fires")],  # ... fails verification
        [ok(value="'reverted'")],
        [ok()],  # second skill applies
        [err("still there too")],  # ... also fails verification
        [ok(value="'reverted'")],
        [ok()],  # the model's fix
        [ok()],  # verifies
        case(),
        baseline(),
        saved(),
    ]
    drive(
        stocked_two.clean("df"),
        decisions=[GateDecision("run"), GateDecision("run")],
    )

    rep = report_of(stocked_two)
    assert rep["fixes"][0]["status"] == "fixed"
    assert rep["fixes"][0]["origin"] == "model"
    assert stocked_two.library.entries["fix-sentinel-missing"]["failures"] == 1
    assert stocked_two.library.entries["fix-sentinel-missing-alt"]["failures"] == 1


def test_the_cap_bounds_how_many_candidates_are_tried(stocked_two, monkeypatch):
    """A large library must not turn one finding into a long chain of cells."""
    monkeypatch.setattr("analyst_agent.loop.SKILL_ATTEMPT_CAP", 1)
    monkeypatch.setattr(llm, "generate", gen([FIX_A]))
    stocked_two.library.register("fix-sentinel-missing", disease=4)
    stocked_two.library.register("fix-sentinel-missing-alt", disease=4)
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # the one candidate the cap allows
        [err("signal still fires")],
        [ok(value="'reverted'")],
        [ok()],  # the model's fix, since the cap stops before a second try
        [ok()],
        case(),
        baseline(),
        saved(),
    ]
    drive(stocked_two.clean("df"), decisions=[GateDecision("run")])

    rep = report_of(stocked_two)
    assert rep["fixes"][0]["origin"] == "model"
    assert stocked_two.library.entries["fix-sentinel-missing"]["failures"] == 1
    assert stocked_two.library.entries["fix-sentinel-missing-alt"]["uses"] == 0


def test_a_human_skip_ends_the_whole_chain(stocked_two, monkeypatch):
    """A skip means leave this finding alone, not try the next candidate."""
    monkeypatch.setattr(llm, "generate", counting_generate([]))
    stocked_two.library.register("fix-sentinel-missing", disease=4)
    stocked_two.library.register("fix-sentinel-missing-alt", disease=4)
    FakeClient.script = [
        diag([finding()]),
        baseline(),
    ]
    drive(stocked_two.clean("df"), decisions=[GateDecision("skip")])

    rep = report_of(stocked_two)
    assert rep["fixes"][0]["status"] == "skipped"
    assert rep["fixes"][0]["origin"] == "skill:fix-sentinel-missing"
    assert stocked_two.library.entries["fix-sentinel-missing"]["uses"] == 0
    assert stocked_two.library.entries["fix-sentinel-missing-alt"]["uses"] == 0


# --- P2.5: family mode -------------------------------------------------------


def family_meta(slices=("tax-2007", "tax-2020")):
    """The manifest the family loader prints: per-slice shape, never rows."""
    payload = json.dumps(
        [
            {
                "slice": s,
                "path": f"data/vancouver/{s}.csv",
                "rows": 100,
                "columns": ["folio", "land_value"] + ([] if i else ["note"]),
                "sep": ";",
            }
            for i, s in enumerate(slices)
        ]
    )
    return [StreamOut("stdout", payload + "\n"), ok()]


def drift_finding():
    payload = json.dumps(
        [
            {
                "disease": 20,
                "slug": "schema-drift",
                "columns": ["note"],
                "evidence": "note is missing from one era",
                "stats": {"only_in_a": ["note"], "only_in_b": []},
                "grade": "GATE",
                "confidence": 0.95,
                "indicator": False,
            }
        ]
    )
    return [StreamOut("stdout", payload + "\n"), ok()]


HARMONIZE = (
    "<execute>HARMONIZE_MAP = {'tax-2020': {}}\n"
    "def harmonize(frames):\n"
    "    return {k: v.copy() for k, v in frames.items()}\n"
    "tax = harmonize(tax)</execute>"
)


def test_family_drift_summary_never_dumps_rows(session, monkeypatch):
    """R2: the model sees shapes and a drift summary, never the data."""
    monkeypatch.setattr(llm, "generate", gen([]))
    FakeClient.script = [family_meta()]
    events = drive(session.load_family("data/vancouver/*.csv", "tax"))

    text = "".join(e.text for e in events if isinstance(e, StreamText))
    assert "2 slices" in text
    assert "sep ';'" in text
    assert "missing note" in text


GOOD_PROPOSAL = (
    "<name>fix-sentinel-missing</name>\n"
    "<description>Replace missing-data tokens with NaN. Use when a column "
    "holds 'N/A' or 'unknown' as if they were real values.</description>\n"
    "<fix>\ndef fix(df, columns):\n    out = df.copy()\n"
    "    for c in columns:\n        if c in out.columns:\n"
    "            out[c] = out[c].replace(['N/A', 'n/a'], None)\n    return out\n"
    "</fix>\n"
    "<test>\nimport pandas as pd\ndef test_fix_clears_the_token():\n"
    "    out = fix(pd.DataFrame({'x': ['N/A', '5']}), ['x'])\n"
    "    assert out['x'].isna().sum() == 1\n</test>"
)
PROPOSAL_B = GOOD_PROPOSAL.replace("fix-sentinel-missing", "fix-sentinel-missing-b")


def test_a_death_during_a_later_admission_keeps_the_earlier_skill(session, monkeypatch):
    """A human approved skill A at its gate: the folder was written and the
    in-memory library registered it. Then the kernel died inside skill B's
    admission cell — and because the ledger was only saved at the END of the
    skill pass, skill A existed on disk but was invisible to candidates() in
    every later session, with no record it was ever admitted. Admission must
    be durable the moment the human grants it."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A, FIX_B, GOOD_PROPOSAL, PROPOSAL_B]))
    FakeClient.script = [
        diag([finding(), finding(columns=["b"])]),
        baseline(),
        [ok()],  # fix A
        [ok()],  # verify A
        case(),
        baseline(),
        [ok()],  # fix B
        [ok()],  # verify B
        case(),
        baseline(),
        saved(),
        [ok()],  # skill A: admission cell reproduces the frozen case
        [ok()],  # skill A: its own test passes -> gate -> admitted
        dead_kernel(),  # skill B: the admission cell kills the kernel
    ]
    drive(session.clean("df"))

    ledger = session.skills_dir / "ledger.json"
    assert ledger.exists(), "the ledger must be saved the moment A is admitted"
    assert "fix-sentinel-missing" in json.loads(ledger.read_text())
    rep = report_of(session)
    assert "fix-sentinel-missing" in rep["skills_admitted"], (
        "the report must record the admission the human actually made"
    )


def test_closing_a_family_clean_midway_still_writes_the_summary(session, monkeypatch):
    """Ctrl-C at a gate prompt closes the abandoned generator, and CPython's
    GC closes it after CardReady or a UI rerun parks it. Yielding from inside
    the finally while GeneratorExit propagates raises RuntimeError: generator
    ignored GeneratorExit — so the save the finally exists to guarantee failed
    on exactly the exit it was meant to cover."""
    monkeypatch.setattr(llm, "generate", gen([HARMONIZE]))
    FakeClient.script = [family_meta(), drift_finding(), [ok()]]
    turn = session.clean_family("data/vancouver/*.csv", "tax")
    for _ in range(3):
        next(turn)

    turn.close()  # the operator left; this must not raise

    assert (session.session_dir / "family_tax.json").exists(), (
        "the summary is the guarantee: closing early must still write it"
    )


def test_family_harmonizes_once_then_cleans_each_slice(session, monkeypatch):
    """R4/R7: one mapping for the family, then the ordinary CLEAN per slice."""
    monkeypatch.setattr(llm, "generate", gen([HARMONIZE]))
    FakeClient.script = [
        family_meta(),
        drift_finding(),
        [ok()],  # family baseline
        [ok()],  # the harmonize cell
        [ok()],  # family verification passes
        [ok()],  # bind slice 1 to its own variable
        diag([]),  # ... and clean it: nothing to fix
        baseline(),
        [ok()],  # bind slice 2
        diag([]),
        baseline(),
    ]
    events = drive(session.clean_family("data/vancouver/*.csv", "tax"))

    notices = [e.text for e in events if isinstance(e, Notice)]
    assert any("harmonized 2 slices" in n for n in notices)
    gates = [e for e in events if isinstance(e, GateRequest)]
    assert len(gates) == 1, "one mapping for the whole family, not one per slice"
    assert "harmonize 2 slices" in gates[0].title


def test_a_mapping_that_loses_data_is_reverted(session, monkeypatch):
    """R5: renaming is not permission to drop populated cells."""
    monkeypatch.setattr(llm, "generate", gen([HARMONIZE, HARMONIZE]))
    FakeClient.script = [
        family_meta(),
        drift_finding(),
        [ok()],  # baseline
        [ok()],  # harmonize runs
        [err("harmonizing dropped populated cells")],  # ... and is refused
        [ok(value="'reverted'")],
        [ok()],  # second attempt
        [ok()],  # verification passes
        [ok()],
        diag([]),
        baseline(),
        [ok()],
        diag([]),
        baseline(),
    ]
    events = drive(session.clean_family("data/vancouver/*.csv", "tax"))

    notices = [e.text for e in events if isinstance(e, Notice)]
    assert any("harmonize rejected" in n for n in notices)
    assert any("_family_backup" in c for c in FakeClient.executed), "must revert"
    assert any("harmonized 2 slices" in n for n in notices), "and then succeed"


def test_a_family_run_summarises_across_its_slices(session, monkeypatch):
    """R8: per-slice reports answer "what happened to 2007". The family summary
    answers "did the library earn its keep across all of them"."""
    monkeypatch.setattr(llm, "generate", gen([HARMONIZE]))
    v1 = session._slice_var("tax", "tax-2007")
    v2 = session._slice_var("tax", "tax-2020")

    def reg(v):
        # the bind result must carry the slice variable, as the real kernel's
        # would — with only `df` in it, clean() refuses the unknown variable
        # and the "cleaned" slices were never cleaned at all
        return [dict(REG[0]), {"name": v, "type": "DataFrame", "shape": [100, 2]}]

    FakeClient.script = [
        family_meta(),
        drift_finding(),
        [ok()],
        [ok()],
        [ok()],
        [ok(registry=reg(v1))],
        diag([]),
        baseline(),
        [ok(registry=reg(v2))],
        diag([]),
        baseline(),
    ]
    events = drive(session.clean_family("data/vancouver/*.csv", "tax"))

    summary = json.loads((session.session_dir / "family_tax.json").read_text())
    assert summary["slices"] == ["tax-2007", "tax-2020"]
    assert summary["harmonized"] is True
    assert summary["rows"] == 200
    text = "".join(e.text for e in events if isinstance(e, StreamText))
    assert "family tax" in text


def test_a_saved_mapping_harmonizes_the_next_family_with_no_model_call(
    session, monkeypatch
):
    """P2.5 AC3: the flagship claim. The mapping is the artifact worth keeping,
    so file twenty-two costs nothing — the model is never consulted again."""
    gen_stub = counting_generate([])
    monkeypatch.setattr(llm, "generate", gen_stub)
    skills.save(
        skills.Skill(
            name="fix-schema-drift-tax",
            description="Apply the confirmed column mapping for this family.",
            fix_source="def fix(df, columns):\n    return df.copy()\n",
            test_source="def test_fix():\n    assert True\n",
            metadata={"disease": "20", "mapping": '{"note": "note"}'},
        ),
        session.skills_dir,
    )
    entry = session.library.register("fix-schema-drift-tax", disease=20)
    entry["state"] = "proven"

    FakeClient.script = [
        family_meta(),
        drift_finding(),
        [ok()],  # family baseline
        [ok()],  # the saved mapping applied
        [ok()],  # family verification passes
        [ok()],
        diag([]),
        baseline(),
        [ok()],
        diag([]),
        baseline(),
    ]
    events = drive(session.clean_family("data/vancouver/*.csv", "tax"))

    assert gen_stub.calls == [], "a confirmed mapping must not be re-derived"
    notices = [e.text for e in events if isinstance(e, Notice)]
    assert any("fix-schema-drift-tax" in n for n in notices)
    assert json.loads((session.session_dir / "family_tax.json").read_text())[
        "harmonized"
    ]


def dead_kernel():
    """Every subsequent cell reports the kernel gone."""
    return [
        ExecResult(
            status="kernel_died",
            error={"ename": "KernelDied", "evalue": "kernel died"},
            exec_count=0,
        )
    ]


def test_a_dead_kernel_is_reported_as_such_not_as_failed_fixes(session, monkeypatch):
    """The substrate dying is not the model being wrong. Reported: a kernel
    killed mid-clean marked every finding 'failed (3 attempts)' and never
    mentioned the kernel, which blames the model for an infrastructure death."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A] * 12))
    FakeClient.script = [
        diag([finding(), finding(disease=6, slug="whitespace-damage")]),
        baseline(),
        *[dead_kernel()] * 20,
    ]
    events = drive(session.clean("df"))

    notices = [e.text for e in events if isinstance(e, Notice)]
    assert any("kernel" in n.lower() for n in notices), (
        "the run must say the kernel died"
    )
    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["aborted", "aborted"]
    # Measured, not read back: `attempts` is a constant _aborted() hardcodes,
    # so asserting it is 0 passes for any implementation — including one that
    # burned the whole budget. What must be true is that the model was never
    # asked again after the kernel died.
    assert len(FakeClient.executed) <= 3, FakeClient.executed


def test_a_kernel_death_after_the_fix_loop_still_writes_a_report(session, monkeypatch):
    """Regression from the kernel-death fix itself. A death during the write
    step unwound past _save_report, so a gate-approved, verified fix mutated
    the frame and left NO record anywhere — a worse silence than the
    misreporting the fix was written to stop. The report must also not claim a
    durable result for state that died with the kernel."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A] * 6))
    FakeClient.script = [
        diag([finding()]),
        baseline(),
        [ok()],  # fix
        [ok()],  # verifies
        case(),
        baseline(),  # refresh — the loop now completes
        *[dead_kernel()] * 10,  # ... and the kernel dies during the write
    ]
    events = drive(session.clean("df"))

    rep = report_of(session)
    assert rep["fixes"][0]["status"] == "fixed"
    assert not rep["outputs"], "nothing was persisted, so claim nothing"
    assert any(isinstance(e, Notice) and "kernel" in e.text.lower() for e in events)
    md = (session.session_dir / "clean_reports" / "r001.md").read_text()
    assert "not saved" in md, "the report must say the fixes were never written out"


def test_a_dead_kernel_during_family_mode_does_not_kill_the_driver(
    session, monkeypatch
):
    """KernelLost is raised from every _exec_events call site but was caught in
    clean() only. load_family, _harmonize and the per-slice binding are all
    unguarded, and no driver catches it — so a death there unwound out of
    run_repl, skipping session.close() and orphaning the sandbox container."""
    monkeypatch.setattr(llm, "generate", gen([]))
    FakeClient.script = [dead_kernel()] * 10

    events = drive(session.clean_family("data/vancouver/*.csv", "tax"))

    assert any(isinstance(e, Notice) and "kernel" in e.text.lower() for e in events), (
        "the family flow must report the death, not raise through the driver"
    )


def test_a_family_slice_knows_which_file_it_came_from(session, monkeypatch):
    """load_family registered datasets as tax['2007'] but the slice is cleaned
    under the variable tax_2007, so _source_sha found nothing. Every family
    report recorded a null source — making its output unreachable in the
    provenance graph — and library.record(dataset="") never grew the skill's
    source list, so a skill fixing the same disease in all 21 slices could
    never reach the two distinct sources promotion requires. That is exactly
    the compounding claim P2.5 exists to demonstrate."""
    monkeypatch.setattr(llm, "generate", gen([]))
    slices = []
    for year in ("2007", "2020"):
        path = session.session_dir / f"tax-{year}.csv"
        path.write_text("folio;land_value\n1;100\n")
        slices.append(
            {
                "slice": f"tax-{year}",
                "path": str(path),
                "rows": 1,
                "columns": ["folio", "land_value"],
                "sep": ";",
            }
        )
    FakeClient.script = [[StreamOut("stdout", json.dumps(slices) + "\n"), ok()]]
    drive(session.load_family(str(session.session_dir / "tax-*.csv"), "tax"))

    var = session._slice_var("tax", "tax-2007")
    registered = {d["variable"] for d in session.datasets}
    assert var in registered, registered
    assert session._source_sha(var), "a slice must resolve to the file it came from"


def test_an_aborted_slice_is_not_counted_as_cleaned(session, monkeypatch):
    """clean() swallows KernelLost internally, and the family loop appended to
    run["cleaned"] unconditionally after it — so family_<name>.json listed
    slices whose report was 100% aborted and whose parquet was never written,
    and _write_family summed their rows into the headline. "What was actually
    cleaned, not what was found" is the comment on the field; this makes the
    comment true."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A] * 4))
    v1 = session._slice_var("tax", "tax-2007")
    v2 = session._slice_var("tax", "tax-2020")

    def reg(v):
        return [dict(REG[0]), {"name": v, "type": "DataFrame", "shape": [100, 2]}]

    FakeClient.script = [
        family_meta(),
        [StreamOut("stdout", "[]\n"), ok()],  # no drift to reconcile
        [ok(registry=reg(v1))],  # bind slice 1
        dead_kernel(),  # ... whose diagnosis kills the kernel
        [ok(registry=reg(v2))],  # bind slice 2
        diag([]),  # ... which cleans: nothing to fix
        baseline(),
    ]
    drive(session.clean_family("data/vancouver/*.csv", "tax"))

    summary = json.loads((session.session_dir / "family_tax.json").read_text())
    assert summary["slices"] == ["tax-2020"], summary["slices"]
    assert summary["rows"] == 100, "aborted slices must not inflate the headline"


def test_colliding_slice_names_stay_distinct_variables(session):
    """re.sub(r'\\W+', '_') is not injective: tax-2007, tax_2007, tax 2007 and
    tax.2007 all collapsed to one variable, so the second slice's kernel frame
    silently overwrote the first, both clean reports cited the first file's
    sha, and library.record credited the wrong dataset — defeating the
    distinct-sources promotion rule this helper was extracted to fix. Slices
    with different names must get different variables, and every variable must
    still be a Python identifier the kernel can bind."""
    keys = ["tax-2007", "tax_2007", "tax 2007", "tax.2007"]
    variables = [session._slice_var("tax", k) for k in keys]

    assert len(set(variables)) == len(keys), variables
    for variable in variables:
        assert variable.isidentifier(), variable
    # deterministic: the loader and the cleaner derive it independently
    assert variables == [session._slice_var("tax", k) for k in keys]


def test_a_dead_kernel_is_restarted_so_the_next_clean_can_run(session, monkeypatch):
    """supervisor latches _hung and only a restart clears it, so one timeout
    made every later /clean in the session abort instantly and forever, with
    nothing telling the user a restart was needed. QUERY mode already recovers
    this way; CLEAN just gave up permanently."""
    monkeypatch.setattr(llm, "generate", gen([]))
    restarts: list = []
    monkeypatch.setattr(
        session, "_restart_and_replay", lambda dead: restarts.append(dead)
    )
    FakeClient.script = [dead_kernel()] * 6

    drive(session.clean("df"))

    assert restarts, "a dead kernel must be restarted, not left latched"


def test_a_broken_detector_reaches_the_operator(session, monkeypatch):
    """detect_all reports crashed detectors in `broken`, but nothing read it —
    so the commit that added it changed nothing an operator can see. A
    systematically broken signal still reads as a slightly shorter clear list,
    which is the silence the key was added to break."""
    monkeypatch.setattr(llm, "generate", gen([]))
    payload = json.dumps(
        {
            "findings": [],
            "clear": [1, 2],
            "broken": {"7": "ValueError: cannot reindex on a duplicate axis"},
        }
    )
    FakeClient.script = [[StreamOut("stdout", payload + "\n"), ok()], baseline()]
    events = drive(session.clean("df"))

    shown = "".join(e.text for e in events if isinstance(e, StreamText))
    assert "d07" in shown and "did not run" in shown, shown
    assert report_of(session)["broken"] == {
        "7": "ValueError: cannot reindex on a duplicate axis"
    }


def test_a_family_run_records_what_it_did_even_when_the_kernel_dies(
    session, monkeypatch
):
    """The same defect as clean()'s, one function away, for the third time:
    _clean_family writes family_<name>.json as its last statement with no
    guard, so a death anywhere earlier loses skill_hits — the single number
    P2.5 exists to produce."""
    monkeypatch.setattr(llm, "generate", gen([]))
    FakeClient.script = [family_meta(), drift_finding(), *[dead_kernel()] * 10]

    drive(session.clean_family("data/vancouver/*.csv", "tax"))

    summary = session.session_dir / "family_tax.json"
    assert summary.exists(), "a family run must record what it did"
    assert json.loads(summary.read_text())["harmonized"] is False


@pytest.mark.parametrize("die_after", range(7))
def test_a_kernel_death_at_any_point_reports_recovers_and_records(
    session, monkeypatch, die_after
):
    """Written against the invariant, not against one reproduction.

    Every previous kernel fix was verified by killing the kernel at the one
    place that had broken, and each test then passed while a sibling path
    stayed broken — two handlers ended up doing half the job each, and the half
    was different. Whatever cell dies, all three must hold: the operator is
    told, a report exists, and the kernel is restarted so the session is not
    latched for good.
    """
    monkeypatch.setattr(llm, "generate", gen([FIX_A] * 8))
    restarts: list = []
    monkeypatch.setattr(
        session, "_restart_and_replay", lambda dead: restarts.append(dead)
    )
    happy = [
        diag([finding()]),
        baseline(),
        [ok()],  # the fix cell
        [ok()],  # its verification
        case(),
        baseline(),  # baseline walks forward
        saved(),  # the cleaned copy
    ]
    FakeClient.script = happy[:die_after] + [dead_kernel()] * 12
    events = drive(session.clean("df"))

    assert any(isinstance(e, Notice) and "kernel" in e.text.lower() for e in events), (
        "the operator must be told the kernel died"
    )
    assert list((session.session_dir / "clean_reports").glob("*.json")), (
        "a report must exist whatever cell died"
    )
    assert restarts, "the kernel must be restarted or the session stays latched"


@pytest.mark.parametrize("die_after", range(7))
def test_after_recovery_the_next_clean_actually_works(session, monkeypatch, die_after):
    """Restarting is not recovering. The test above monkeypatches
    _restart_and_replay away, so it proved the restart was *requested* while
    the real replay threw away every replayed load's registry — and the next
    /clean greeted the survivor with "unknown variable 'df'", on every death
    path, forever. The invariant is the session is USABLE afterwards, so this
    drives the real replay and then runs a whole second clean through it."""
    monkeypatch.setattr(llm, "generate", gen([FIX_A] * 16))
    session.loads.append(("df", "df = pd.read_csv('x.csv')"))

    real_execute = FakeClient.execute

    def forgiving(self, code, timeout_s=120):
        # the replay happens after the scripted death; feed it a plain ok
        # instead of an IndexError so the real _restart_and_replay can run
        if not FakeClient.script:
            FakeClient.executed.append(code)
            yield ok()
            return
        yield from real_execute(self, code, timeout_s)

    monkeypatch.setattr(FakeClient, "execute", forgiving)
    happy = [
        diag([finding()]),
        baseline(),
        [ok()],
        [ok()],
        case(),
        baseline(),
        saved(),
    ]
    FakeClient.script = happy[:die_after] + [dead_kernel()]
    drive(session.clean("df"))

    FakeClient.script = list(happy)
    events = drive(session.clean("df"))

    unknown = [
        e for e in events if isinstance(e, Notice) and "unknown variable" in e.text
    ]
    assert not unknown, f"recovered session refused to work: {unknown[0].text}"
    rep = report_of(session)
    assert [f["status"] for f in rep["fixes"]] == ["fixed"], (
        "the second clean must run end to end on the recovered session"
    )
