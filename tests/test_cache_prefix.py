"""Cache-prefix hygiene guard (A0 R5). The system prompt is the cacheable
prefix (prompts.py says so), and DeepSeek/Anthropic prefix-caching only pays
if that prefix is byte-identical across calls. The classic cache-killer is a
timestamp, date, or per-run id interpolated into an otherwise-stable prompt
(research "Don't Break the Cache"). This pins that invariant so a later edit
can't silently reintroduce volatile content into the stable prefix."""

import re

from crivo import prompts

# the prompts that ride at or near the front of every call and must stay stable
_STABLE = ("SYSTEM_PROMPT", "CLEAN_PROMPT", "NUDGE_PROMPT", "CLEAN_NUDGE_PROMPT")

_VOLATILE = (
    re.compile(r"\d{4}-\d{2}-\d{2}"),  # a date
    re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b"),  # a time
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", re.IGNORECASE),  # a uuid
)


def test_stable_prompts_carry_no_volatile_tokens():
    for name in _STABLE:
        text = getattr(prompts, name, None)
        if text is None:
            continue  # not every name exists in every version; skip cleanly
        for pattern in _VOLATILE:
            assert not pattern.search(text), (
                f"{name} embeds a volatile token matching {pattern.pattern!r}; "
                "that breaks the cacheable prefix (A0 R5)"
            )


def test_generate_scoped_appends_volatile_registry_last(monkeypatch):
    """The single model choke point must keep the volatile registry block at
    the END of the context, so everything before it stays a stable prefix."""
    import types

    # a stub kernel client so importing/constructing a Session needs no real
    # kernel — we only exercise the message assembly in _generate_scoped
    from crivo import llm
    from crivo.loop import Session

    captured = {}

    def fake_generate(context, model=None):
        captured["context"] = list(context)
        yield "ok"

    monkeypatch.setattr(llm, "generate", fake_generate)

    class _StubKernel:
        def __init__(self, *a, **k):
            pass

        def start(self):
            return types.SimpleNamespace(pid=1, python="3.12", ipykernel="7")

        def close(self):
            pass

    monkeypatch.setattr("crivo.loop.KernelClient", _StubKernel)
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        s = Session(workspace=d, data_dir=d, skills_dir=d, preview=False, snapshots=False)
        s._registry = [{"name": "df", "type": "DataFrame", "shape": [3, 2], "mem_mb": 0.0}]
        msgs = [
            {"role": "system", "content": "STABLE-PREFIX"},
            {"role": "user", "content": "the question"},
        ]
        list(s._generate_scoped(msgs, stream=False))
        s.close()

    ctx = captured["context"]
    assert ctx[0]["content"] == "STABLE-PREFIX"  # stable prefix stays first
    assert ctx[:2] == msgs  # nothing volatile is injected before the caller's msgs
    assert ctx[-1]["role"] == "user"  # the appended registry block is last
    assert "df" in ctx[-1]["content"]  # and it is the (volatile) registry
