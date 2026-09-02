"""FIXERS[18] — the header fixer (bulletproof-core arc, Wave 1 R2).

Damaged column NAMES are the one d18 problem a deterministic fixer can repair
without deleting rows: BOM/zero-width residue, "Unnamed: N" placeholders, and
duplicate names. Header-repeat data rows stay gated (row deletion is a
judgement call), so a frame with those honestly fails verification instead of
being half-fixed.
"""

import pandas as pd

from crivo.autoclean import clean


def test_clean_repairs_unnamed_duplicate_and_bom_headers():
    frame = pd.DataFrame(
        {
            "﻿amount": [1.0, 2.0, 3.0] * 8,
            "Unnamed: 1": ["a", "b", "c"] * 8,
            "region": ["e", "w", "n"] * 8,
        }
    )
    # a duplicate name needs positional construction — pandas collapses dict keys
    frame.columns = ["﻿amount", "Unnamed: 1", "﻿amount"]

    cleaned, summary = clean(frame)

    assert 18 in {a["disease"] for a in summary.applied}, summary.needs_review
    names = [str(c) for c in cleaned.columns]
    assert all("﻿" not in n for n in names), names
    assert not any(n.startswith("Unnamed:") for n in names), names
    assert len(names) == len(set(names)), f"duplicates survived: {names}"
    assert len(cleaned.columns) == 3  # renamed, never dropped

    healthy = pd.DataFrame({"amount": [1.0, 2.0] * 8, "region": ["e", "w"] * 8})
    _, healthy_summary = clean(healthy)
    assert 18 not in {a["disease"] for a in healthy_summary.applied}


def test_header_fix_receipts_show_renames_not_removals():
    """A rename must never masquerade as a drop in the receipts: the d19
    'removed' branch fires on any old name absent from `after`, which is
    exactly what a renamed column looks like — the d18 receipt path has to
    say old -> new instead."""
    frame = pd.DataFrame({"Unnamed: 0": [1.0, 2.0] * 8, "region": ["e", "w"] * 8})
    _, summary = clean(frame)
    (receipt,) = [r for r in summary.samples() if r["disease"] == 18]
    assert receipt["examples"], "renames must appear in the receipts"
    example = receipt["examples"][0]
    assert example.get("renamed") is True
    assert example["column"] == "Unnamed: 0"
    assert example["new"] == "column_0"
    assert not example.get("removed"), "a rename is not a removal"


def test_header_fix_composes_with_drops_and_is_idempotent_and_pure():
    """d19 runs before d18 in _ORDER, so a clean can both DROP a column and
    RENAME others — the receipt pairing must survive the position shift. And
    like every fixer: running clean twice changes nothing more, and the
    caller's frame is never mutated."""
    # the dropped column deliberately comes FIRST: a naive vanished/appeared
    # pairing would match "dead" to the renamed column's new name
    frame = pd.DataFrame(
        {
            "dead": ["x"] * 20,  # constant column, d19 drops it
            "Unnamed: 1": [1.0, 2.0] * 10,
            "region": ["e", "w"] * 10,
        }
    )
    original_columns = list(frame.columns)

    cleaned, summary = clean(frame)
    applied = {a["disease"] for a in summary.applied}
    assert 18 in applied and 19 in applied, summary.needs_review
    (receipt,) = [r for r in summary.samples() if r["disease"] == 18]
    # d19 drops "dead" BEFORE d18 runs (_ORDER), so the Unnamed column sits
    # at position 0 of the frame being fixed — hence column_0, not column_1
    assert receipt["examples"] == [
        {"column": "Unnamed: 1", "renamed": True, "new": "column_0"}
    ], receipt["examples"]

    again, second = clean(cleaned)
    assert 18 not in {a["disease"] for a in second.applied}
    assert list(again.columns) == list(cleaned.columns)
    assert list(frame.columns) == original_columns, "input frame was mutated"
