"""generate() seam: DeepSeek default, Claude later behind the same signature (R3).

DeepSeek is OpenAI-compatible, so the openai SDK pointed at their base URL is
the whole client. The seam yields plain text chunks; only `delta.content` is
read, so a model that streams reasoning in `reasoning_content` never leaks
thinking into the tag parser.
"""

import os
import queue
import re
import threading
import time
from collections.abc import Iterable, Iterator

import httpx
from openai import OpenAI

# Overflow shapes for the providers generate() actually speaks to, each with
# the real error message it came from:
#
# - DeepSeek (OpenAI-compatible): "This model's maximum context length is
#   65536 tokens. However, you requested 78123 tokens ..."
# - DeepSeek/OpenAI error code: "context_length_exceeded"
# - OpenAI (newer phrasing): "Your input exceeds the context window of this model"
# - OpenAI-compatible gateways: "Input is too long for requested model"
# - Anthropic: "prompt is too long: 213462 tokens > 200000 maximum"
# - Anthropic: "input length and `max_tokens` exceed context limit:
#   195018 + 8192 > 200000"
# - Anthropic HTTP 413: '{"error":{"type":"request_too_large",...}}'
# - Generic token-count phrasing: "too many tokens" / "token limit exceeded"
#   (throttling says this too — _NOT_OVERFLOW_PATTERNS screens it out first)
_OVERFLOW_PATTERNS = (
    re.compile(r"maximum context length", re.IGNORECASE),
    re.compile(r"context[_ ]length[_ ]exceeded", re.IGNORECASE),
    re.compile(r"exceeds the context window", re.IGNORECASE),
    re.compile(r"input is too long", re.IGNORECASE),
    re.compile(r"prompt is too long", re.IGNORECASE),
    re.compile(r"max_tokens.{0,40}exceed", re.IGNORECASE),
    re.compile(r"request_too_large", re.IGNORECASE),
    re.compile(r"too many tokens", re.IGNORECASE),
    re.compile(r"token limit exceeded", re.IGNORECASE),
)

# Throttling shapes that also mention tokens and would otherwise false-match
# above (e.g. "429 ... too many tokens per minute"). Checked first: matching
# any of these means NOT overflow, whatever else the message says.
_NOT_OVERFLOW_PATTERNS = (
    re.compile(r"rate[_ ]?limit", re.IGNORECASE),  # DeepSeek/OpenAI 429 phrasing
    re.compile(r"too many requests", re.IGNORECASE),  # generic HTTP 429
    re.compile(r"per min(ute)?", re.IGNORECASE),  # "tokens per minute" quotas
    re.compile(r"overloaded", re.IGNORECASE),  # Anthropic 529 overloaded_error
)


def is_context_overflow(message: str) -> bool:
    """True when a provider error says the prompt outgrew the context window.

    Throttling errors mention tokens too ("too many tokens per minute") and
    must stay False: they mean retry later, not shrink the prompt.
    """
    if any(p.search(message) for p in _NOT_OVERFLOW_PATTERNS):
        return False
    return any(p.search(message) for p in _OVERFLOW_PATTERNS)


# Every env var that can carry a provider credential or selection, anywhere in
# crivo (llm.py, __main__.py, mcp_server.py). The single source of truth: the
# test-suite quarantine deletes exactly these so an ambient shell key can never
# make a test pass that would fail in CI.
KEY_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "CRIVO_PROVIDER",
    "CRIVO_MODEL",
    "CRIVO_BASE_URL",
    "CRIVO_API_KEY",
    "CRIVO_STALL_S",
    "CRIVO_MAX_CALL_S",
)

DEFAULT_MODEL = "deepseek-v4-pro"
BASE_URL = "https://api.deepseek.com"
CLAUDE_DEFAULT_MODEL = "claude-opus-5"
CLAUDE_MAX_TOKENS = 8192
TEMPERATURE = 0.0
# A read timeout cannot fire while reasoning chunks keep arriving, so a model
# can think without bound. This is the ceiling on one call, start to finish.
MAX_CALL_S = 420
STALL_S = 90  # no bytes at all for this long means the stream is dead


def stall_budget_s() -> float:
    """STALL_S, env-tunable (CRIVO_STALL_S): a local model can take minutes
    loading weights before its first token, which is not a dead stream."""
    return float(os.environ.get("CRIVO_STALL_S") or STALL_S)


def call_budget_s() -> float:
    """MAX_CALL_S, env-tunable (CRIVO_MAX_CALL_S) for slow local endpoints."""
    return float(os.environ.get("CRIVO_MAX_CALL_S") or MAX_CALL_S)


class CallCancelled(RuntimeError):
    """The user canceled the in-flight model call (A0 R2)."""


# One-shot cancel channel: the REPL's interrupt path sets it, the in-flight
# _watched loop raises CallCancelled and clears it. A fresh call always
# starts clean, so a cancel with nothing in flight targets nobody.
_cancel = threading.Event()


