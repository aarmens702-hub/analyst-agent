"""`crivo.ask(data, question)` — one verified answer over the query loop.

A public wrapper on the same seam the MCP server drives (`mcp_server._ask`):
construct a session, load the data, run one turn, auto-approve gates — the
caller is the operator, the `--auto-run` trust position; the sandbox and the
card's executed checks still stand — and return the finished answer card.
Wrapper only: the loop's trust and gate semantics are untouched.
"""

import os


def _required_key() -> str | None:
    """The env var the configured provider needs, or None when it is set."""
    provider = os.environ.get("CRIVO_PROVIDER", "deepseek")
    key = "ANTHROPIC_API_KEY" if provider == "claude" else "DEEPSEEK_API_KEY"
    if not os.environ.get(key):
        return key
    return None


def _make_session():
    """Session factory, module-level so tests can swap in a double."""
    from crivo.loop import Session

    return Session(
        workspace=os.environ.get("CRIVO_WORKSPACE", "workspace"),
        preview=False,  # one-shot: nobody reads a gate preview
    )


class Answer:
    """One answered question: the card's claims plus the loop's notices."""

    def __init__(self, card, notices):
        self.card = card
        self.notices = notices

    @property
    def text(self) -> str:
        return self.card.answer

    @property
    def checks(self) -> list:
        return self.card.checks

    @property
    def lineage(self) -> dict:
        return self.card.lineage

    def __repr__(self) -> str:
        return self.card.to_markdown()

    def _repr_html_(self) -> str:
        from crivo import notebook

        return notebook.answer_html(self)

    @property
    def code(self) -> str:
        """The code that produced the answer: the last cell run to status ok,
        the same rule the card's own renderer uses."""
        cell = self.card._final_ok_cell()
        return cell["code"] if cell else ""


def _materialize(frame, name: str | None) -> str:
    """Write the frame to a parquet snapshot the kernel can load; the file is
    kept — lineage points at it, and deleted receipts are no receipts."""
    import uuid
    from pathlib import Path

    out = Path(os.environ.get("CRIVO_WORKSPACE", "workspace")) / "ask"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name or 'data'}-{uuid.uuid4().hex[:8]}.parquet"
    frame.to_parquet(path)
    return str(path)


def ask(data, question: str, *, name: str | None = None) -> Answer:
    """Answer one question over `data` (a DataFrame or a path) with receipts."""
    missing = _required_key()
    if missing:
        raise RuntimeError(
            f"{missing} is not set — the agent half needs a model key "
            f"(the keyless half, crivo.diagnose/crivo.clean, does not)"
        )
    import pandas as pd

    from crivo.events import CardReady, GateDecision, GateRequest, Notice

    path = _materialize(data, name) if isinstance(data, pd.DataFrame) else str(data)
    session = _make_session()
    notices: list[str] = []
    try:
        session.load(path, name)
        turn = session.run_turn(question)
        event = next(turn)
        while True:
            reply = None
            if isinstance(event, GateRequest):
                reply = GateDecision("run")
            elif isinstance(event, CardReady):
                turn.close()
                return Answer(event.card, notices)
            elif isinstance(event, Notice):
                notices.append(f"{event.kind}: {event.text}")
            event = turn.send(reply)
    except StopIteration:
        pass
    finally:
        session.close()
    raise RuntimeError(f"the turn produced no answer card (notices: {notices})")
