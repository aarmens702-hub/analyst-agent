"""MCP wrapper tests (specs/2026-08-14-mcp-wrapper-design.md).

The server is a driver, like repl.py: logic lives in plain module functions
tested here against SessionLike doubles, with the FastMCP registration a thin
layer over them. One real-kernel test (AC2) proves the session tools against
the actual subprocess kernel; everything else uses doubles, in the style of
tests/test_repl.py.
"""

import json
import pathlib

import pandas as pd

from crivo import mcp_server


def test_diagnose_file_is_keyless_and_returns_json(tmp_path, monkeypatch) -> None:
    """The try-before-trust property must survive the wrapper: a stranger's
    agent can diagnose with no API key configured at all."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CRIVO_MCP_ROOT", str(tmp_path))  # the fixture file is served
    path = tmp_path / "beers.csv"
    pd.DataFrame({"ibu": ["N/A"] * 8 + [str(v) for v in range(20)]}).to_csv(
        path, index=False
    )

    report = json.loads(mcp_server._diagnose_file(str(path)))

    assert any(f["slug"] == "sentinel-missing" for f in report["findings"])
    assert "clear" in report


def test_key_needing_tools_name_the_missing_key(monkeypatch) -> None:
    """R5: the error must name the key the configured provider actually uses,
    so the person fixing their MCP client config knows which env var to add."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CRIVO_PROVIDER", raising=False)
    assert "DEEPSEEK_API_KEY" in mcp_server._clean_file("x.csv")["error"]

    monkeypatch.setenv("CRIVO_PROVIDER", "claude")
    assert "ANTHROPIC_API_KEY" in mcp_server._clean_file("x.csv")["error"]


class FakeSession:
    """SessionLike double in the tests/test_repl.py style: scripted event
    generators, recorded decisions, and just enough surface for the server."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.sent = []
        self.datasets = []
        self.history = []
        self.closed = False
        self.session_dir = None

    def load(self, path, name=None):
        variable = name or "df"
        self.datasets.append({"path": path, "variable": variable})
        self.history.append(
            {
                "role": "user",
                "content": f"<dataset variable={variable!r}>\nrows\n</dataset>",
            }
        )

    def run_turn(self, question):
        for event in self.script:
            self.sent.append((yield event))

    def clean(self, var):
        for event in self.script:
            self.sent.append((yield event))

    def close(self):
        self.closed = True


def test_clean_file_relays_needs_human_and_closes_the_session(monkeypatch) -> None:
    """AC1: the wrapper must not swallow the one thing the policy exists to
    surface — the judgement calls it refused to make — and must never leak a
    kernel, success or not."""
    from crivo.events import GateRequest

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("CRIVO_PROVIDER", raising=False)
    fake = FakeSession(
        script=[
            GateRequest("fix_a", 1, title="fix 1/2 · strip spaces", grade="AUTO"),
            GateRequest("fix_b", 1, title="fix 2/2 · merge variants", grade="GATE"),
        ]
    )
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)

    summary = mcp_server._clean_file("data/x.csv")

    assert [d.action for d in fake.sent if d is not None] == ["run", "skip"]
    assert summary["needs_human"] == ["fix 2/2 · merge variants"]
    assert summary["policy"] == "auto"
    assert fake.closed, "the kernel must be closed, success or not"


def test_ask_runs_query_cells_and_returns_the_card(monkeypatch) -> None:
    """AC1: the session tools. open_data registers a persistent session and
    hands back its profile; ask drives run_turn with the calling agent as the
    operator for the analyst's own query cells (grade "", the --auto-run trust
    position) and returns the card as a dict with its executed checks. Gates
    carrying a person's grade are a different matter, tested below."""
    from crivo.card import AnswerCard
    from crivo.events import CardReady, GateRequest

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("CRIVO_PROVIDER", raising=False)
    card = AnswerCard(
        card_id="c001",
        session="s01",
        question="how many rows?",
        answer="two rows",
        checks=[{"expr": "result == 2", "passed": True}],
    )
    fake = FakeSession(script=[GateRequest("result = 2", 1), CardReady(card)])
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)

    opened = mcp_server._open_data("data/tiny.csv")
    assert opened["variable"] == "df"
    assert "<dataset" in opened["profile"]

    result = mcp_server._ask(opened["session_id"], "how many rows?")

    assert result["answer"] == "two rows"
    assert result["checks"] == [{"expr": "result == 2", "passed": True}]
    assert [d.action for d in fake.sent if d is not None] == ["run"]
    assert not fake.closed, "the session stays open for the next question"
    mcp_server.close_all()


