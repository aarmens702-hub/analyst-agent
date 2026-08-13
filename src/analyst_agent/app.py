"""Streamlit UI shell: a second driver over the SessionLike generator
protocol (P3; CLAUDE.md "Claude owns ... the Streamlit shell"; spec
specs/2026-08-09-p0-core-design.md R5 — "the P3 Streamlit app is a second
driver over the same loop").

repl.py drives run_turn()/clean() by blocking on input() at each gate.
Streamlit reruns the whole script top-to-bottom on every widget interaction
and can never block, so this driver holds the live generator in
st.session_state, pumps it with pump() until it suspends at a GateRequest
or ends, and accumulates render instructions in a session-state transcript
so reruns repaint history instead of losing it. A pending gate halts
pumping and shows Run / Reject / Skip controls; the click builds a
GateDecision and pump() resumes the generator via gen.send() on the next
rerun.

Everything above the "Streamlit glue" divider is plain functions with no
st.* calls, unit-tested without a running server in test_app.py (see that
file, and tests/test_repl.py for the sibling driver's tests). Below the
divider is a thin rendering layer that trusts those functions completely.
"""

import os
import re
from collections.abc import Generator
from pathlib import Path

from dotenv import load_dotenv

from analyst_agent.events import (
    ArtifactSaved,
    CardReady,
    Event,
    GateDecision,
    GateRequest,
    Notice,
    SessionLike,
    StreamText,
)
from analyst_agent.loop import Session

BANNER = "analyst-agent"

# -- pure functions (tested in test_app.py) ----------------------------------


def render_instruction(event: Event) -> dict:
    """Map one loop event to a UI-agnostic render instruction.

    One dict shape per Event subtype, discriminated by "kind". This is the
    seam that makes the driver testable without a Streamlit runtime: Event
    -> dict has no st.* dependency, unlike repl._drive's print-as-you-go.
    """
    if isinstance(event, StreamText):
        return {"kind": "stream", "name": event.name, "text": event.text}
    if isinstance(event, GateRequest):
        return {
            "kind": "gate",
            "code": event.code,
            "iteration": event.iteration,
            "title": event.title,
        }
    if isinstance(event, ArtifactSaved):
        return {"kind": "artifact", "path": event.path}
    if isinstance(event, Notice):
        return {"kind": "notice", "notice_kind": event.kind, "text": event.text}
    if isinstance(event, CardReady):
        return {"kind": "card", "markdown": event.card.to_markdown()}
    raise TypeError(f"unrenderable event: {event!r}")


def pump(
    gen: Generator[Event, GateDecision | None, None],
    decision: GateDecision | None = None,
) -> tuple[list[dict], GateRequest | None]:
    """Advance a run_turn()/clean() generator to its next suspend point.

    Renders every event it drains via gen.send(), same as repl._drive, but
    where _drive can block on input() at a GateRequest, this must not: the
    first GateRequest it meets is returned as `pending`, unanswered, so the
    Streamlit driver can stop and render Run/Reject/Skip controls instead.
    Call again with `decision` set to resume the suspended generator.
    `decision=None` also covers turn start — gen.send(None) on a fresh
    generator is exactly next(gen).

    Returns (render_instructions_in_order, pending_gate_or_None). A card
    ends the turn the same as exhaustion: both come back with pending=None.
    """
    instructions: list[dict] = []
    try:
        event = gen.send(decision)
        while True:
            instructions.append(render_instruction(event))
            if isinstance(event, GateRequest):
                return instructions, event
            if isinstance(event, CardReady):
                return instructions, None
            event = gen.send(None)
    except StopIteration:
        return instructions, None


def notice_level(kind: str) -> str:
    """Best-effort st.error/warning/info bucket for a Notice.kind.

    Not part of the wire contract (repl.py renders every kind identically
    as "· {kind}: {text}") — a small heuristic allowlist for the kinds that
    warrant an elevated color in a browser; anything else defaults to info.
    """
    if kind in ("llm_error", "kernel_died", "error"):
        return "error"
    if kind in ("cap", "restart_offer"):
        return "warning"
    return "info"


def default_var_name(path: str) -> str:
    """Best-effort default for the sidebar loader's name field.

    Mirrors the regex Session.load() (loop.py) uses when name is omitted,
    purely to pre-fill the UI before the kernel round-trip that would
    otherwise be the only source of the real name. The resolved value is
    always passed to session.load() explicitly, so drift here is cosmetic.
    """
    stem = re.sub(r"\W+", "_", Path(path).stem).strip("_")
    return stem or "df"


# -- Streamlit glue (no unit tests; see module docstring) --------------------

import streamlit as st


