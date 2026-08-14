"""analyst-agent as an MCP server (v1 wrapper): a driver, like repl.py.

Six tools over stdio so any MCP client — Claude Desktop, Claude Code, Cursor —
can drive the agent natively. The architecture ruling from the spec: this
wraps the whole agent (the inner model still writes fixes); exposing the
verification primitives to the *outer* model is v2.

Logic lives in plain module functions so the suite tests them against
SessionLike doubles; `build_server()` imports the mcp SDK lazily, so importing
analyst_agent never requires it (AC4). Every tool returns errors as results —
one bad file must not kill the server (R6).
"""

import json

from analyst_agent import diagnose


def _diagnose_file(path: str) -> str:
    """The free report: no key, no kernel, read-only (R2)."""
    try:
        return diagnose.report(path, as_json=True)
    except Exception as exc:  # noqa: BLE001 — R6: errors are results, not crashes
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


def _required_key() -> str | None:
    """R5: which env var the configured provider needs, or None when set."""
    import os

    provider = os.environ.get("ANALYST_PROVIDER", "deepseek")
    key = "ANTHROPIC_API_KEY" if provider == "claude" else "DEEPSEEK_API_KEY"
    if not os.environ.get(key):
        return f"{key} is not set — add it to the MCP client config env"
    return None


def _make_session():
    """Session factory, module-level so tests can swap in a double."""
    import os

    from analyst_agent.loop import Session

    return Session(
        workspace=os.environ.get("ANALYST_WORKSPACE", "workspace"),
        preview=False,  # headless: nobody reads a gate preview here (R7)
    )


def _clean_file(path: str, name: str | None = None, policy: str = "auto") -> dict:
    """Headless one-shot clean under the grade policy (R2)."""
    missing = _required_key()
    if missing:
        return {"error": missing}
    try:
        from analyst_agent.repl import run_clean_once

        session = _make_session()
        try:
            summary = run_clean_once(session, path, name=name, policy=policy)
            summary["policy"] = policy
            return summary
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 — R6: errors are results, not crashes
        return {"error": f"{type(exc).__name__}: {exc}"}


IDLE_S = 1800  # a session untouched this long is closed on the next lookup

# session_id -> {"session": Session, "last_used": monotonic seconds}
_SESSIONS: dict[str, dict] = {}


def _evict_idle() -> None:
    import time

    now = time.monotonic()
    for sid in list(_SESSIONS):
        if now - _SESSIONS[sid]["last_used"] > IDLE_S:
            entry = _SESSIONS.pop(sid)
            try:
                entry["session"].close()
            except Exception:  # noqa: BLE001, S110 — eviction is best-effort
                pass


def _lookup(session_id: str):
    import time

    _evict_idle()
    entry = _SESSIONS.get(session_id)
    if entry is None:
        return None
    entry["last_used"] = time.monotonic()
    return entry["session"]


def _open_data(path: str) -> dict:
    """Load a file into a persistent kernel session (R3): a conversation over
    one dataset must not reload it per question."""
    missing = _required_key()
    if missing:
        return {"error": missing}
    try:
        import time
        import uuid

        session = _make_session()
        session.load(path)
        datasets = getattr(session, "datasets", None) or []
        if not datasets:
            session.close()
            return {"error": f"could not load {path}"}
        history = getattr(session, "history", None) or []
        profile = history[-1]["content"] if history else ""
        _evict_idle()
        session_id = uuid.uuid4().hex[:8]
        _SESSIONS[session_id] = {"session": session, "last_used": time.monotonic()}
        return {
            "session_id": session_id,
            "variable": datasets[-1]["variable"],
            "profile": profile,
        }
    except Exception as exc:  # noqa: BLE001 — R6
        return {"error": f"{type(exc).__name__}: {exc}"}