def test_ask_on_an_unknown_session_says_what_to_do(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("CRIVO_PROVIDER", raising=False)

    result = mcp_server._ask("nope1234", "anything")

    assert "unknown session" in result["error"]
    assert "open_data" in result["error"], "the fix must be named, not implied"


def test_idle_sessions_are_evicted_and_their_kernels_closed(monkeypatch) -> None:
    """R4: an abandoned kernel is a leaked process. Eviction happens on the
    next lookup, and the evicted session's kernel is actually closed."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("CRIVO_PROVIDER", raising=False)
    fake = FakeSession()
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)
    opened = mcp_server._open_data("data/tiny.csv")

    import time as real_time

    later = real_time.monotonic() + mcp_server.IDLE_S + 1
    monkeypatch.setattr("time.monotonic", lambda: later)

    result = mcp_server._ask(opened["session_id"], "still there?")

    assert "unknown session" in result["error"]
    assert fake.closed, "eviction must close the kernel, not just forget it"


def test_why_and_close_session_round_trip(monkeypatch, tmp_path) -> None:
    """R3: why renders the provenance chain from the session's dir on disk;
    close_session is idempotent-honest — True once, False after."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("CRIVO_PROVIDER", raising=False)
    fake = FakeSession()
    fake.session_dir = tmp_path
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)
    opened = mcp_server._open_data("data/tiny.csv")

    rendered = mcp_server._why(opened["session_id"])
    assert isinstance(rendered, str) and not rendered.startswith("error")

    assert mcp_server._close_session(opened["session_id"]) is True
    assert fake.closed
    assert mcp_server._close_session(opened["session_id"]) is False


