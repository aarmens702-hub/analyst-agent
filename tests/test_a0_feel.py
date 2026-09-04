"""A0 feel-and-visibility units (spec: specs/2026-09-04-a0-design.md).

Claude-lane pieces only: the cancel token in the llm seam, the telemetry
writer, and bench model stamping. Loop-side streaming coverage and steer are
core proposals and are tested when they land.
"""

import json
import threading

import pytest

from crivo import llm


def test_cancel_aborts_in_flight_call_and_self_clears():
    release = threading.Event()

    def fake_stream():
        yield "first"
        release.wait(5)  # a long thinking pause the test never releases early
        yield "late"

    gen = llm._watched(fake_stream(), lambda item: item)
    assert next(gen) == "first"
    llm.request_cancel()
    with pytest.raises(llm.CallCancelled):
        next(gen)
    release.set()
    # the flag self-clears: a fresh call streams normally
    assert list(llm._watched(iter(["ok"]), lambda item: item)) == ["ok"]


def test_cancel_between_calls_is_a_noop():
    """A cancel with nothing in flight must not poison the next call."""
    llm.request_cancel()
    assert list(llm._watched(iter(["fine"]), lambda item: item)) == ["fine"]


def test_telemetry_disabled_is_noop(monkeypatch):
    monkeypatch.delenv("CRIVO_TELEMETRY", raising=False)
    from crivo import telemetry

    with telemetry.span("gen_ai.client.call", probe=1):
        pass  # must not raise, must not write anywhere
    assert not telemetry.enabled()


def test_telemetry_span_writes_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("CRIVO_TELEMETRY", str(path))
    from crivo import telemetry

    with telemetry.span("gen_ai.client.call", **{"gen_ai.request.model": "m1"}):
        pass
    row = json.loads(path.read_text().splitlines()[0])
    assert row["name"] == "gen_ai.client.call"
    assert row["attrs"]["gen_ai.request.model"] == "m1"
    assert row["dur_s"] >= 0


def test_generate_emits_call_span(tmp_path, monkeypatch):
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("CRIVO_TELEMETRY", str(path))
    monkeypatch.setenv("CRIVO_PROVIDER", "faux")
    llm.faux_enqueue("hello")

    assert "".join(llm.generate([{"role": "user", "content": "q"}])) == "hello"

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    call = next(r for r in rows if r["name"] == "gen_ai.client.call")
    assert call["attrs"]["gen_ai.system"] == "faux"
    assert call["attrs"]["gen_ai.request.model"]
    assert call["dur_s"] >= 0
