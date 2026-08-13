"""Protocol v1 conformance suite (spec R9–R17, AC9) — LLM-free.

Drives the real supervisor over stdio in subprocess mode (same wire format as
the container; no Docker needed). Container-specific behavior lives in the
`docker`-marked tests at the bottom, auto-skipped when the daemon is down.

The `client` fixture is a lazy accessor: the kernel is started by the first
test that calls it (so startup failures surface inside tests, not fixtures)
and shared for the rest of the module.
"""

import ast
import json
import sys
import tempfile
from pathlib import Path

import pytest

from analyst_agent.kernel.client import KernelClient, StreamOut

SUBPROCESS_ARGV = [sys.executable, "-m", "analyst_agent.kernel.supervisor"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    holder = {}

    def get() -> KernelClient:
        if "kc" not in holder:
            workspace = tmp_path_factory.mktemp("workspace")
            kc = KernelClient(workspace_dir=workspace, transport_argv=SUBPROCESS_ARGV)
            hello = kc.start()
            assert hello.proto == 1
            holder["kc"] = kc
        return holder["kc"]

    yield get
    if "kc" in holder:
        holder["kc"].close()


def test_hello_and_basic_execute(client):
    events = list(client().execute("x = 6 * 7\nx"))
    result = events[-1]
    assert result.status == "ok"
    assert result.value.strip() == "42"
    assert result.error is None
    assert result.exec_count >= 1
    assert result.elapsed_s >= 0


def test_stream_output_relayed(client):
    events = list(client().execute('print("loading...")\n"done"'))
    streams = [e for e in events if isinstance(e, StreamOut)]
    assert streams, "expected at least one stream event before the result"
    assert "loading" in "".join(s.text for s in streams)
    assert isinstance(events[-1], type(events[-1]))
    assert events[-1].value.strip() == "'done'"


def test_registry_piggybacked_on_execute(client):
    code = "import pandas as pd\ndf_r = pd.DataFrame({'a': [1, 2, 3]})\n_hidden = 1"
    r = list(client().execute(code))[-1]
    names = {e["name"]: e for e in r.registry}
    assert "df_r" in names, f"registry missing df_r: {r.registry}"
    assert names["df_r"]["type"] == "DataFrame"
    assert names["df_r"]["shape"] == [3, 1]
    assert names["df_r"]["mem_mb"] >= 0
    assert "pd" not in names, "modules must be skipped"
    assert "_hidden" not in names, "underscored names must be skipped"
    assert r.registry_omitted == 0


def test_error_traceback_trimmed_and_ansi_free(client):
    r = list(client().execute("def boom():\n    return 1 / 0\nboom()"))[-1]
    assert r.status == "error"
    assert r.error["ename"] == "ZeroDivisionError"
    assert "\x1b" not in "\n".join(r.error["traceback"]), "ANSI must be stripped"
    assert len(r.error["traceback"]) <= 36  # first 5 + last 30 + marker


def test_value_truncated_at_8kib(client):
    r = list(client().execute("'x' * 20000"))[-1]
    assert r.status == "ok"
    assert r.truncated.get("value") is True
    assert len(r.value) <= 8192
    assert "omitted" in r.value


def test_stream_capped_at_64kib(client):
    code = "for i in range(300):\n    print('y' * 400)"
    events = list(client().execute(code))
    r = events[-1]
    total = sum(len(e.text) for e in events if isinstance(e, StreamOut))
    assert r.status == "ok"
    assert r.truncated.get("stream") is True
    assert total <= 64 * 1024 + 4096, f"streamed {total} bytes past the cap"


def test_supervisor_survives_garbage_and_unknown_ops(client):
    kc = client()
    kc._send_raw("this is not json")
    res = kc._request({"op": "frobnicate"}, timeout=10)
    assert res["status"] == "bad_request"
    r = list(kc.execute("1 + 1"))[-1]
    assert r.status == "ok"
    assert r.value.strip() == "2"


def test_second_concurrent_execute_rejected(client):
    import threading
    import time as _time

    kc = client()
    outcome = {}

    def consume():
        outcome["first"] = list(
            kc.execute("import time\ntime.sleep(2)\n'ok1'", timeout_s=30)
        )[-1]

    t = threading.Thread(target=consume)
    t.start()
    _time.sleep(0.6)
    second = list(kc.execute("2 + 2", timeout_s=10))[-1]
    t.join(timeout=30)
    assert not t.is_alive()
    assert second.status == "bad_request"
    assert outcome["first"].status == "ok"
    assert outcome["first"].value.strip() == "'ok1'"


def test_client_interrupt_ends_cell_interrupted(client):
    import threading
    import time as _time

    kc = client()
    outcome = {}

    def consume():
        outcome["r"] = list(kc.execute("import time\ntime.sleep(30)", timeout_s=60))[-1]

    t = threading.Thread(target=consume)
    t.start()
    _time.sleep(0.8)
    kc.interrupt()
    t.join(timeout=20)
    assert not t.is_alive()
    assert outcome["r"].status == "interrupted"
    assert outcome["r"].error is None
    assert outcome["r"].elapsed_s < 20


def test_watchdog_timeout_status(client):
    r = list(client().execute("import time\ntime.sleep(60)", timeout_s=2))[-1]
    assert r.status == "timeout"
    assert r.error is None
    assert r.elapsed_s < 15


def test_uninterruptible_cell_goes_hung_then_restart_recovers(client):
    kc = client()
    stubborn = (
        "import time\n"
        "while True:\n"
        "    try:\n"
        "        time.sleep(1)\n"
        "    except KeyboardInterrupt:\n"
        "        pass\n"
    )
    r = list(kc.execute(stubborn, timeout_s=2))[-1]
    assert r.status == "hung"

    r2 = list(kc.execute("1 + 1", timeout_s=5))[-1]
    assert r2.status == "hung", "executes while hung must fail fast"

    kc.restart()
    r3 = list(kc.execute("'alive'", timeout_s=10))[-1]
    assert r3.status == "ok"
    assert r3.value.strip() == "'alive'"
    assert r3.registry == [], "restart must clear user state"


def test_matplotlib_chart_saved_as_artifact(client):
    from pathlib import Path

    from analyst_agent.kernel.client import DisplayItem

    code = "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.show()"
    events = list(client().execute(code))
    pngs = [e for e in events if isinstance(e, DisplayItem) and e.mime == "image/png"]
    assert len(pngs) == 1
    assert not pngs[0].dropped
    p = Path(pngs[0].payload)
    assert p.exists() and p.suffix == ".png"
    assert p.stat().st_size > 1000
    assert "artifacts" in p.parts
    assert events[-1].status == "ok"


@pytest.mark.docker
def test_container_end_to_end(tmp_path_factory):
    """AC9's container half: build the image, run net-none, prove the sandbox."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    build = subprocess.run(
        ["docker", "build", "-t", "analyst-kernel", "-f",
         str(root / "docker" / "Dockerfile"), str(root)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert build.returncode == 0, build.stderr[-2000:]

    ws = tmp_path_factory.mktemp("container-ws")
    kc = KernelClient(workspace_dir=ws)  # default docker transport
    try:
        hello = kc.start()
        assert hello.proto == 1
        r = list(kc.execute("6 * 7"))[-1]
        assert r.status == "ok" and r.value.strip() == "42"
        egress = list(
            kc.execute(
                "import socket\n"
                "try:\n"
                "    socket.create_connection(('8.8.8.8', 53), timeout=2)\n"
                "    blocked = False\n"
                "except OSError:\n"
                "    blocked = True\n"
                "blocked"
            )
        )[-1]
        assert egress.value.strip() == "True", "net-none must block egress"
    finally:
        kc.close()


@pytest.mark.docker
def test_the_sandbox_can_run_the_gates_that_admit_a_skill():
    """P2's admission cells import analyst_agent inside the kernel. If those
    modules did not make it into the image, admission would fail only in
    sandbox mode — the mode that matters — and nothing else would notice."""
    kc = KernelClient(workspace_dir=Path(tempfile.mkdtemp()))
    try:
        kc.start()
        probe = (
            "import json\n"
            "import pandas as pd\n"
            "from analyst_agent.detect import detect_all, detect_one\n"
            "from analyst_agent import library, provenance, skills, verify\n"
            "df = pd.DataFrame({'flow': ['N/A'] * 12 + [str(v) for v in range(20)],\n"
            "                   'name': [f's{i}' for i in range(32)]})\n"
            "found = detect_all(df, 'probe')['findings']\n"
            "json.dumps({'diseases': sorted({f['disease'] for f in found}),\n"
            "            'one': detect_one(df, 4, ['flow']) is not None})"
        )
        result = list(kc.execute(probe, timeout_s=120))[-1]
        assert result.status == "ok", result.error
        payload = json.loads(ast.literal_eval(result.value))
        assert 4 in payload["diseases"], "the detector must fire inside the sandbox"
        assert payload["one"] is True, "and detect_one, which verifies every fix"
    finally:
        kc.close()
