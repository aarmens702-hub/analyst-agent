"""Typed events between the turn loop and its drivers (P0 spec section 1).

The hand-written turn loop (loop.py, which implements SessionLike) yields these
events from its run_turn generator; drivers render them and answer via
gen.send(). GateRequest is answered with a GateDecision; every other event is
answered with None; CardReady is terminal for the turn.
"""

from collections.abc import Generator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GateRequest:
    """A model-authored cell awaiting the user's gate decision (R4).

    The driver must answer via gen.send(GateDecision(...)). `title` is empty
    for QUERY turns; CLEAN fix turns carry the finding header (P1 R8).
    """

    code: str
    iteration: int
    title: str = ""
    # R3: the rendered consequence of running this cell on a sampled scratch
    # copy — empty when previews are off or the preview itself failed loudly
    preview: str = ""
    # the finding's grade (AUTO/GATE/HUMAN), so a headless driver can apply
    # the human's pre-authorisation instead of deciding on their behalf;
    # empty for QUERY gates, "HUMAN" for skill admissions
    grade: str = ""


@dataclass(frozen=True)
class StreamText:
    """Live stream output from the executing cell; render as it arrives."""

    name: str
    text: str


@dataclass(frozen=True)
class ArtifactSaved:
    """A display artifact (e.g. chart PNG) was written under workspace/."""

    path: str


@dataclass(frozen=True)
class Notice:
    """Loop status line: nudge, cap, kernel_died, restart_offer."""

    kind: str
    text: str


@dataclass(frozen=True)
class CardReady:
    """Terminal event of a turn, carrying the finished answer card."""

    card: "CardLike"


@dataclass(frozen=True)
class GateDecision:
    """Driver's answer to a GateRequest."""

    action: str
    note: str = ""

    def __post_init__(self) -> None:
        if self.action not in ("run", "reject", "skip"):
            raise ValueError(
                'GateDecision.action must be "run", "reject", or "skip", '
                f"got {self.action!r}"
            )


type Event = GateRequest | StreamText | ArtifactSaved | Notice | CardReady


class CardLike(Protocol):
    """What drivers need from an answer card (card.py, hand-written)."""

    def to_markdown(self) -> str: ...


class SessionLike(Protocol):
    """What drivers consume. loop.py (hand-written) implements this."""

    def load(self, path: str, name: str | None = None) -> None: ...

    def run_turn(
        self, question: str
    ) -> Generator[Event, GateDecision | None, None]: ...

    def clean(self, var: str) -> Generator[Event, GateDecision | None, None]: ...

    def clean_family(
        self, pattern: str, name: str
    ) -> Generator[Event, GateDecision | None, None]: ...

    def close(self) -> None: ...
