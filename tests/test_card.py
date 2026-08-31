"""Card tests: AST check-lifting, markdown render, persistence (spec §3)."""

import json

from crivo.card import AnswerCard, lift_checks


def test_lift_checks_extracts_assert_expressions():
    code = (
        "result = df.groupby('y')['v'].median()\n"
        "assert len(result) > 0\n"
        "assert result.between(0, 1e6).all()\n"
        "result"
    )
    assert lift_checks(code) == [
        {"expr": "len(result) > 0", "passed": True},
        {"expr": "result.between(0, 1000000.0).all()", "passed": True},
    ]


def test_lift_checks_handles_no_asserts_and_bad_syntax():
    assert lift_checks("x = 1") == []
    assert lift_checks("def broken(:") == []


def _card():
    return AnswerCard(
        card_id="s01-c001",
        session="s01",
        question="which district grew most?",
        answer="RS-1 grew the most.",
        cells=[
            {
                "event_id": 4,
                "exec_count": None,
                "code": "df.mean()",
                "status": None,
                "gate": {"rejected": "medians, not means"},
                "value_preview": None,
                "display_paths": [],
            },
            {
                "event_id": 6,
                "exec_count": 2,
                "code": "result = 1\nassert result == 1\nresult",
                "status": "ok",
                "gate": "run",
                "value_preview": "1",
                "display_paths": [],
            },
        ],
        checks=[{"expr": "result == 1", "passed": True}],
        lineage={
            "datasets": [
                {
                    "path": "data/t.csv",
                    "sha256": "abc123def4567890",
                    "variable": "df",
                    "loaded_event": 2,
                }
            ],
            "event_chain": [3, 6, 7],
        },
        model={"provider": "deepseek", "model": "deepseek-v4-pro", "temperature": 0.0},
        flags={"capped": False, "malformed_answer": False, "truncated": False},
        created="2026-08-11T12:00:00-07:00",
    )


def test_markdown_render_contains_the_contract():
    md = _card().to_markdown()
    assert "**Q:** which district grew most?" in md
    assert "RS-1 grew the most." in md
    assert "✓ `result == 1`" in md
    assert 'rejected — "medians, not means"' in md
    assert "sha256 abc123def456…" in md
    assert "3 → 6 → 7" in md
    assert "```python" in md and "assert result == 1" in md


def test_save_writes_json_and_md(tmp_path):
    card = _card()
    json_path = card.save(tmp_path / "cards")
    assert json_path.name == "c001.json"
    data = json.loads(json_path.read_text())
    assert data["card_id"] == "s01-c001"
    assert data["checks"][0]["expr"] == "result == 1"
    assert (tmp_path / "cards" / "c001.md").exists()


def test_capped_flag_appears_in_meta_line():
    card = _card()
    card.flags["capped"] = True
    assert "flags: capped" in card.to_markdown()


def test_an_intent_mismatch_appears_next_to_the_answer_it_undermines() -> None:
    """Every check can pass while the code answers a different question, so the
    warning belongs above the fold, not buried in a flags dict."""
    card = AnswerCard(
        card_id="s01-c001",
        session="s01",
        question="what is the total of column a?",
        answer="the total is 42",
        checks=[{"expr": "result > 0", "passed": True}],
        intent={
            "verdict": "mismatch",
            "restatement": "the code summed column b",
            "reason": "the question asked for column a",
        },
    )
    md = card.to_markdown()
    assert "may not answer the question asked" in md
    assert "the code summed column b" in md
    assert md.index("summed column b") < md.index("**cells**")