def request_cancel() -> None:
    _cancel.set()


_client: OpenAI | None = None
_claude_client = None
_openai_client: OpenAI | None = None  # CRIVO_PROVIDER=openai: generic endpoint

# CRIVO_PROVIDER=faux: canned replies consumed FIFO by generate(), one per
# call. No network, no SDK — same seam, so callers need no test-only branch.
_faux_responses: list[str] = []


def faux_enqueue(*texts: str) -> None:
    """Queue canned replies for the faux provider, delivered in order."""
    _faux_responses.extend(texts)


def _provider() -> str:
    """R10: two providers, one env switch, no registry."""
    return os.environ.get("CRIVO_PROVIDER", "deepseek")


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=BASE_URL,
        )
    return _client


def _get_claude_client():
    global _claude_client
    if _claude_client is None:
        # imported here so the deepseek-only install never pays for (or
        # needs) the anthropic package at import time
        import anthropic

        _claude_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _claude_client


def _get_openai_client() -> OpenAI:
    """CRIVO_PROVIDER=openai: any OpenAI-compatible /v1 server (Ollama,
    OpenRouter, vLLM). The key is optional — local servers ignore it, but the
    SDK requires a non-empty string, hence the placeholder."""
    global _openai_client
    if _openai_client is None:
        base_url = os.environ.get("CRIVO_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "CRIVO_PROVIDER=openai needs CRIVO_BASE_URL — the endpoint's /v1 "
                "root (Ollama example: http://localhost:11434/v1)"
            )
        if not base_url.startswith(("http://", "https://")):
            raise RuntimeError(
                f"CRIVO_BASE_URL must start with http:// or https:// — got "
                f"{base_url!r} (a bare host dies deep in the SDK instead)"
            )
        _openai_client = OpenAI(
            api_key=os.environ.get("CRIVO_API_KEY", "local-no-key"),
            base_url=base_url,
        )
    return _openai_client


def model_name() -> str:
    provider = _provider()
    if provider == "openai":
        # no sane default exists across arbitrary endpoints, so silence here
        # would mean silently querying somebody's default-loaded model
        model = os.environ.get("CRIVO_MODEL")
        if not model:
            raise RuntimeError(
                "CRIVO_PROVIDER=openai needs CRIVO_MODEL — the endpoint's model "
                "id (e.g. llama3.3, qwen3:32b, openrouter/auto)"
            )
        return model
    default = CLAUDE_DEFAULT_MODEL if provider == "claude" else DEFAULT_MODEL
    return os.environ.get("CRIVO_MODEL", default)


def model_info() -> dict:
    """Provenance stamp for the answer card."""
    return {"provider": _provider(), "model": model_name(), "temperature": TEMPERATURE}


def _deepseek_text(item) -> str | None:
    if not item.choices:
        return None
    delta = item.choices[0].delta
    if getattr(delta, "content", None):
        return delta.content
    if getattr(delta, "reasoning_content", None):
        # A reasoning model can think for minutes before its first content
        # token. Dropping those chunks silently — which we must, so thinking
        # never reaches the tag parser — makes a working turn look hung.
        # An empty chunk says "still alive" and carries nothing.
        return ""
    return None


def _claude_text(event) -> str | None:
    if getattr(event, "type", "") != "content_block_delta":
        return None
    delta = event.delta
    if getattr(delta, "type", "") == "text_delta":
        return delta.text
    if getattr(delta, "type", "") == "thinking_delta":
        return ""  # same heartbeat contract as reasoning_content above
    return None


def generate(messages: Iterable[dict], model: str | None = None) -> Iterator[str]:
    """Yield response text chunks for a chat completion.

    The loop joins chunks before parsing tags; drivers may render them live.
    The provider switch changes which client produces the stream and how one
    stream item becomes text; the watchdog around them is shared, because a
    hung stream hangs identically whoever serves it. Every call emits one
    telemetry span (A0 R4) carrying model, duration, and usage when the
    provider reports it (cache hit/miss tokens included on DeepSeek).
    """
    from crivo import telemetry

    # observer only: a misconfigured provider must raise its own error from
    # the seam below, in the same order as before telemetry existed
    try:
        requested = model or model_name()
    except RuntimeError:
        requested = ""
    usage_box: list = []
    with telemetry.span(
        "gen_ai.client.call",
        **{"gen_ai.system": _provider(), "gen_ai.request.model": requested},
    ) as extra:
        yield from _generate(messages, model, usage_box.append)
        for usage in usage_box[-1:]:
            for src, dst in (
                ("prompt_tokens", "gen_ai.usage.input_tokens"),
                ("completion_tokens", "gen_ai.usage.output_tokens"),
                ("prompt_cache_hit_tokens", "crivo.cache.hit_tokens"),
                ("prompt_cache_miss_tokens", "crivo.cache.miss_tokens"),
            ):
                value = getattr(usage, src, None)
                if value is not None:
                    extra[dst] = value