def test_a_crashing_tool_returns_an_error_result_not_a_protocol_crash(
    monkeypatch,
) -> None:
    """R6: one bad file, one broken kernel start — the server answers with an
    error result; it never dies mid-protocol."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("CRIVO_PROVIDER", raising=False)

    def explode():
        raise RuntimeError("kernel refused to start")

    monkeypatch.setattr(mcp_server, "_make_session", explode)

    assert mcp_server._clean_file("x.csv")["error"].startswith("RuntimeError")
    assert mcp_server._open_data("x.csv")["error"].startswith("RuntimeError")


def test_the_server_exposes_exactly_the_six_tools_with_docstrings() -> None:
    """AC3: the six-tool inventory is the contract, and each docstring is
    what a calling model acts on — clean_file's must carry the policy=all
    consent warning, because that sentence is the only thing standing between
    an eager agent and unattended judgement calls."""
    import asyncio

    app = mcp_server.build_server()
    tools = {t.name: t for t in asyncio.run(app.list_tools())}

    assert set(tools) == {
        "diagnose_file",
        "clean_file",
        "open_data",
        "ask",
        "why",
        "close_session",
    }
    for tool in tools.values():
        assert tool.description and len(tool.description) > 40
    assert "consent" in tools["clean_file"].description.lower()


def test_the_elicit_decider_asks_only_for_gate_grade() -> None:
    """v1.5: GATE fixes are the client's human's call, per fix. HUMAN
    findings and admissions are never a yes/no popup — they stay deferred —
    and AUTO never bothers anyone. Decline and elicitation failure both skip
    with the reason in the note, because a popup that errored is not consent."""
    from types import SimpleNamespace

    from crivo.events import GateRequest

    asked: list[str] = []

    def accept(message: str):
        asked.append(message)
        return SimpleNamespace(action="accept", data=SimpleNamespace(approve=True))

    decide = mcp_server._make_decider(accept)
    auto = GateRequest("c", 1, title="fix 1/3 · auto", grade="AUTO")
    human = GateRequest("c", 1, title="fix 2/3 · human", grade="HUMAN")
    gate = GateRequest(
        "c", 1, title="fix 3/3 · gate", preview="a: 2 cells", grade="GATE"
    )

    assert decide(auto).action == "run"
    assert decide(human).action == "skip"
    assert asked == [], "AUTO and HUMAN must never reach the client"
    assert decide(gate).action == "run"
    assert asked and "fix 3/3 · gate" in asked[0] and "a: 2 cells" in asked[0]

    def decline(message: str):
        return SimpleNamespace(action="decline", data=None)

    decision = mcp_server._make_decider(decline)(gate)
    assert decision.action == "skip" and "declined" in decision.note

    def boom(message: str):
        raise RuntimeError("client hung up")

    decision = mcp_server._make_decider(boom)(gate)
    assert decision.action == "skip" and "unavailable" in decision.note


def test_clean_file_threads_the_decide_callback_through(monkeypatch) -> None:
    """The tool layer hands its elicitation decider to the driver; without
    this seam the decider exists but every clean still runs blanket policy."""
    from types import SimpleNamespace

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    received: dict = {}

    def fake_run(session, path, name=None, policy="auto", decide=None):
        received.update(path=path, policy=policy, decide=decide)
        return {"file": path, "needs_human": []}

    monkeypatch.setattr("crivo.repl.run_clean_once", fake_run)
    monkeypatch.setattr(
        mcp_server, "_make_session", lambda: SimpleNamespace(close=lambda: None)
    )
    sentinel = object()

    summary = mcp_server._clean_file("x.csv", decide=sentinel)

    # the RESOLVED path is what goes downstream, not the client's string: the
    # check and the read must not resolve it twice (see the TOCTOU test below)
    assert summary["file"] == str(pathlib.Path("x.csv").resolve())
    assert received["decide"] is sentinel


def test_client_capability_detection_fails_closed(monkeypatch) -> None:
    """No elicitation capability, no popup — exactly v1 behavior. The check
    itself failing must read as 'cannot ask', never as a crash."""
    from types import SimpleNamespace

    def ctx_with(answer):
        session = SimpleNamespace(check_client_capability=lambda cap: answer)
        return SimpleNamespace(request_context=SimpleNamespace(session=session))

    assert mcp_server._client_can_elicit(ctx_with(True)) is True
    assert mcp_server._client_can_elicit(ctx_with(False)) is False

    class Exploding:
        @property
        def request_context(self):
            raise RuntimeError("no request context outside a call")

    assert mcp_server._client_can_elicit(Exploding()) is False
    assert mcp_server._client_can_elicit(None) is False


def test_session_chatter_never_reaches_stdout(monkeypatch) -> None:
    """In stdio MCP mode, stdout IS the protocol channel — Session prints
    'starting kernel', 'loaded ...', and the profile, which a strict client
    parses as corrupt JSON-RPC. Found live: a real client warned 'Invalid
    JSON: starting kernel' mid-handshake. The tool paths must route every byte
    of that chatter to stderr, the way the CLI already does."""
    import contextlib
    import io

    class ChattySession:
        def __init__(self):
            print("starting kernel (subprocess) …")  # to stdout, like the real one
            self.datasets = []
            self.history = []
            self.closed = False
            self.session_dir = None

        def load(self, path, name=None):
            print(f"loaded {path} → df")
            self.datasets.append({"path": path, "variable": "df"})
            self.history.append(
                {"role": "user", "content": "<dataset variable='df'>\nrows\n</dataset>"}
            )

        def close(self):
            self.closed = True

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("CRIVO_PROVIDER", raising=False)
    monkeypatch.setattr(mcp_server, "_make_session", ChattySession)

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        opened = mcp_server._open_data("data/tiny.csv")

    assert "session_id" in opened, opened
    assert captured.getvalue() == "", (
        f"session chatter leaked to the protocol channel: {captured.getvalue()!r}"
    )


def test_clean_file_refuses_a_name_that_is_not_a_variable_name(monkeypatch) -> None:
    """The client's `name` is interpolated into loop.LOAD_TEMPLATE unescaped,
    so a client that can name a frame can run any code it likes in the
    server's kernel. Refuse it here, before a kernel exists."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    fake = FakeSession()
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)

    injected = "df\nimport os; os.system('id > /tmp/crivo_pwned')\ndf2"
    refused = mcp_server._clean_file("data/x.csv", name=injected)

    assert "not a variable name" in refused["error"]
    assert fake.datasets == [], "the injected name must never reach a kernel"
    assert (
        "Python keyword" in mcp_server._clean_file("data/x.csv", name="class")["error"]
    )

    ok = mcp_server._clean_file("data/x.csv", name="beers")
    assert "error" not in ok and ok["variable"] == "beers"


