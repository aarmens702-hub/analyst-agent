"""`crivo.ask(data, question)` — the verified one-liner over the query loop
(Phase 3.2). A public wrapper on the same seam the MCP server drives: gates
auto-approved (the caller is the operator; the sandbox and the card's executed
checks still stand), judgement surfaced, the answer returned as an `Answer`
wrapping the card: text + code + checks + lineage."""

import pandas as pd
import pytest


def test_ask_without_a_key_raises_before_any_session_spins_up(monkeypatch):
    """No key -> a clear error naming the env var, and no kernel is started."""
    from crivo import query as ask_mod

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("CRIVO_PROVIDER", raising=False)
    spun = []
    monkeypatch.setattr(ask_mod, "_make_session", lambda: spun.append(1))

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        ask_mod.ask(pd.DataFrame({"a": [1]}), "how many rows?")

    assert not spun, "no session may be constructed without a key"


def test_answer_images_delegate_and_render_as_data_uris(tmp_path):
    """P3 §5 wrapper: Answer.images mirrors the card's aggregated chart
    paths, and the notebook card embeds existing files as self-contained
    data URIs (inline-only doctrine — the card must survive export);
    a missing file is skipped, never a crash, never a broken img tag."""
    import base64

    from crivo import notebook
    from crivo.card import AnswerCard
    from crivo.query import Answer

    png = tmp_path / "chart-001.png"
    png.write_bytes(b"png-bytes-for-embedding")
    card = AnswerCard(
        card_id="c1",
        session="s1",
        question="plot revenue?",
        answer="Done.",
        cells=[
            {
                "status": "ok",
                "code": "plt.plot(...)",
                "display_paths": [str(png), str(tmp_path / "gone.png")],
            }
        ],
    )
    answer = Answer(card, [])
    assert answer.images == [str(png), str(tmp_path / "gone.png")]

    html = notebook.answer_html(answer)
    expected = base64.b64encode(png.read_bytes()).decode()
    assert f"data:image/png;base64,{expected}" in html
    assert "gone.png" not in html


def test_ask_gate_matches_the_configured_provider(monkeypatch):
    """P3: the gate demands what the CONFIGURED provider actually needs —
    openai wants an endpoint and model (keys optional: local servers ignore
    them), faux wants nothing at all — instead of always naming DeepSeek."""
    from crivo import query as ask_mod

    spun = []
    monkeypatch.setattr(ask_mod, "_make_session", lambda: spun.append(1))

    monkeypatch.setenv("CRIVO_PROVIDER", "openai")
    with pytest.raises(RuntimeError, match="CRIVO_BASE_URL"):
        ask_mod.ask(pd.DataFrame({"a": [1]}), "how many rows?")
    monkeypatch.setenv("CRIVO_BASE_URL", "http://localhost:11434/v1")
    with pytest.raises(RuntimeError, match="CRIVO_MODEL"):
        ask_mod.ask(pd.DataFrame({"a": [1]}), "how many rows?")
    assert not spun, "no session may be constructed while the gate fails"

    monkeypatch.setenv("CRIVO_MODEL", "llama3.3")
    assert ask_mod._required_key() is None

    monkeypatch.setenv("CRIVO_PROVIDER", "faux")
    assert ask_mod._required_key() is None, "the faux provider needs no env"


class _FakeSession:
    """A Session double speaking the real event protocol: one gate, then the
    card. Records what ask() did with it."""

    def __init__(self, card):
        self._card = card
        self.loaded: list[tuple[str, str | None]] = []
        self.closed = False

    def load(self, path, name=None):
        self.loaded.append((str(path), name))

    def run_turn(self, question):
        from crivo.events import CardReady, GateRequest

        decision = yield GateRequest(code="len(df)", iteration=1)
        assert decision.action == "run", "ask() must auto-approve gates"
        yield CardReady(card=self._card)

    def close(self):
        self.closed = True


def _card(**over):
    from crivo.card import AnswerCard

    fields = {
        "card_id": "card-1",
        "session": "s",
        "question": "how many rows?",
        "answer": "20 rows",
        "cells": [{"event_id": 3, "gate": "run", "status": "ok", "code": "len(df)"}],
        "checks": [{"expr": "len(df) == 20", "passed": True}],
        "lineage": {"datasets": [], "event_chain": [1, 2, 3]},
    }
    fields.update(over)
    return AnswerCard(**fields)


