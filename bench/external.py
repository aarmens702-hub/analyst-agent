"""Adapter from the Raha clean/dirty pairs into the bench's GroundTruth (spec R4).

Raha convention: every value is text, so a clean/dirty diff is exact string
inequality — no type-aware equivalence (that lives in bench/score.py for the
synthetic corpus, where disease is known; here disease is not, so cell-diff
is the whole truth).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from bench.truth import Cell, Corruption, GroundTruth, frame_sha256


def load_pair(
    name: str, root: Path = Path("data/external/raha")
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(clean, dirty) for a fetched Raha dataset, both all-string (Raha's own
    convention: no type coercion, blank cell stays "" rather than becoming NaN)."""
    clean_path = root / name / "clean.csv"
    dirty_path = root / name / "dirty.csv"
    if not clean_path.exists() or not dirty_path.exists():
        raise FileNotFoundError(
            f"{name}: no clean/dirty pair under {root} — run scripts/fetch_raha.py to fetch it"
        )
    clean = pd.read_csv(clean_path, dtype=str, keep_default_na=False)
    dirty = pd.read_csv(dirty_path, dtype=str, keep_default_na=False)
    return clean, dirty


def truth_from_pair(clean: pd.DataFrame, dirty: pd.DataFrame, name: str) -> GroundTruth:
    """Cell-diff a Raha pair into a GroundTruth: one Corruption per changed
    column, disease=0 (external/unknown taxonomy — Raha doesn't label diseases)."""
    if clean.shape != dirty.shape:
        raise ValueError(
            f"{name}: clean/dirty shape mismatch {clean.shape} vs {dirty.shape}"
        )

    corruptions = []
    if list(clean.columns) != list(dirty.columns):
        # Raha reality: hospital renames every header, beers renames two —
        # same order, same count. The rename is itself dirt: record each
        # renamed header as a column-granular corruption, then align
        # positionally so the cell-diff can proceed.
        for ours, theirs in zip(clean.columns, dirty.columns, strict=True):
            if ours != theirs:
                corruptions.append(
                    Corruption(
                        disease=0,
                        columns=(ours,),
                        granularity="column",
                        note=f"{name}: dirty header {theirs!r}",
                    )
                )
        dirty = dirty.set_axis(clean.columns, axis=1)
    for column in clean.columns:
        differs = clean[column].to_numpy() != dirty[column].to_numpy()
        if not differs.any():
            continue
        cells = tuple(
            Cell(
                row=int(row),
                column=column,
                original=clean[column].iat[row],
                corrupted=dirty[column].iat[row],
            )
            for row in differs.nonzero()[0]
        )
        corruptions.append(
            Corruption(
                disease=0, columns=(column,), granularity="cell", cells=cells, note=name
            )
        )

    return GroundTruth(
        seed=0,
        base="external",
        n_rows=len(clean),
        n_cols=len(clean.columns),
        frame_sha256=frame_sha256(dirty),
        corruptions=corruptions,
    )


def load_scored_pair(
    name: str, root: Path = Path("data/external/raha")
) -> tuple[pd.DataFrame, pd.DataFrame, GroundTruth]:
    """The one-call entry for scoring a fetched dataset: (clean, ALIGNED
    dirty, truth). The returned dirty carries the clean header names and is
    exactly the frame the manifest hash pins — hand it straight to
    score_pair, which scores external pairs in string space."""
    clean, dirty = load_pair(name, root)
    truth = truth_from_pair(clean, dirty, name)
    if list(clean.columns) != list(dirty.columns):
        dirty = dirty.set_axis(clean.columns, axis=1)
    return clean, dirty, truth