def _generate(
    messages: Iterable[dict], model: str | None, usage_sink
) -> Iterator[str]:
    if _provider() == "faux":
        if not _faux_responses:
            raise RuntimeError(
                "faux provider: response queue is empty — queue replies with "
                "llm.faux_enqueue() before calling generate()"
            )
        # one queued reply per call, through the same watchdog as the real
        # providers so a faux run exercises the code path callers get
        yield from _watched(
            iter([_faux_responses.pop(0)]), lambda item: item, usage_sink
        )
        return

    if _provider() == "claude":
        msgs = list(messages)
        # anthropic takes system as a top-level param, not a message role
        system = "\n\n".join(m["content"] for m in msgs if m["role"] == "system")
        stream = _get_claude_client().messages.create(
            model=model or model_name(),
            max_tokens=CLAUDE_MAX_TOKENS,
            system=system,
            messages=[m for m in msgs if m["role"] != "system"],
            temperature=TEMPERATURE,
            stream=True,
        )
        yield from _watched(stream, _claude_text, usage_sink)
        return

    # deepseek and the generic openai provider share the wire format; only
    # which client (fixed DeepSeek endpoint vs env-configured) differs
    client = _get_openai_client() if _provider() == "openai" else _get_client()
    stream = client.chat.completions.create(
        model=model or model_name(),
        messages=list(messages),
        temperature=TEMPERATURE,
        stream=True,
        # the final stream chunk then carries usage, including DeepSeek's
        # prompt_cache_hit_tokens / prompt_cache_miss_tokens (A0 R4/R5)
        stream_options={"include_usage": True},
        # First line of defense, not the only one: httpx re-applies this read
        # timeout to every socket read for the life of the stream (confirmed
        # against a real local socket -- it is not just a connect timeout), so
        # it independently catches true wire silence for the real client. The
        # wall-clock checks below no longer assume that is enough on its own.
        timeout=httpx.Timeout(
            connect=15.0, read=stall_budget_s(), write=30.0, pool=15.0
        ),
    )
    yield from _watched(stream, _deepseek_text, usage_sink)


def _watched(stream, extract, usage_sink=None) -> Iterator[str]:
    """Enforce the call and stall budgets around any provider's stream.

    Consuming the stream happens on a background thread so both budgets below
    are enforced on the wall clock even while that thread sits blocked inside
    a single `next()` call. That case matters: a same-thread check can only
    run between chunks, so it can never catch a stream that stops producing
    chunks at all -- confirmed to be exactly what one real ~15-minute
    production hang was, back when only MAX_CALL_S existed.
    """
    _cancel.clear()  # a cancel with nothing in flight targets nobody
    done = object()
    chunks: queue.Queue = queue.Queue()
    stop = threading.Event()

    def pump() -> None:
        try:
            for item in stream:
                if stop.is_set():
                    return
                chunks.put(item)
            chunks.put(done)
        except Exception as exc:  # noqa: BLE001 — forwarded, raised on the caller's thread below
            chunks.put(exc)

    threading.Thread(target=pump, daemon=True).start()

    call_budget, stall_budget = call_budget_s(), stall_budget_s()
    started = last_progress = time.monotonic()
    try:
        while True:
            if _cancel.is_set():
                _cancel.clear()
                raise CallCancelled("model call canceled by user")
            now = time.monotonic()
            call_left = call_budget - (now - started)
            stall_left = stall_budget - (now - last_progress)
            if call_left <= 0:
                raise TimeoutError(
                    f"gave up after {call_budget:g}s of thinking with no complete reply"
                )
            if stall_left <= 0:
                raise TimeoutError(f"gave up after {stall_budget:g}s of silence")

            try:
                # capped short so cancel (above) answers in a beat, not at the
                # end of a stall budget
                item = chunks.get(timeout=min(call_left, stall_left, 0.25))
            except queue.Empty:
                continue  # re-check cancel and budgets

            last_progress = time.monotonic()
            if item is done:
                return
            if isinstance(item, Exception):
                raise item
            if usage_sink is not None:
                usage = getattr(item, "usage", None)
                if usage is not None:
                    usage_sink(usage)
            text = extract(item)
            if text is not None:
                yield text
    finally:
        # Tells the pump thread to stop queueing once its current `next()`
        # call returns, instead of consuming an abandoned stream forever in
        # the background. Not a force-close: closing the underlying stream
        # from this thread while the pump thread may be mid-`next()` on it is
        # exactly the kind of cross-thread resource race this function exists
        # to avoid. The real client still bounds that call on its own via the
        # `timeout=` above; daemon=True means an abandoned fake never outlives
        # the process either way.
        stop.set()
