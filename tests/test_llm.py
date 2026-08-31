"""generate() seam tests: a fake client stands in for the openai SDK so every
test runs with no network calls and no API key. Each fake stream is a plain
iterator we control -- it can yield reasoning-only chunks, content chunks, or
block -- so we can drive the same code paths a real DeepSeek stream would hit
without ever opening a socket."""

import inspect
import os
import re
import time
from types import SimpleNamespace

import pytest

from crivo import llm


@pytest.fixture(scope="module")
def ambient_key():
    """Plants a fake shell credential the way a developer's rc file would.

    Module scope matters: higher-scoped fixtures set up before the
    function-scoped autouse quarantine in conftest, so the plant is in
    os.environ when the quarantine runs — exactly the ambient-leak scenario."""
    os.environ["DEEPSEEK_API_KEY"] = "sk-ambient-shell-leak"
    yield
    os.environ.pop("DEEPSEEK_API_KEY", None)


def test_ambient_credentials_never_reach_a_test(ambient_key):
    """F2: a real key in the developer's shell must not make a test pass that
    would fail keyless in CI. The conftest quarantine deletes every var in
    KEY_ENV_VARS before each test; tests that need one set it explicitly."""
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_deepseek_context_overflow_is_recognized():
    """F3: the shape DeepSeek's OpenAI-compatible endpoint actually returns
    when the prompt outgrows the window."""
    msg = (
        "Error code: 400 - {'error': {'message': 'This model's maximum "
        "context length is 65536 tokens. However, you requested 78123 tokens "
        "(70000 in the messages, 8123 in the completion).', "
        "'code': 'invalid_request_error'}}"
    )
    assert llm.is_context_overflow(msg) is True


def test_anthropic_context_overflow_is_recognized():
    """F3: both real Anthropic overflow shapes — the plain prompt overflow and
    the prompt+max_tokens budget overflow."""
    assert (
        llm.is_context_overflow("prompt is too long: 213462 tokens > 200000 maximum")
        is True
    )
    assert (
        llm.is_context_overflow(
            "input length and `max_tokens` exceed context limit: 195018 + 8192 > 200000"
        )
        is True
    )


def test_rate_limit_errors_are_not_context_overflow():
    """F3: throttling mentions tokens too, but means retry-later, not
    shrink-the-prompt. It must never be classified as overflow."""
    assert (
        llm.is_context_overflow(
            "Error code: 429 - rate limit reached: too many tokens per minute, "
            "please wait before trying again"
        )
        is False
    )


def test_unrelated_errors_are_not_context_overflow():
    """F3: an ordinary failure shares no overflow vocabulary and stays False."""
    assert llm.is_context_overflow("Connection reset by peer") is False


def test_faux_provider_returns_queued_responses_in_order(monkeypatch):
    """F4: CRIVO_PROVIDER=faux rides the same generate() seam callers use —
    no network, no SDK — handing back queued canned replies in order."""
    monkeypatch.setenv("CRIVO_PROVIDER", "faux")
    monkeypatch.setattr(llm, "_faux_responses", [])  # isolate queue state
    llm.faux_enqueue("first reply", "second reply")

    assert "".join(llm.generate([{"role": "user", "content": "a"}])) == "first reply"
    assert "".join(llm.generate([{"role": "user", "content": "b"}])) == "second reply"


def test_faux_provider_exhausted_queue_raises_a_clear_error(monkeypatch):
    """F4: running past the queue must name the faux provider and say how to
    feed it, not surface a bare IndexError from deep in the seam."""
    monkeypatch.setenv("CRIVO_PROVIDER", "faux")
    monkeypatch.setattr(llm, "_faux_responses", [])
    llm.faux_enqueue("only reply")
    assert "".join(llm.generate([{"role": "user", "content": "a"}])) == "only reply"

    with pytest.raises(RuntimeError, match="faux"):
        list(llm.generate([{"role": "user", "content": "b"}]))


def test_key_env_vars_registry_covers_every_env_var_the_module_reads():
    """F1: one source of truth for provider credentials and selection. The
    membership is pinned exactly, and every os.environ read in llm.py must be
    registered so a new read can't silently dodge the test-suite quarantine."""
    assert set(llm.KEY_ENV_VARS) == {
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "CRIVO_PROVIDER",
        "CRIVO_MODEL",
    }
    source = inspect.getsource(llm)
    reads = set(re.findall(r'environ(?:\.get\(|\[)\s*"([A-Z_]+)"', source))
    assert reads <= set(llm.KEY_ENV_VARS), f"unregistered env reads: {reads}"


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


def claude_event(text=None, thinking=None, kind="content_block_delta"):
    """A double for one anthropic stream event: only `.type` and
    `.delta.{type,text}` are ever read by the claude path."""
    if text is not None:
        delta = SimpleNamespace(type="text_delta", text=text)
    else:
        delta = SimpleNamespace(type="thinking_delta", thinking=thinking or "")
    return SimpleNamespace(type=kind, delta=delta)


def fake_claude_client(stream, calls=None):
    """A client double for anthropic's messages.create(**kw)."""

    def create(**kw):
        if calls is not None:
            calls.append(kw)
        return stream

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_the_provider_switch_selects_claude_behind_the_same_seam(monkeypatch):
    """R10: generate(messages) -> Iterator[str] is the whole contract, and a
    provider env switch must be the only difference a caller can observe.
    model_info() hard-coded "deepseek", so every answer card produced through
    the Claude half would have carried a false provenance stamp."""
    monkeypatch.setenv("CRIVO_PROVIDER", "claude")
    monkeypatch.delenv("CRIVO_MODEL", raising=False)
    calls: list = []
    stream = iter([claude_event(text="hel"), claude_event(text="lo")])
    monkeypatch.setattr(
        llm, "_get_claude_client", lambda: fake_claude_client(stream, calls)
    )

    result = list(
        llm.generate(
            [
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "hi"},
            ]
        )
    )

    assert result == ["hel", "lo"]
    info = llm.model_info()
    assert info["provider"] == "claude"
    assert info["model"].startswith("claude-")
    # anthropic takes system as a top-level param, not a message role
    assert calls[0]["system"] == "be terse"
    assert all(m["role"] != "system" for m in calls[0]["messages"])


def test_claude_thinking_deltas_are_heartbeats_not_text(monkeypatch):
    """Same contract as DeepSeek's reasoning_content: thinking never reaches
    the tag parser, but an empty chunk says the stream is alive."""
    monkeypatch.setenv("CRIVO_PROVIDER", "claude")
    stream = iter(
        [
            claude_event(thinking="pondering..."),
            claude_event(text="done"),
            SimpleNamespace(type="message_stop"),
        ]
    )
    monkeypatch.setattr(llm, "_get_claude_client", lambda: fake_claude_client(stream))

    assert list(llm.generate([{"role": "user", "content": "hi"}])) == ["", "done"]


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