def test_ask_declines_person_grade_gates_and_reports_them(monkeypatch) -> None:
    """A GATE or HUMAN grade is a person's decision, and skill admission is
    hard-coded HUMAN. The calling MCP client is not that person, so those
    gates must be skipped and named back to the caller, never answered on the
    person's behalf; the analyst's own query cell still runs."""
    from crivo.card import AnswerCard
    from crivo.events import CardReady, GateRequest

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    card = AnswerCard(card_id="c1", session="s1", question="q", answer="a")
    fake = FakeSession(
        script=[
            GateRequest("rows = len(df)", 1),  # QUERY: grade is empty
            GateRequest("admit", 1, title="admit skill money_to_float", grade="HUMAN"),
            GateRequest("fix", 1, title="merge variants", grade="GATE"),
            CardReady(card),
        ]
    )
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)
    opened = mcp_server._open_data("data/tiny.csv")

    result = mcp_server._ask(opened["session_id"], "how many rows?")

    assert [d.action for d in fake.sent if d is not None] == ["run", "skip", "skip"]
    assert result["answer"] == "a"
    assert result["unresolved_gates"] == [
        "HUMAN: admit skill money_to_float",
        "GATE: merge variants",
    ], "the caller must learn a person-grade decision was needed and skipped"
    mcp_server.close_all()


def test_file_tools_refuse_paths_outside_the_configured_root(
    tmp_path, monkeypatch
) -> None:
    """diagnose_file is documented read-only and always safe to call first;
    unconfined it reads any file the server process can read. A symlink inside
    the root pointing out of it is the same escape, so containment is tested
    after resolving both sides."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    root = tmp_path / "served"
    (root / "sub").mkdir(parents=True)
    inside = root / "sub" / "ok.csv"
    pd.DataFrame({"a": [1, 2]}).to_csv(inside, index=False)
    secret = tmp_path / "secret.csv"
    pd.DataFrame({"a": [1]}).to_csv(secret, index=False)
    escape = root / "escape.csv"
    escape.symlink_to(secret)
    monkeypatch.setenv("CRIVO_MCP_ROOT", str(root))
    fake = FakeSession()
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)

    assert "findings" in json.loads(mcp_server._diagnose_file(str(inside)))

    for path in (str(secret), str(escape), str(root / ".." / "secret.csv")):
        assert "outside" in json.loads(mcp_server._diagnose_file(path))["error"], path
        assert "outside" in mcp_server._open_data(path)["error"], path
        assert "outside" in mcp_server._clean_file(path)["error"], path
    assert fake.datasets == [], "no refused path may reach a kernel"


def test_the_sandbox_choice_is_explicit_and_warns_when_it_is_off(
    monkeypatch, capsys
) -> None:
    """loop.Session defaults to docker=False, so a factory that passes no
    docker argument runs model-authored code in a host subprocess with this
    user's files and network while the tools promise a sandbox. The choice
    must be made explicitly, and the unsandboxed default must say so aloud."""
    import crivo.loop

    seen: list[dict] = []

    class Recorder:
        def __init__(self, **kwargs):
            seen.append(kwargs)

    monkeypatch.setattr(crivo.loop, "Session", Recorder)
    monkeypatch.delenv("CRIVO_MCP_SANDBOX", raising=False)

    mcp_server._make_session()
    assert seen[0]["docker"] is False
    warning = capsys.readouterr().err
    assert "CRIVO_MCP_SANDBOX" in warning and "UNSANDBOXED" in warning.upper()

    monkeypatch.setenv("CRIVO_MCP_SANDBOX", "docker")
    mcp_server._make_session()
    assert seen[1]["docker"] is True
    assert "UNSANDBOXED" not in capsys.readouterr().err.upper()


def test_no_tool_docstring_promises_a_sandbox_the_server_may_not_have() -> None:
    """The docstrings are the contract a calling model acts on. While the
    host subprocess is still the default, none of them may claim the kernel is
    sandboxed, and the two that run model-authored code must name the env var
    that decides it."""
    import asyncio

    app = mcp_server.build_server()
    tools = {t.name: t for t in asyncio.run(app.list_tools())}

    for name in ("clean_file", "open_data"):
        assert "sandboxed kernel" not in tools[name].description
        assert "CRIVO_MCP_SANDBOX" in tools[name].description


def test_the_file_tools_use_the_path_they_checked(tmp_path, monkeypatch) -> None:
    """The check resolved the path and then handed the client's ORIGINAL
    string downstream, so a symlink that pointed inside the root when it was
    checked could point outside it by the time pandas opened it. That race was
    won 257 times in 8 seconds; passing the resolved path closes it."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("CRIVO_MCP_ROOT", str(tmp_path))
    real = tmp_path / "real.csv"
    pd.DataFrame({"a": [1]}).to_csv(real, index=False)
    link = tmp_path / "link.csv"
    link.symlink_to(real)
    fake = FakeSession()
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)

    mcp_server._open_data(str(link))

    assert fake.datasets[-1]["path"] == str(real.resolve()), (
        "what was checked and what is opened have to be the same path"
    )


