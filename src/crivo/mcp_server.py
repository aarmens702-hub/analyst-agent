"""crivo as an MCP server (v1 wrapper): a driver, like repl.py.

Six tools over stdio so any MCP client — Claude Desktop, Claude Code, Cursor —
can drive the agent natively. The architecture ruling from the spec: this
wraps the whole agent (the inner model still writes fixes); exposing the
verification primitives to the *outer* model is v2.

Logic lives in plain module functions so the suite tests them against
SessionLike doubles; `build_server()` imports the mcp SDK lazily, so importing
crivo never requires it (AC4). Every tool returns errors as results —
one bad file must not kill the server (R6).
"""

import contextlib
import json
import sys

from crivo import checkup


def _quiet():
    """Route Session's own chatter (starting kernel, loaded ..., the profile)
    to stderr. In stdio MCP mode stdout is the JSON-RPC channel, and a strict
    client parses any stray line as a corrupt message — observed live as
    'Invalid JSON: starting kernel'. The CLI does this for the whole process;
    the tool paths must do it around every Session-touching region."""
    return contextlib.redirect_stdout(sys.stderr)


def _diagnose_file(path: str) -> str:
    """The free report: no key, no kernel, read-only (R2)."""
    try:
        return checkup.report(path, as_json=True)
    except Exception as exc:  # noqa: BLE001 — R6: errors are results, not crashes
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


def _required_key() -> str | None:
    """R5: which env var the configured provider needs, or None when set."""
    import os

    provider = os.environ.get("CRIVO_PROVIDER", "deepseek")
    key = "ANTHROPIC_API_KEY" if provider == "claude" else "DEEPSEEK_API_KEY"
    if not os.environ.get(key):
        return f"{key} is not set — add it to the MCP client config env"
    return None


def _make_session():
    """Session factory, module-level so tests can swap in a double."""
    import os

    from crivo.loop import Session

    return Session(
        workspace=os.environ.get("CRIVO_WORKSPACE", "workspace"),
        preview=False,  # headless: nobody reads a gate preview here (R7)
    )


def _clean_file(
    path: str, name: str | None = None, policy: str = "auto", decide=None
) -> dict:
    """Headless one-shot clean under the grade policy (R2); `decide` is the
    v1.5 per-gate callback when the client can be asked (elicitation)."""
    missing = _required_key()
    if missing:
        return {"error": missing}
    try:
        from crivo import repl

        with _quiet():  # construction and load() print to stdout, the RPC wire
            session = _make_session()
        try:
            with _quiet():
                summary = repl.run_clean_once(
                    session, path, name=name, policy=policy, decide=decide
                )
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

        with _quiet():  # construction and load() print to stdout, the RPC wire
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

    from crivo.events import CardReady, GateDecision, GateRequest, Notice

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
        from crivo import provenance

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
    here, not at module top: importing crivo must never require the
    mcp package (AC4). Each docstring below is the contract a calling model
    acts on — they are instructions, not comments."""
    from mcp.server.mcpserver import Context, MCPServer

    app = MCPServer("crivo")

    @app.tool()
    def diagnose_file(path: str) -> str:
        """Report what is wrong with a CSV/parquet file: 22 named data-quality
        checks (money as text, mixed date formats, sentinels, encoding damage,
        duplicates, contradictions). Returns JSON with findings, checks that
        ran clean, and checks that could not run. Free, keyless, read-only —
        always safe to call first."""
        return _diagnose_file(path)

    @app.tool()
    async def clean_file(
        path: str,
        name: str | None = None,
        policy: str = "auto",
        ctx: Context | None = None,
    ) -> dict:
        """Clean a data file headlessly. Fixes are model-written, executed in
        a sandboxed kernel, and verified by re-running the detector plus row
        and hash invariants; failures revert. Under policy="auto" only
        AUTO-grade fixes run — judgement-grade findings come back in
        needs_human for a person to decide; if your client supports
        elicitation, GATE-grade fixes are offered to the human one by one
        instead. policy="all" approves judgement-grade changes unattended and
        requires explicit human consent relayed by you, the calling agent; do
        not pass it on your own initiative. Returns fixes, needs_human, and
        artifact paths (cleaned parquet, lineage, report)."""
        import anyio

        decide = None
        if policy == "auto" and ctx is not None and _client_can_elicit(ctx):
            decide = _make_decider(_elicit_sync_via(ctx))
        # the kernel blocks for minutes; a worker thread keeps the event loop
        # free to carry the elicitation replies the decider waits on
        return await anyio.to_thread.run_sync(
            lambda: _clean_file(path, name=name, policy=policy, decide=decide)
        )

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


def _make_decider(elicit_sync):
    """v1.5: per-gate decisions through the client's human (R3 of the wrapper
    spec's future notes). Only GATE grades are asked — AUTO needs nobody and
    HUMAN findings and skill admissions need a richer conversation than a
    yes/no popup, so they stay deferred. Decline, cancel, and elicitation
    failure all skip with the reason in the note: a popup that errored is not
    consent."""
    from crivo.events import GateDecision

    def decide(event):
        if event.grade != "GATE":
            return (
                GateDecision("run") if event.grade == "AUTO" else GateDecision("skip")
            )
        message = f"Approve this fix? {event.title}"
        if event.preview:
            message += f"\n\n{event.preview}"
        try:
            result = elicit_sync(message)
        except Exception as exc:  # noqa: BLE001 — R6: failure is a skip, not a crash
            return GateDecision(
                "skip", f"elicitation unavailable: {type(exc).__name__}"
            )
        if getattr(result, "action", "") == "accept" and getattr(
            getattr(result, "data", None), "approve", False
        ):
            return GateDecision("run")
        return GateDecision("skip", "declined via elicitation")

    return decide


def _client_can_elicit(ctx) -> bool:
    """True only when the connected client declared the elicitation
    capability. Fails closed: no capability, no context, or a check that
    itself errors all mean "cannot ask" — which degrades to exactly the v1
    blanket-policy behavior, never to a crash."""
    try:
        from mcp_types import ClientCapabilities, ElicitationCapability

        session = ctx.request_context.session
        return bool(
            session.check_client_capability(
                ClientCapabilities(elicitation=ElicitationCapability())
            )
        )
    except Exception:  # noqa: BLE001 — fail closed by design
        return False


def _approve_schema():
    """The one-field yes/no schema an elicitation popup shows. Built lazily:
    pydantic is a hard dependency but the schema is only needed when a
    client can actually be asked."""
    from pydantic import BaseModel

    class ApproveFix(BaseModel):
        approve: bool

    return ApproveFix


def _elicit_sync_via(ctx):
    """Bridge a worker-thread gate decision to the async client ask. The
    clean runs in a worker thread (kernel cells block for minutes and must
    not starve the event loop that carries the elicitation reply), so the
    decider hops back to the loop per question. Only a live client proves
    this bridge end to end."""

    def elicit_sync(message: str):
        import anyio.from_thread

        return anyio.from_thread.run(_do_elicit, ctx, message)

    return elicit_sync


async def _do_elicit(ctx, message: str):
    return await ctx.elicit(message, _approve_schema())
