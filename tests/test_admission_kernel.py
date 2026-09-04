"""P2 AC1/AC3 against a real kernel and real data.

The admission gate is the load-bearing claim of the whole harness: a
generalised fix earns its place by reproducing the case it came from. A test
with a faked kernel cannot check that, because the thing being checked IS the
execution. So this drives the real subprocess kernel with a real dataframe and
only the model stubbed.
"""

import json
import sys

import pytest


@pytest.fixture(autouse=True)
def _legacy_arm(monkeypatch):
    """These scripts drive model-origin fixes into admission and predate the
    T1.4 autoclean rung; the admission mechanics they check are the same in
    both arms, so they run on the pinned CRIVO_M1=off flow."""
    monkeypatch.setenv("CRIVO_M1", "off")

from crivo import llm, skills
from crivo.events import CardReady, GateDecision, GateRequest, Notice
from crivo.loop import Session

SUBPROCESS_ARGV = [sys.executable, "-m", "crivo.kernel.supervisor"]

# a frame with one clearly sick column: "N/A" masquerading as a value (disease 4)
SEED = (
    "import pandas as pd\n"
    "beers = pd.DataFrame({\n"
    "    'name': [f'beer {i}' for i in range(32)],\n"  # unique, so no dup-row finding
    "    'ibu': ['N/A'] * 12 + [str(v) for v in range(20, 40)],\n"
    "})\n"
    "beers.shape"
)

GOOD_FIX = (
    "<execute>\n"
    "def fix_sentinel_missing(df):\n"
    "    out = df.copy()\n"
    "    out['ibu'] = out['ibu'].replace('N/A', None)\n"
    "    return out\n"
    "beers = fix_sentinel_missing(beers)\n"
    "assert (beers['ibu'] == 'N/A').sum() == 0\n"
    "</execute>"
)

# generalised, honest: only touches the columns it was handed
GOOD_PROPOSAL = (
    "<name>fix-sentinel-missing</name>\n"
    "<description>Replace missing-data tokens with NaN. Use when a column "
    "holds 'N/A' or 'unknown' as if they were real values.</description>\n"
    "<fix>\n"
    "def fix(df, columns):\n"
    "    out = df.copy()\n"
    "    for c in columns:\n"
    "        if c in out.columns:\n"
    "            out[c] = out[c].replace(['N/A', 'n/a', 'unknown'], None)\n"
    "    return out\n"
    "</fix>\n"
    "<test>\n"
    "import pandas as pd\n"
    "def test_fix_clears_the_token():\n"
    "    out = fix(pd.DataFrame({'x': ['N/A', '5']}), ['x'])\n"
    "    assert out['x'].isna().sum() == 1\n"
    "</test>"
)


def scripted(responses):
    queue = list(responses)

    def generate(messages, model=None):
        yield queue.pop(0) if queue else "<answer>done</answer>"

    return generate


def drive(gen, decisions=()):
    decisions = list(decisions)
    events = []
    try:
        event = next(gen)
        while True:
            events.append(event)
            answer = None
            if isinstance(event, GateRequest):
                answer = decisions.pop(0) if decisions else GateDecision("run")
            if isinstance(event, CardReady):
                return events
            event = gen.send(answer)
    except StopIteration:
        return events


@pytest.fixture
def seeded(tmp_path):
    session = Session(
        workspace=tmp_path / "ws",
        data_dir=tmp_path,
        transport_argv=SUBPROCESS_ARGV,
        skills_dir=tmp_path / "skills",
    )
    result = None
    for ev in session.client.execute(SEED, timeout_s=60):
        result = ev
    assert result.status == "ok", result.error
    session._stamp_registry(result.registry, 1)
    yield session
    session.close()


def test_a_faithful_generalisation_is_admitted(seeded, monkeypatch):
    """AC1: the round trip, executed. The generalised fix re-runs against the
    frozen case, its own test runs, and only then does a skill exist on disk."""
    monkeypatch.setattr(llm, "generate", scripted([GOOD_FIX, GOOD_PROPOSAL]))
    drive(seeded.clean("beers"))

    admitted = sorted(p.name for p in seeded.skills_dir.glob("fix-*"))
    assert admitted == ["fix-sentinel-missing"]
    skill = skills.load(seeded.skills_dir / "fix-sentinel-missing")
    assert "def fix(df, columns)" in skill.fix_source
    assert skill.metadata["disease"] == "4"
    assert seeded.library.entries["fix-sentinel-missing"]["state"] == "probation"


# generalised, but it heals the sick by harming the healthy: it blanks the lot
DESTRUCTIVE_PROPOSAL = GOOD_PROPOSAL.replace(
    "            out[c] = out[c].replace(['N/A', 'n/a', 'unknown'], None)",
    "            out[c] = None",
)


def test_a_generalisation_that_harms_healthy_rows_is_refused(seeded, monkeypatch):
    """AC3: it clears the detector — by destroying the column. The frozen case
    catches exactly that, and nothing is written under skills/."""
    monkeypatch.setattr(llm, "generate", scripted([GOOD_FIX, DESTRUCTIVE_PROPOSAL]))
    events = drive(seeded.clean("beers"))

    assert not list(seeded.skills_dir.glob("fix-*")), "a refused skill leaves no folder"
    refusals = [
        e.text
        for e in events
        if isinstance(e, Notice) and e.kind == "skill" and "refused" in e.text
    ]
    assert refusals, "the refusal must be reported, not silent"


def test_the_frozen_case_holds_sick_rows_and_healthy_ones(seeded, monkeypatch):
    """R7: a case of only-sick rows could never prove a fix leaves anything
    alone, which is half of what admission has to check."""
    monkeypatch.setattr(llm, "generate", scripted([GOOD_FIX, GOOD_PROPOSAL]))
    drive(seeded.clean("beers"))

    assert list((seeded.session_dir / "skill_cases").glob("*.parquet"))
    report = json.loads(
        max((seeded.session_dir / "clean_reports").glob("*.json")).read_text()
    )
    case = next(r["case"] for r in report["fixes"] if r["status"] == "fixed")
    assert case["sick"] == 12, "the rows the fix actually changed"
    assert case["rows"] > case["sick"], "healthy rows ride along"


def test_a_verified_fix_survives_a_kernel_death(seeded, monkeypatch):
    """P5 AC5, against the real kernel. The replay brings back only the
    original loads — and this frame was never loaded from a file at all, so
    after a SIGKILL the snapshot is the only road back. If the restored frame
    still carries the verified fix, R8 has done its whole job."""
    monkeypatch.setattr(llm, "generate", scripted([GOOD_FIX, GOOD_PROPOSAL]))
    drive(seeded.clean("beers"))
    assert (seeded.session_dir / "kernel_state.pkl").exists(), (
        "a verified fix must leave a snapshot behind"
    )

    for _ev in seeded.client.execute(
        "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)", timeout_s=30
    ):
        pass
    seeded._restart_and_replay(dead=True)

    result = None
    for ev in seeded.client.execute("int((beers['ibu'] == 'N/A').sum())", timeout_s=60):
        result = ev
    assert result.status == "ok", result.error
    assert result.value == "0", "the verified fix must survive the death"
