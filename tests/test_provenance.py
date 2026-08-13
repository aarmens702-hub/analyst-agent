"""Tests for the provenance DAG (P3, after VeriGraph arXiv 2606.16603).

The graph exists to answer one question: is this claim reachable from raw data
with passing checks the whole way? So the tests are mostly about the ways that
can be false — an unreachable claim and a claim standing on an unverified step
are both untrusted, for different reasons, and the reasons must not collapse.
"""

import json
from pathlib import Path

from analyst_agent import provenance


def write_card(session: Path, card_id: str, checks, datasets) -> None:
    (session / "cards").mkdir(parents=True, exist_ok=True)
    (session / "cards" / f"{card_id}.json").write_text(
        json.dumps(
            {
                "card_id": card_id,
                "question": "which brewery has the most beers?",
                "answer": "Brewery Vivant, 62",
                "checks": checks,
                "lineage": {"datasets": datasets},
                "flags": {},
            }
        )
    )


def test_a_card_becomes_a_claim_grounded_in_its_source(tmp_path) -> None:
    session = tmp_path / "s01"
    write_card(
        session,
        "s01-c001",
        [{"expr": "len(result) == 3", "passed": True}],
        [{"path": "data/beers.csv", "sha256": "abc123", "variable": "beers"}],
    )
    dag = provenance.build(session)

    kinds = {n["kind"] for n in dag["nodes"].values()}
    assert kinds == {"claim", "variable", "source"}
    assert {"from": "var:beers", "to": "src:abc123", "rel": "derived_from"} in dag[
        "edges"
    ]


def test_a_grounded_claim_with_passing_checks_is_trusted(tmp_path) -> None:
    session = tmp_path / "s01"
    write_card(
        session,
        "s01-c001",
        [{"expr": "len(result) == 3", "passed": True}],
        [{"path": "data/beers.csv", "sha256": "abc123", "variable": "beers"}],
    )
    verdict = provenance.trust(provenance.build(session), "claim:s01-c001")

    assert verdict["trusted"] is True
    assert "raw file" in verdict["reason"]


def test_an_ungrounded_claim_and_a_failed_one_are_untrusted_differently(
    tmp_path,
) -> None:
    """Both are untrusted; the reasons must not collapse. "nobody can say where
    this came from" and "somebody can, and it did not hold" are different."""
    session = tmp_path / "s01"
    write_card(session, "s01-c001", [{"expr": "x", "passed": True}], [])
    write_card(
        session,
        "s01-c002",
        [{"expr": "len(result) == 3", "passed": False}],
        [{"path": "data/beers.csv", "sha256": "abc123", "variable": "beers"}],
    )
    dag = provenance.build(session)

    ungrounded = provenance.trust(dag, "claim:s01-c001")
    assert ungrounded["trusted"] is False
    assert "not reachable" in ungrounded["reason"]

    failed = provenance.trust(dag, "claim:s01-c002")
    assert failed["trusted"] is False
    assert "did not pass its checks" in failed["reason"]


def write_report(session: Path, statuses) -> None:
    (session / "clean_reports").mkdir(parents=True, exist_ok=True)
    (session / "clean_reports" / "r001.json").write_text(
        json.dumps(
            {
                "report_id": "s01-r001",
                "variable": "beers",
                "source": {"path": "data/beers.csv", "sha256": "abc123"},
                "fixes": [
                    {
                        "finding": {"disease": 4, "slug": "sentinel-missing"},
                        "status": status,
                        "origin": "model",
                        "transcript_evs": [7],
                    }
                    for status in statuses
                ],
                "outputs": {"parquet": "workspace/s01/cleaned/beers.parquet"},
            }
        )
    )


def test_a_cleaned_output_carries_the_chain_of_fixes_behind_it(tmp_path) -> None:
    session = tmp_path / "s01"
    write_report(session, ["fixed", "fixed"])
    dag = provenance.build(session)

    out = "out:workspace/s01/cleaned/beers.parquet"
    chain = provenance.ancestors(dag, out)
    assert sum(1 for n in chain if dag["nodes"][n]["kind"] == "fix") == 2
    assert any(dag["nodes"][n]["kind"] == "source" for n in chain)
    assert provenance.trust(dag, out)["trusted"] is True


def test_a_failed_fix_is_visible_but_does_not_taint_the_output(tmp_path) -> None:
    """A failed fix was reverted, so the output genuinely does not contain it.
    The chain hangs off the last step that actually held — but the failed
    attempt still appears in the graph, because hiding it would be the lie."""
    session = tmp_path / "s01"
    write_report(session, ["fixed", "failed"])
    dag = provenance.build(session)

    out = "out:workspace/s01/cleaned/beers.parquet"
    assert provenance.trust(dag, out)["trusted"] is True
    assert out not in provenance.ancestors(dag, "fix:s01-r001:2")

    failed = [n for n in dag["nodes"].values() if n.get("status") == "failed"]
    assert len(failed) == 1, "the attempt is recorded even though it was reverted"
    assert failed[0]["checks_passed"] is False


def test_to_markdown_reads_as_a_chain_a_person_can_follow(tmp_path) -> None:
    session = tmp_path / "s01"
    write_card(
        session,
        "s01-c001",
        [{"expr": "len(result) == 3", "passed": True}],
        [{"path": "data/beers.csv", "sha256": "abc123", "variable": "beers"}],
    )
    text = provenance.to_markdown(provenance.build(session))

    assert "✓ trusted" in text
    assert "which brewery has the most beers?" in text
    assert "source: data/beers.csv" in text
