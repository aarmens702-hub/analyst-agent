"""MCP wrapper tests (specs/2026-08-14-mcp-wrapper-design.md).

The server is a driver, like repl.py: logic lives in plain module functions
tested here against SessionLike doubles, with the FastMCP registration a thin
layer over them. One real-kernel test (AC2) proves the session tools against
the actual subprocess kernel; everything else uses doubles, in the style of
tests/test_repl.py.
"""

import json

import pandas as pd

from analyst_agent import mcp_server


def test_diagnose_file_is_keyless_and_returns_json(tmp_path, monkeypatch) -> None:
    """The try-before-trust property must survive the wrapper: a stranger's
    agent can diagnose with no API key configured at all."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
    monkeypatch.delenv("ANALYST_PROVIDER", raising=False)
    assert "DEEPSEEK_API_KEY" in mcp_server._clean_file("x.csv")["error"]

    monkeypatch.setenv("ANALYST_PROVIDER", "claude")
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
    from analyst_agent.events import GateRequest

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("ANALYST_PROVIDER", raising=False)
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


def test_ask_auto_approves_gates_and_returns_the_card(monkeypatch) -> None:
    """AC1: the session tools. open_data registers a persistent session and
    hands back its profile; ask drives run_turn with the calling agent as the
    operator (gates auto-approved, the --auto-run trust position) and returns
    the card as a dict with its executed checks."""
    from analyst_agent.card import AnswerCard
    from analyst_agent.events import CardReady, GateRequest

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("ANALYST_PROVIDER", raising=False)
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
    monkeypatch.delenv("ANALYST_PROVIDER", raising=False)

    result = mcp_server._ask("nope1234", "anything")

    assert "unknown session" in result["error"]
    assert "open_data" in result["error"], "the fix must be named, not implied"


def test_idle_sessions_are_evicted_and_their_kernels_closed(monkeypatch) -> None:
    """R4: an abandoned kernel is a leaked process. Eviction happens on the
    next lookup, and the evicted session's kernel is actually closed."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("ANALYST_PROVIDER", raising=False)
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
    monkeypatch.delenv("ANALYST_PROVIDER", raising=False)
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
    monkeypatch.delenv("ANALYST_PROVIDER", raising=False)

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

    from analyst_agent.events import GateRequest

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

    monkeypatch.setattr("analyst_agent.repl.run_clean_once", fake_run)
    monkeypatch.setattr(
        mcp_server, "_make_session", lambda: SimpleNamespace(close=lambda: None)
    )
    sentinel = object()

    summary = mcp_server._clean_file("x.csv", decide=sentinel)

    assert summary["file"] == "x.csv"
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