def test_a_root_that_holds_everything_is_refused_not_served(monkeypatch) -> None:
    """MCP stdio clients commonly launch a server with cwd="/", and the root
    fell back to cwd — so the deployment that most needs confining got none of
    it. A root of / or of the home directory is no root at all."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    for root in ("/", str(pathlib.Path.home())):
        monkeypatch.setenv("CRIVO_MCP_ROOT", root)
        refused = json.loads(mcp_server._diagnose_file("/etc/passwd"))["error"]
        assert "CRIVO_MCP_ROOT" in refused and "no usable root" in refused, root


def test_a_refusal_never_echoes_the_configured_root(tmp_path, monkeypatch) -> None:
    """Naming the env var helps the operator; naming its value tells an
    unauthenticated client the server's directory layout."""
    root = tmp_path / "clients" / "acme" / "private"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRIVO_MCP_ROOT", str(root))

    refused = json.loads(mcp_server._diagnose_file("/etc/passwd"))["error"]

    assert "CRIVO_MCP_ROOT" in refused
    assert str(root) not in refused and "acme" not in refused


def test_a_device_file_inside_the_root_is_refused(tmp_path, monkeypatch) -> None:
    """A FIFO or a character device inside the root is not a data file:
    reading one never returns, and this server has no other thread to notice
    that the client has hung it."""
    import os

    monkeypatch.setenv("CRIVO_MCP_ROOT", str(tmp_path))
    fifo = tmp_path / "pipe.csv"
    os.mkfifo(fifo)

    refused = json.loads(mcp_server._diagnose_file(str(fifo)))["error"]

    assert "not a regular file" in refused


def test_an_ordinary_variable_name_is_not_refused(monkeypatch) -> None:
    """The injection check was written as a regex stricter than Python, so
    _scratch, café and any name over 63 characters were refused although they
    are valid identifiers that worked before the check existed."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    fake = FakeSession()
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)

    for name in ("_scratch", "_raw", "café", "df2"):
        assert mcp_server._name_error(name) is None, name
        assert "error" not in mcp_server._clean_file("data/x.csv", name=name), name
    # and the injection it exists for is still refused
    assert "not a variable name" in mcp_server._name_error("df\nimport os")
    assert "longer than" in mcp_server._name_error("d" * 65)


def test_an_untitled_person_grade_gate_is_still_reported(monkeypatch) -> None:
    """The case that most needs surfacing: an untitled gate over an empty cell
    raised IndexError off code.splitlines()[0], so _ask returned a generic
    error, sent no decision, and reported no unresolved gate at all."""
    from crivo.events import GateRequest

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    fake = FakeSession(script=[GateRequest("", 1, grade="HUMAN")])
    monkeypatch.setattr(mcp_server, "_make_session", lambda: fake)
    opened = mcp_server._open_data("data/tiny.csv")

    result = mcp_server._ask(opened["session_id"], "how many rows?")

    assert [d.action for d in fake.sent if d is not None] == ["skip"]
    assert result["unresolved_gates"] == ["HUMAN: "]
    mcp_server.close_all()


def test_an_unknown_sandbox_value_is_an_error_not_the_weaker_default(
    monkeypatch,
) -> None:
    """CRIVO_MCP_SANDBOX=dokcer used to run unsandboxed with a stderr line the
    client may never show anyone. A sandbox the server does not recognize is
    not a sandbox, and silently downgrading is the wrong direction to fail."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setenv("CRIVO_MCP_SANDBOX", "dokcer")

    assert "CRIVO_MCP_SANDBOX" in mcp_server._clean_file("data/x.csv")["error"]
    assert "CRIVO_MCP_SANDBOX" in mcp_server._open_data("data/x.csv")["error"]


def test_no_tool_docstring_still_sells_policy_all_as_human_consent() -> None:
    """repl.policy_decision now ignores `policy` entirely, so a description
    telling the calling model to obtain and relay human consent through it is
    the same class of false promise as the sandbox claim that was removed. ask
    must also say plainly what calling it executes."""
    import asyncio

    app = mcp_server.build_server()
    tools = {t.name: t for t in asyncio.run(app.list_tools())}

    clean = tools["clean_file"].description
    assert "approves judgement-grade changes unattended" not in clean
    assert "human consent relayed by you" not in clean
    assert "no value of policy changes that" in clean
    assert "model-authored code" in tools["ask"].description
