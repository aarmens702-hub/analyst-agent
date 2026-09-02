"""Report.suggest() — keyless starter questions from schema + findings (P3
table stakes: Genie ships per-space suggested questions; ours must work with
no model key, deterministically, and never leak a cell value)."""

import pandas as pd

import crivo


def _typed_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [f"OR{i:04d}" for i in range(40)],
            "placed_at": pd.date_range("2024-01-01", periods=40, freq="D"),
            "region": (["east", "west", "north", "south"] * 10),
            "amount": [round(10.0 + i, 2) for i in range(40)],
        }
    )


def test_suggest_derives_questions_from_schema_deterministically():
    report = crivo.diagnose(_typed_frame(), name="orders")
    first = report.suggest()
    again = report.suggest()

    assert first == again, "same report, same questions — no set/dict order leaks"
    assert 3 <= len(first) <= 5
    assert len(first) == len(set(first)), "no duplicate questions"
    mentioned = " ".join(first)
    assert "amount" in mentioned and "region" in mentioned
    assert report.suggest(k=3) == first[:3], "k caps, preserving order"


def test_suggest_uses_findings_but_never_leaks_cell_values_and_survives_edges():
    dirty = pd.DataFrame(
        {
            "score": ["12.5", "-999", "8.1", "-999", "3.3"] * 8,
            "team": ["red", "blue"] * 20,
        }
    )
    report = crivo.diagnose(dirty, name="scores")
    assert report.findings, "fixture must actually trip the detector"
    questions = report.suggest()
    assert any("score" in q for q in questions), "the defective column is suggested"
    assert all("-999" not in q for q in questions), "cell values must never leak"
    assert all("12.5" not in q for q in questions)

    # edges: single column and empty frames answer with whatever is derivable,
    # never an exception
    single = crivo.diagnose(pd.DataFrame({"only": [1, 2, 3]}), name="single")
    assert isinstance(single.suggest(), list)
    empty = crivo.diagnose(pd.DataFrame(), name="empty")
    assert empty.suggest() == []