def _get_session() -> SessionLike:
    """Construct the session once per browser session and cache it.

    Session() boots a kernel subprocess synchronously in __init__; doing
    that on every rerun (Streamlit's default execution model) instead of
    once would restart the kernel on every click.
    """
    if st.session_state.get("session") is None:
        st.session_state.session = Session(
            workspace=os.environ.get("ANALYST_AGENT_WORKSPACE", "workspace"),
            data_dir=os.environ.get("ANALYST_AGENT_DATA_DIR", "data"),
        )
    return st.session_state.session


def _init_state() -> None:
    st.session_state.setdefault("session", None)
    st.session_state.setdefault("transcript", [])
    st.session_state.setdefault("loaded_vars", [])
    st.session_state.setdefault("current_gen", None)
    st.session_state.setdefault("pending_gate", None)


def _advance(
    gen: Generator[Event, GateDecision | None, None],
    decision: GateDecision | None = None,
) -> None:
    """Pump gen, fold the results into session state, force a repaint.

    Every path that changes what belongs on screen ends in st.rerun():
    widgets earlier in this same top-to-bottom pass already rendered with
    the old state and won't see the update otherwise.
    """
    instructions, pending = pump(gen, decision)
    st.session_state.transcript.extend(instructions)
    st.session_state.pending_gate = pending
    st.session_state.current_gen = gen if pending is not None else None
    st.rerun()


def _render_transcript() -> None:
    for item in st.session_state.transcript:
        kind = item["kind"]
        if kind == "question":
            with st.chat_message("user"):
                st.markdown(item["text"])
        elif kind == "stream":
            with st.chat_message("assistant"):
                st.markdown(item["text"])
        elif kind == "gate":
            with st.chat_message("assistant"):
                if item["title"]:
                    st.caption(item["title"])
                st.code(item["code"], language="python")
        elif kind == "artifact":
            st.image(item["path"], caption=Path(item["path"]).name)
        elif kind == "notice":
            text = f"{item['notice_kind']}: {item['text']}"
            level = notice_level(item["notice_kind"])
            if level == "error":
                st.error(text)
            elif level == "warning":
                st.warning(text)
            else:
                st.info(text)
        elif kind == "card":
            with st.chat_message("assistant"):
                st.markdown(item["markdown"])


def _render_gate_controls() -> None:
    if st.session_state.pending_gate is None:
        return
    # Keyed by transcript length so each new gate gets a fresh, empty note
    # field — Streamlit forbids resetting a widget's own session_state key
    # in place, and the transcript always grows by at least one "gate" item
    # (this one) between successive gates.
    note_key = f"reject_note_{len(st.session_state.transcript)}"
    note = st.text_input("steering note (used only if you reject)", key=note_key)
    run_col, reject_col, skip_col = st.columns(3)
    if run_col.button("Run", type="primary"):
        _advance(st.session_state.current_gen, GateDecision("run"))
    if reject_col.button("Reject"):
        _advance(st.session_state.current_gen, GateDecision("reject", note))
    if skip_col.button("Skip"):
        _advance(st.session_state.current_gen, GateDecision("skip"))


def _render_sidebar() -> None:
    session = _get_session()
    busy = st.session_state.current_gen is not None
    with st.sidebar:
        st.header(BANNER)

        st.subheader("Load a dataset")
        path = st.text_input("path", placeholder="data/property-tax-report.csv")
        name = st.text_input("variable name (optional)")
        if st.button("Load", disabled=busy or not path):
            resolved = name.strip() or default_var_name(path)
            session.load(path, resolved)
            if resolved not in dict(st.session_state.loaded_vars):
                st.session_state.loaded_vars.append((resolved, path))
            st.rerun()

        st.subheader("Loaded variables")
        if not st.session_state.loaded_vars:
            st.caption("none yet")
        for var_name, var_path in st.session_state.loaded_vars:
            label_col, button_col = st.columns([2, 1])
            label_col.markdown(f"**{var_name}**  \n`{var_path}`")
            if button_col.button("/clean", key=f"clean_{var_name}", disabled=busy):
                st.session_state.transcript.append(
                    {"kind": "question", "text": f"/clean {var_name}"}
                )
                _advance(session.clean(var_name))

        st.divider()
        if st.button("End session", disabled=busy):
            session.close()
            st.session_state.session = None
            st.session_state.loaded_vars = []
            st.rerun()


def main() -> None:
    load_dotenv()
    st.set_page_config(page_title=BANNER, layout="wide")
    _init_state()

    if not os.environ.get("DEEPSEEK_API_KEY"):
        st.error("DEEPSEEK_API_KEY is not set — put it in .env or export it.")
        st.stop()

    _render_sidebar()

    st.title(BANNER)
    _render_transcript()
    _render_gate_controls()

    busy = st.session_state.current_gen is not None
    question = st.chat_input("Ask a question about a loaded dataset", disabled=busy)
    if question:
        st.session_state.transcript.append({"kind": "question", "text": question})
        _advance(_get_session().run_turn(question))


if __name__ == "__main__":
    main()