def test_ask_runs_a_turn_over_a_dataframe_and_returns_the_answer(monkeypatch, tmp_path):
    """The whole promise: df in, Answer out — materialized to a real file the
    kernel loaded, gates auto-approved, card fields surfaced, session closed."""
    from crivo import query as ask_mod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("CRIVO_WORKSPACE", str(tmp_path))
    session = _FakeSession(_card())
    monkeypatch.setattr(ask_mod, "_make_session", lambda: session)

    answer = ask_mod.ask(pd.DataFrame({"a": range(20)}), "how many rows?")

    assert answer.text == "20 rows"
    assert answer.checks[0]["expr"] == "len(df) == 20"
    assert answer.lineage["event_chain"] == [1, 2, 3]
    (path, _name) = session.loaded[0]
    assert path.endswith(".parquet") and str(tmp_path) in path
    assert pd.read_parquet(path).shape == (20, 1), "the snapshot is real bytes"
    assert session.closed


def test_ask_with_a_path_loads_it_directly_without_a_snapshot(monkeypatch, tmp_path):
    """A path input is the kernel's file already — no parquet copy is made."""
    from crivo import query as ask_mod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("CRIVO_WORKSPACE", str(tmp_path))
    session = _FakeSession(_card())
    monkeypatch.setattr(ask_mod, "_make_session", lambda: session)

    ask_mod.ask(tmp_path / "input.csv", "how many rows?", name="sales")

    assert session.loaded == [(str(tmp_path / "input.csv"), "sales")]
    assert (
        not list((tmp_path / "ask").glob("*")) if (tmp_path / "ask").exists() else True
    )


def test_ask_surfaces_loop_notices_on_the_answer(monkeypatch, tmp_path):
    """A nudge/cap notice from the loop lands on Answer.notices, not stdout."""
    from crivo import query as ask_mod
    from crivo.events import CardReady, Notice

    class _NoticedSession(_FakeSession):
        def run_turn(self, question):
            yield Notice(kind="nudge", text="show your work")
            yield CardReady(card=self._card)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("CRIVO_WORKSPACE", str(tmp_path))
    session = _NoticedSession(_card())
    monkeypatch.setattr(ask_mod, "_make_session", lambda: session)

    answer = ask_mod.ask(tmp_path / "d.csv", "q?")

    assert answer.notices == ["nudge: show your work"]


def test_answer_code_is_the_final_ok_cell(monkeypatch, tmp_path):
    """`answer.code` is the code that actually produced the answer — the last
    cell that ran to status ok, same rule the card itself uses."""
    from crivo import query as ask_mod

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("CRIVO_WORKSPACE", str(tmp_path))
    card = _card(
        cells=[
            {"event_id": 2, "gate": "run", "status": "error", "code": "bad()"},
            {"event_id": 3, "gate": "run", "status": "ok", "code": "len(df)"},
        ]
    )
    monkeypatch.setattr(ask_mod, "_make_session", lambda: _FakeSession(card))

    answer = ask_mod.ask(tmp_path / "d.csv", "q?")

    assert answer.code == "len(df)"


def test_ask_closes_the_session_even_when_the_turn_raises(monkeypatch, tmp_path):
    """A crashed turn must not leak a kernel: close() runs, the error surfaces."""
    from crivo import query as ask_mod

    class _CrashingSession(_FakeSession):
        def run_turn(self, question):
            raise ValueError("kernel exploded")
            yield  # pragma: no cover — makes this a generator function

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("CRIVO_WORKSPACE", str(tmp_path))
    session = _CrashingSession(_card())
    monkeypatch.setattr(ask_mod, "_make_session", lambda: session)

    with pytest.raises(ValueError, match="kernel exploded"):
        ask_mod.ask(tmp_path / "d.csv", "q?")

    assert session.closed


def test_ask_is_exported_at_package_level():
    """`crivo.ask` and `crivo.Answer` are the public surface, next to
    diagnose/clean/read/write."""
    import crivo

    assert crivo.ask is not None
    assert "ask" in crivo.__all__
    assert crivo.Answer is not None


def test_answer_repr_is_the_card_markdown():
    """Printing an Answer shows the card: the answer, its checks, its code —
    not an object address."""
    from crivo.query import Answer

    text = repr(Answer(_card(), notices=[]))

    assert "20 rows" in text
    assert "len(df) == 20" in text
    assert "0x" not in text.split("\n")[0]


def test_answer_repr_html_is_a_self_contained_escaped_card():
    """In a notebook the Answer renders as the dark card: self-painted ground,
    a ✓ per check, the code, and every interpolated value escaped."""
    from crivo.query import Answer

    html = Answer(
        _card(answer="<b>20</b> rows"), notices=["cap: 1 cell left"]
    )._repr_html_()

    assert html.startswith("<div")
    assert "background:#14171a" in html
    assert "✓" in html and "len(df) == 20" in html
    assert "&lt;b&gt;20&lt;/b&gt; rows" in html
    assert "<b>20</b>" not in html
