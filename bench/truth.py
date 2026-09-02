"""Ground truth for the Proving Ground (spec R1) — the frozen interface.

Injectors record every planted corruption here; the scorer reads it back; the
external adapter emits it from clean/dirty pairs. Values stored in `Cell` must
be JSON-safe (injectors store reprs for anything that isn't). Disease ids are
the crivo taxonomy 1..22, plus 0 for external corruption of unknown taxonomy
(the Raha pairs, where only the cell diff is known).

Coordinate stability: `Cell.row` is a positional index into the pristine
frame's RangeIndex. Injectors modify cells in place and append row-granular
material (duplicates, aggregate rows) at the end — existing rows are never
reordered — so coordinates stay valid for the life of a pair.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

GRANULARITIES = ("cell", "row", "column")


@dataclass(frozen=True)
class Cell:
    row: int  # positional index into the pristine frame
    column: str
    original: object  # value in the pristine frame (JSON-safe)
    corrupted: object  # value planted in the dirty frame (JSON-safe)


@dataclass(frozen=True)
class Corruption:
    disease: int  # taxonomy id 1..22; 0 = external/unknown
    columns: tuple[str, ...]
    granularity: str  # one of GRANULARITIES
    cells: tuple[Cell, ...] = ()  # granularity == "cell", else ()
    rows: tuple[int, ...] = ()  # granularity == "row": positions in the DIRTY frame
    note: str = ""

    def __post_init__(self):
        if self.granularity not in GRANULARITIES:
            raise ValueError(
                f"granularity must be one of {GRANULARITIES}, got {self.granularity!r}"
            )
        if not 0 <= self.disease <= 22:
            raise ValueError(
                f"disease must be 0 (external) or 1..22, got {self.disease}"
            )


@dataclass
class GroundTruth:
    seed: int
    base: str  # base-generator name; "external" for adapted pairs
    n_rows: int  # pristine row count (dirty may exceed it via appended rows)
    n_cols: int
    frame_sha256: str  # sha256 of the dirty frame's to_csv() bytes
    corruptions: list[Corruption] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, text: str) -> GroundTruth:
        data = json.loads(text)
        data["corruptions"] = [
            Corruption(
                disease=c["disease"],
                columns=tuple(c["columns"]),
                granularity=c["granularity"],
                cells=tuple(Cell(**cell) for cell in c["cells"]),
                rows=tuple(c["rows"]),
                note=c["note"],
            )
            for c in data["corruptions"]
        ]
        return cls(**data)

    def verify_frame(self, frame) -> None:
        """Refuse a frame/manifest pair that drifted apart — a scorer running
        against the wrong dirty frame would produce confident nonsense."""
        digest = frame_sha256(frame)
        if digest != self.frame_sha256:
            raise ValueError(
                f"frame does not match manifest: sha256 {digest[:12]}… "
                f"vs recorded {self.frame_sha256[:12]}…"
            )


def frame_sha256(frame) -> str:
    """Pin a dirty frame to its manifest: sha256 of the CSV serialization."""
    return hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()
