"""generate() seam: DeepSeek default, Claude later behind the same signature (R3).

DeepSeek is OpenAI-compatible, so the openai SDK pointed at their base URL is
the whole client. The seam yields plain text chunks; only `delta.content` is
read, so a model that streams reasoning in `reasoning_content` never leaks
thinking into the tag parser.
"""

import os
from collections.abc import Iterable, Iterator

from openai import OpenAI

DEFAULT_MODEL = "deepseek-v4-pro"
BASE_URL = "https://api.deepseek.com"
TEMPERATURE = 0.0

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
    """
    stream = _get_client().chat.completions.create(
        model=model or model_name(),
        messages=list(messages),
        temperature=TEMPERATURE,
        stream=True,
        timeout=180,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if getattr(delta, "content", None):
            yield delta.content
        elif getattr(delta, "reasoning_content", None):
            # A reasoning model can think for minutes before its first content
            # token. Dropping those chunks silently — which we must, so thinking
            # never reaches the tag parser — makes a working turn look hung.
            # An empty chunk says "still alive" and carries nothing.
            yield ""
