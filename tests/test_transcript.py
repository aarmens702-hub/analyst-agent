import json
from datetime import datetime
from pathlib import Path

import pytest

from analyst_agent.transcript import Transcript


def test_ids_monotonic_from_1(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "transcript.jsonl")
    assert t.append("session_meta", session="s01") == 1
    assert t.append("user", text="hi") == 2
    assert t.append("model", text="hello") == 3


def test_every_line_is_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    t = Transcript(path)
    t.append("user", text="q1")
    t.append("exec", code="1 + 1")
    del t
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        ev = json.loads(line)
        assert isinstance(ev, dict)
        assert {"ev_id", "kind"} <= ev.keys()


def test_append_is_flushed_while_still_open(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    t = Transcript(path)
    t.append("user", text="acknowledged")
    with path.open(encoding="utf-8") as second_handle:
        lines = second_handle.read().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["ev_id"] == 1
    assert t.append("user", text="writer survived the read") == 2


def test_unknown_kind_raises_value_error(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "transcript.jsonl")
    with pytest.raises(ValueError):
        t.append("telemetry", detail="not in the vocabulary")


def test_nested_payload_roundtrips_through_events(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "transcript.jsonl")
    payload = {
        "code": "df.head()",
        "registry": [{"name": "df", "type": "DataFrame", "shape": [3, 2]}],
        "truncated": {"stream": False, "value": None},
    }
    t.append("exec", **payload)
    (ev,) = t.events()
    assert ev["ev_id"] == 1
    assert ev["kind"] == "exec"
    for key, value in payload.items():
        assert ev[key] == value


def test_reopen_continues_numbering(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    t = Transcript(path)
    t.append("user", text="one")
    t.append("model", text="two")
    del t
    reopened = Transcript(path)
    assert reopened.append("gate", action="run") == 3
    assert [ev["ev_id"] for ev in reopened.events()] == [1, 2, 3]


def test_timestamps_are_tz_aware_iso8601(tmp_path: Path) -> None:
    t = Transcript(tmp_path / "transcript.jsonl")
    t.append("card", card_id="s01-c001")
    (ev,) = t.events()
    stamp = datetime.fromisoformat(ev["t"])
    assert stamp.tzinfo is not None
    assert stamp.utcoffset() is not None