def _ask(session_id: str, question: str) -> dict:
    """One question over an open session (R3). Gates are auto-approved: the
    calling agent is the operator — the --auto-run trust position; the
    sandbox and the card's executed checks still stand."""
    missing = _required_key()
    if missing:
        return {"error": missing}
    session = _lookup(session_id)
    if session is None:
        return {
            "error": f"unknown session {session_id!r} — call open_data first "
            f"(sessions idle over {IDLE_S // 60} minutes are closed)"
        }
    import dataclasses

    from analyst_agent.events import CardReady, GateDecision, GateRequest, Notice

    notices: list[str] = []
    try:
        turn = session.run_turn(question)
        event = next(turn)
        while True:
            answer = None
            if isinstance(event, GateRequest):
                answer = GateDecision("run")
            elif isinstance(event, CardReady):
                turn.close()
                return dataclasses.asdict(event.card)
            elif isinstance(event, Notice):
                notices.append(f"{event.kind}: {event.text}")
            event = turn.send(answer)
    except StopIteration:
        pass
    except Exception as exc:  # noqa: BLE001 — R6
        return {"error": f"{type(exc).__name__}: {exc}", "notices": notices}
    return {"error": "the turn produced no answer card", "notices": notices}


def close_all() -> None:
    """Shut every open session; called on server shutdown."""
    for sid in list(_SESSIONS):
        entry = _SESSIONS.pop(sid)
        try:
            entry["session"].close()
        except Exception:  # noqa: BLE001, S110 — shutdown is best-effort
            pass


def _why(session_id: str) -> str:
    """The provenance chain for an open session (R3), built from disk."""
    session = _lookup(session_id)
    if session is None:
        return f"unknown session {session_id!r} — call open_data first"
    try:
        from analyst_agent import provenance

        dag = provenance.build(session.session_dir)
        return provenance.to_markdown(dag, None)
    except Exception as exc:  # noqa: BLE001 — R6
        return f"error: {type(exc).__name__}: {exc}"


def _close_session(session_id: str) -> bool:
    """Shut one session's kernel (R3). True when it existed."""
    entry = _SESSIONS.pop(session_id, None)
    if entry is None:
        return False
    try:
        entry["session"].close()
    except Exception:  # noqa: BLE001, S110 — closing is best-effort
        pass
    return True


def build_server():
    """The MCP layer over the plain functions above. The SDK import lives
    here, not at module top: importing analyst_agent must never require the
    mcp package (AC4). Each docstring below is the contract a calling model
    acts on — they are instructions, not comments."""
    from mcp.server.mcpserver import MCPServer

    app = MCPServer("analyst-agent")

    @app.tool()
    def diagnose_file(path: str) -> str:
        """Report what is wrong with a CSV/parquet file: 22 named data-quality
        checks (money as text, mixed date formats, sentinels, encoding damage,
        duplicates, contradictions). Returns JSON with findings, checks that
        ran clean, and checks that could not run. Free, keyless, read-only —
        always safe to call first."""
        return _diagnose_file(path)

    @app.tool()
    def clean_file(path: str, name: str | None = None, policy: str = "auto") -> dict:
        """Clean a data file headlessly. Fixes are model-written, executed in
        a sandboxed kernel, and verified by re-running the detector plus row
        and hash invariants; failures revert. Under policy="auto" only
        AUTO-grade fixes run — judgement-grade findings come back in
        needs_human for a person to decide. policy="all" approves
        judgement-grade changes unattended and requires explicit human
        consent relayed by you, the calling agent; do not pass it on your own
        initiative. Returns fixes, needs_human, and artifact paths (cleaned
        parquet, lineage, report)."""
        return _clean_file(path, name=name, policy=policy)

    @app.tool()
    def open_data(path: str) -> dict:
        """Load a data file into a persistent sandboxed kernel for
        conversational analysis. Returns a session_id for ask/why/
        close_session and a schema/stats profile of the data. Use this when
        multiple questions will be asked over one file; sessions idle for 30
        minutes are closed automatically."""
        return _open_data(path)

    @app.tool()
    def ask(session_id: str, question: str) -> dict:
        """Ask a question over an open session. The inner analyst writes and
        runs code cells against the loaded data and returns an answer card:
        the answer, the code, executed checks, and lineage back to the source
        file. Trust the card's checks, not the prose alone."""
        return _ask(session_id, question)

    @app.tool()
    def why(session_id: str) -> str:
        """The provenance chain for an open session: which artifacts are
        trusted, meaning reachable from raw bytes with passing checks on
        every step, and which claims rest on them."""
        return _why(session_id)

    @app.tool()
    def close_session(session_id: str) -> bool:
        """Shut an open session's kernel. Returns True when the session
        existed. Call this when the analysis conversation is finished."""
        return _close_session(session_id)

    return app


def main() -> None:
    """Console entry: serve over stdio until the client hangs up."""
    app = build_server()
    try:
        app.run()
    finally:
        close_all()
