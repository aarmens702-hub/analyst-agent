"""The answer card: build, check-lifting, render, persist (spec §3, R18-R20).

Checks are never self-reported: they are lifted by AST from the final cell
that executed with status "ok" — code that ran is the only source of a ✓.
"""

import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class AnswerCard:
    card_id: str
    session: str
    question: str
    answer: str
    cells: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    lineage: dict = field(default_factory=dict)
    model: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    intent: dict = field(default_factory=dict)  # P3 intent gate verdict
    created: str = ""

    @property
    def images(self) -> list:
        """Chart/image paths the kernel saved while this card's cells ran —
        aggregated from the per-cell records (P3 §5): a chart drawn on the
        way to an answer is part of the answer, not an orphan file."""
        out: list = []
        for cell in self.cells:
            for path in cell.get("display_paths") or ():
                if path not in out:
                    out.append(path)
        return out

    def to_markdown(self) -> str:
        lines = [f"## answer card {self.card_id}", "", f"**Q:** {self.question}", ""]
        lines += [self.answer.strip(), ""]

        lines.append("**checks**")
        if self.checks:
            lines += [f"- ✓ `{c['expr']}`" for c in self.checks]
        else:
            lines.append("- (none — see flags)")
        lines.append("")

        # A mismatch belongs above the fold, next to the answer it undermines:
        # every check can pass while the code answers a different question.
        if self.intent.get("verdict") == "mismatch":
            lines += [
                "> ⚠ **the code may not answer the question asked**",
                f"> it computed: {self.intent.get('restatement', '')}",
                f"> {self.intent.get('reason', '')}",
                "",
            ]

        lines.append("**cells**")
        for cell in self.cells:
            gate = cell.get("gate")
            gate_txt = (
                f'rejected — "{gate["rejected"]}"' if isinstance(gate, dict) else gate
            )
            status = cell.get("status") or "-"
            lines.append(f"- ev {cell['event_id']} · {gate_txt} · {status}")
        final = self._final_ok_cell()
        if final:
            lines += ["", "```python", final["code"].strip(), "```"]
        lines.append("")

        lines.append("**lineage**")
        for ds in self.lineage.get("datasets", []):
            sha = ds.get("sha256", "")[:12]
            lines.append(
                f"- {ds['path']} `sha256 {sha}…` → {ds['variable']} "
                f"(ev {ds['loaded_event']})"
            )
        chain = " → ".join(str(e) for e in self.lineage.get("event_chain", []))
        lines.append(f"- events: {chain}")
        lines.append("")

        flags = [k for k, v in self.flags.items() if v]
        meta = (
            f"{self.model.get('model', '?')} · temp {self.model.get('temperature')} · "
            f"{len([c for c in self.cells if c.get('status')])} cells run · "
            f"{len(self.checks)} checks passed · {self.created}"
        )
        if flags:
            meta += f" · flags: {', '.join(flags)}"
        lines.append(f"*{meta}*")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def save(self, cards_dir: Path) -> Path:
        cards_dir.mkdir(parents=True, exist_ok=True)
        stem = self.card_id.split("-")[-1]
        json_path = cards_dir / f"{stem}.json"
        json_path.write_text(self.to_json() + "\n", encoding="utf-8")
        (cards_dir / f"{stem}.md").write_text(
            self.to_markdown() + "\n", encoding="utf-8"
        )
        return json_path

    def _final_ok_cell(self) -> dict | None:
        for cell in reversed(self.cells):
            if cell.get("status") == "ok":
                return cell
        return None


def lift_checks(code: str) -> list[dict]:
    """Extract assert expressions from a cell that ran to status ok (R18).

    If the cell executed cleanly, every assert in it passed by definition.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    return [
        {"expr": ast.unparse(node.test), "passed": True}
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
    ]
