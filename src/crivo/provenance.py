"""Provenance as a DAG, not a log (P3, after VeriGraph arXiv 2606.16603).

Nodes are raw files, kernel variables, verified fixes, cleaned outputs, and
claims. Edges say what was derived from what. The graph exists to answer one
narrow, load-bearing question:

    a claim is trusted when it is reachable from raw data AND every step on
    that path carried passing checks.

Everything here reads artifacts already on disk — cards, clean reports, their
lineage. Nothing extra is recorded to build the graph, which is the point:
provenance that depends on remembering to log it is provenance you discover you
lack at the worst possible moment.
"""

import json
from pathlib import Path

SOURCE, VARIABLE, FIX, OUTPUT, CLAIM = "source", "variable", "fix", "output", "claim"


def _node(node_id: str, kind: str, label: str, **meta) -> dict:
    return {"id": node_id, "kind": kind, "label": label, **meta}


def build(session_dir) -> dict:
    """Read one session's artifacts into a graph of nodes and edges."""
    session_dir = Path(session_dir)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add(node: dict) -> str:
        nodes.setdefault(node["id"], node)
        return node["id"]

    def link(child: str, parent: str) -> None:
        edge = {"from": child, "to": parent, "rel": "derived_from"}
        if edge not in edges:
            edges.append(edge)

    for report in sorted((session_dir / "clean_reports").glob("*.json")):
        _add_report(json.loads(report.read_text(encoding="utf-8")), add, link)
    for card in sorted((session_dir / "cards").glob("*.json")):
        _add_card(json.loads(card.read_text(encoding="utf-8")), add, link)

    return {"nodes": nodes, "edges": edges, "session": session_dir.name}


def _add_card(card: dict, add, link) -> None:
    checks = card.get("checks", [])
    claim_id = f"claim:{card.get('card_id')}"
    add(
        _node(
            claim_id,
            CLAIM,
            card.get("question", "")[:80],
            answer=card.get("answer", "")[:200],
            checks_passed=bool(checks) and all(c.get("passed") for c in checks),
            check_count=len(checks),
            flags=card.get("flags", {}),
        )
    )
    for dataset in (card.get("lineage") or {}).get("datasets", []):
        variable_id = f"var:{dataset.get('variable')}"
        add(_node(variable_id, VARIABLE, dataset.get("variable", "?")))
        if dataset.get("sha256"):
            remote = dataset.get("remote")
            if remote:
                # R12: a remote object can be overwritten, so the node claims
                # less — trusted as of this fetch, content hash of what
                # actually arrived. A re-fetch to a different hash is a new
                # node, and both are kept.
                source_id = f"remote:{dataset['sha256']}"
                add(
                    _node(
                        source_id,
                        SOURCE,
                        remote.get("uri", dataset.get("path", "?")),
                        sha256=dataset["sha256"],
                        fetched_at=remote.get("fetched_at", ""),
                        checks_passed=True,  # the hash of what arrived is a fact
                    )
                )
            else:
                source_id = f"src:{dataset['sha256']}"
                add(
                    _node(
                        source_id,
                        SOURCE,
                        dataset.get("path", "?"),
                        sha256=dataset["sha256"],
                        checks_passed=True,  # raw bytes are the axiom, not a claim
                    )
                )
            link(variable_id, source_id)
        link(claim_id, variable_id)


def ancestors(dag: dict, node_id: str) -> list[str]:
    """Every node this one was derived from, nearest first."""
    parents: dict[str, list] = {}
    for edge in dag["edges"]:
        parents.setdefault(edge["from"], []).append(edge["to"])
    seen, order, queue = {node_id}, [], list(parents.get(node_id, []))
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        order.append(current)
        queue.extend(parents.get(current, []))
    return order


