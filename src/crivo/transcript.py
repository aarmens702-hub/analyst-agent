import json
from datetime import UTC, datetime
from pathlib import Path

KINDS = frozenset(
    # "plan" is the M2-min plan artifact, recorded so /why can cite it
    {"session_meta", "user", "model", "gate", "exec", "card", "plan"}
)


class Transcript:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._last_id = 0
        if path.exists():
            self._last_id = max((ev["ev_id"] for ev in self.events()), default=0)
        self._fh = path.open("a", encoding="utf-8")

    def append(self, kind: str, **payload: object) -> int:
        if kind not in KINDS:
            raise ValueError(
                f"unknown event kind {kind!r}; expected one of {sorted(KINDS)}"
            )
        ev_id = self._last_id + 1
        event = {
            "ev_id": ev_id,
            "t": datetime.now(UTC).isoformat(),
            "kind": kind,
            **payload,
        }
        line = json.dumps(event)
        self._fh.write(line + "\n")
        self._fh.flush()
        self._last_id = ev_id
        return ev_id

    def events(self) -> list[dict]:
        with self._path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]
