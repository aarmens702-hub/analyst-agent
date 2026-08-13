"""generate() seam tests: a fake client stands in for the openai SDK so every
test runs with no network calls and no API key. Each fake stream is a plain
iterator we control -- it can yield reasoning-only chunks, content chunks, or
block -- so we can drive the same code paths a real DeepSeek stream would hit
without ever opening a socket."""

import time
from types import SimpleNamespace

import pytest

from analyst_agent import llm


def chunk(content=None, reasoning=None):
    """A double for one `ChatCompletionChunk`: only `.choices[0].delta` is
    ever read by generate(), so that's all this needs to have."""
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def fake_client(stream):
    """A client double whose chat.completions.create(**_) hands back a
    pre-built stream, ignoring every call kwarg (model, timeout, ...)."""

    def create(**_):
        return stream

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def test_content_chunks_pass_through_unchanged(monkeypatch):
    stream = iter([chunk(content="hel"), chunk(content="lo")])
    monkeypatch.setattr(llm, "_get_client", lambda: fake_client(stream))

    result = list(llm.generate([{"role": "user", "content": "hi"}]))

    assert result == ["hel", "lo"]


def test_reasoning_only_chunk_yields_heartbeat_not_leaked_text(monkeypatch):
    stream = iter(
        [
            chunk(reasoning="thinking step one..."),
            chunk(reasoning="thinking step two..."),
            chunk(content="done"),
        ]
    )
    monkeypatch.setattr(llm, "_get_client", lambda: fake_client(stream))

    result = list(llm.generate([{"role": "user", "content": "hi"}]))

    assert result == ["", "", "done"], "reasoning text must never reach the caller"


def test_slow_but_progressing_stream_exceeds_total_budget(monkeypatch):
    """Chunks keep arriving -- just too slowly and for too long -- so the
    call-length ceiling must fire even though the stream is technically
    alive the whole time."""
    monkeypatch.setattr(llm, "MAX_CALL_S", 0.05)

    def endless():
        while True:
            time.sleep(0.02)
            yield chunk(reasoning="...")

    monkeypatch.setattr(llm, "_get_client", lambda: fake_client(endless()))

    with pytest.raises(TimeoutError, match="thinking"):
        list(llm.generate([{"role": "user", "content": "hi"}]))


def test_a_stream_that_goes_silent_is_abandoned(monkeypatch):
    """No read timeout, no chunk -- pure wire silence on an established
    connection, which is what a real 15-minute production hang traced back
    to. This is the case MAX_CALL_S cannot rescue on its own: its check only
    runs when a chunk arrives, and here none ever does. The guard must give
    up on its own wall clock rather than wait for the stream to speak again.

    The fake blocks for 0.5s (not forever) so a regression fails this test
    fast instead of hanging the suite; the assertion on `elapsed` is what
    actually proves the guard didn't just wait the block out."""
    monkeypatch.setattr(llm, "STALL_S", 0.05)

    class GoesSilent:
        def __iter__(self):
            return self

        def __next__(self):
            time.sleep(0.5)
            raise StopIteration

    monkeypatch.setattr(llm, "_get_client", lambda: fake_client(GoesSilent()))

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="silence"):
        list(llm.generate([{"role": "user", "content": "hi"}]))
    elapsed = time.monotonic() - started

    assert elapsed < 0.3, "must give up on the wall clock, not wait for the stream"
