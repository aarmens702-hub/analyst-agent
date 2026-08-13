"""generate() seam: DeepSeek default, Claude later behind the same signature (R3).

DeepSeek is OpenAI-compatible, so the openai SDK pointed at their base URL is
the whole client. The seam yields plain text chunks; only `delta.content` is
read, so a model that streams reasoning in `reasoning_content` never leaks
thinking into the tag parser.
"""

import os
import queue
import threading
import time
from collections.abc import Iterable, Iterator

import httpx
from openai import OpenAI

DEFAULT_MODEL = "deepseek-v4-pro"
BASE_URL = "https://api.deepseek.com"
TEMPERATURE = 0.0
# A read timeout cannot fire while reasoning chunks keep arriving, so a model
# can think without bound. This is the ceiling on one call, start to finish.
MAX_CALL_S = 420
STALL_S = 90  # no bytes at all for this long means the stream is dead

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=BASE_URL,
        )
    return _client


def model_name() -> str:
    return os.environ.get("ANALYST_MODEL", DEFAULT_MODEL)


def model_info() -> dict:
    """Provenance stamp for the answer card."""
    return {"provider": "deepseek", "model": model_name(), "temperature": TEMPERATURE}


def generate(messages: Iterable[dict], model: str | None = None) -> Iterator[str]:
    """Yield response text chunks for a chat completion.

    The loop joins chunks before parsing tags; drivers may render them live.

    Consuming the stream happens on a background thread so both budgets below
    are enforced on the wall clock even while that thread sits blocked inside
    a single `next()` call. That case matters: a same-thread check can only
    run between chunks, so it can never catch a stream that stops producing
    chunks at all -- confirmed to be exactly what one real ~15-minute
    production hang was, back when only MAX_CALL_S existed.
    """
    stream = _get_client().chat.completions.create(
        model=model or model_name(),
        messages=list(messages),
        temperature=TEMPERATURE,
        stream=True,
        # First line of defense, not the only one: httpx re-applies this read
        # timeout to every socket read for the life of the stream (confirmed
        # against a real local socket -- it is not just a connect timeout), so
        # it independently catches true wire silence for the real client. The
        # wall-clock checks below no longer assume that is enough on its own.
        timeout=httpx.Timeout(connect=15.0, read=STALL_S, write=30.0, pool=15.0),
    )

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

    started = last_progress = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            call_left = MAX_CALL_S - (now - started)
            stall_left = STALL_S - (now - last_progress)
            if call_left <= 0:
                raise TimeoutError(
                    f"gave up after {MAX_CALL_S}s of thinking with no complete reply"
                )
            if stall_left <= 0:
                raise TimeoutError(f"gave up after {STALL_S}s of silence")

            try:
                item = chunks.get(timeout=min(call_left, stall_left))
            except queue.Empty:
                continue  # a budget just ran out; the checks above will raise

            last_progress = time.monotonic()
            if item is done:
                return
            if isinstance(item, Exception):
                raise item
            if not item.choices:
                continue
            delta = item.choices[0].delta
            if getattr(delta, "content", None):
                yield delta.content
            elif getattr(delta, "reasoning_content", None):
                # A reasoning model can think for minutes before its first content
                # token. Dropping those chunks silently — which we must, so thinking
                # never reaches the tag parser — makes a working turn look hung.
                # An empty chunk says "still alive" and carries nothing.
                yield ""
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