def trust(dag: dict, claim_id: str) -> dict:
    """Is this claim reachable from raw data with passing checks all the way?

    The verdict carries its reason because "we cannot say where this came from"
    and "we can say, and a step on the way did not hold" are different answers.
    Collapsing them would be the exact mistake the graph exists to prevent.
    """
    nodes = dag["nodes"]
    if claim_id not in nodes:
        return {"trusted": False, "reason": "no such claim", "path": []}
    path = ancestors(dag, claim_id)
    roots = [n for n in path if nodes[n]["kind"] == SOURCE]
    if not roots:
        return {
            "trusted": False,
            "reason": "not reachable from any raw file — nothing grounds this",
            "path": path,
        }
    unproven = [n for n in [claim_id, *path] if nodes[n].get("checks_passed") is False]
    if unproven:
        labels = ", ".join(nodes[n]["label"] for n in unproven[:3])
        return {
            "trusted": False,
            "reason": f"a step on the path did not pass its checks: {labels}",
            "path": path,
            "unproven": unproven,
        }
    return {
        "trusted": True,
        "reason": f"reachable from {len(roots)} raw file(s), checks pass throughout",
        "path": path,
        "roots": roots,
    }


def _add_report(report: dict, add, link) -> None:
    """A clean run is a chain: source -> variable -> fix -> fix -> output.

    Only a *verified* fix advances the chain. A skipped or failed one still
    appears — hiding it would be the lie — but the next step hangs off the last
    thing that actually held.
    """
    var = report.get("variable", "?")
    variable_id = add(_node(f"var:{var}", VARIABLE, var))
    source = report.get("source") or {}
    if source.get("sha256"):
        source_id = add(
            _node(
                f"src:{source['sha256']}",
                SOURCE,
                source.get("path", "?"),
                sha256=source["sha256"],
                checks_passed=True,
            )
        )
        link(variable_id, source_id)

    previous = variable_id
    for i, rec in enumerate(report.get("fixes", []), 1):
        finding = rec.get("finding", {})
        verified = rec.get("status") == "fixed"
        fix_id = add(
            _node(
                f"fix:{report.get('report_id')}:{i}",
                FIX,
                f"d{finding.get('disease', 0):02d} {finding.get('slug', '?')}",
                status=rec.get("status"),
                origin=rec.get("origin", "model"),
                checks_passed=verified,
                events=rec.get("transcript_evs", []),
            )
        )
        link(fix_id, previous)
        if verified:
            previous = fix_id

    outputs = report.get("outputs") or {}
    if outputs.get("parquet"):
        out_id = add(
            _node(
                f"out:{outputs['parquet']}",
                OUTPUT,
                outputs["parquet"],
                checks_passed=True,
            )
        )
        link(out_id, previous)


def to_markdown(dag: dict, claim_id: str | None = None) -> str:
    """The graph as a chain a person can read: every claim, its verdict, and
    the steps behind it marked with whether each one held."""
    nodes = dag["nodes"]
    subjects = [n for n in nodes.values() if n["kind"] in (CLAIM, OUTPUT)]
    if claim_id:
        subjects = [nodes[claim_id]] if claim_id in nodes else []
    lines = [f"## provenance · session {dag.get('session', '?')}", ""]
    if not subjects:
        lines.append("_nothing derived yet_")
    for subject in subjects:
        verdict = trust(dag, subject["id"])
        mark = "✓ trusted" if verdict["trusted"] else "✗ untrusted"
        lines += [
            f"**{subject['label']}**",
            f"- {mark} — {verdict['reason']}",
        ]
        if subject["kind"] == CLAIM:
            lines.append(f"- {subject.get('check_count', 0)} check(s) on the claim")
        for node_id in verdict["path"]:
            node = nodes[node_id]
            state = {True: "✓", False: "✗", None: "·"}[node.get("checks_passed")]
            origin = node.get("origin", "")
            by = f" (by {origin})" if origin.startswith("skill:") else ""
            asof = ""
            if node.get("fetched_at"):
                # R12: remote sources are trusted as of the fetch, not forever
                asof = (
                    f" — as of {node['fetched_at']} · "
                    f"content sha256 {node.get('sha256', '')[:12]}"
                )
            lines.append(f"    {state} {node['kind']}: {node['label']}{by}{asof}")
        lines.append("")
    return "\n".join(lines)
