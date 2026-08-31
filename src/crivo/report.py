"""The clean report: per-run record of what got fixed, skipped, failed,
and flagged (P1 R12-R14). Mirrors the answer card's discipline: every claim
cites transcript event ids."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# aborted != failed: the model never got a turn, the substrate died
STATUS_MARKS = {"fixed": "✓", "skipped": "→", "failed": "✗", "aborted": "⊘"}


@dataclass
class CleanReport:
    report_id: str
    session: str
    variable: str
    source: dict  # {path, sha256} of the original file, when known
    fixes: list = field(default_factory=list)  # fix records, spec R12
    indicators: list = field(default_factory=list)  # findings flagged, not fixed
    clear: list = field(default_factory=list)  # disease ids that ran clean
    broken: dict = field(default_factory=dict)  # ids that could not run, and why
    outputs: dict = field(default_factory=dict)  # {parquet, lineage} paths
    skills_admitted: list = field(default_factory=list)  # skills born here (P2)
    stats: dict = field(default_factory=dict)  # before/after shape + nulls
    event_chain: list = field(default_factory=list)
    created: str = ""

    def counts(self) -> dict:
        out = {"fixed": 0, "skipped": 0, "failed": 0, "aborted": 0}
        for rec in self.fixes:
            out[rec["status"]] = out.get(rec["status"], 0) + 1
        out["flagged"] = len(self.indicators)
        return out

    def to_markdown(self) -> str:
        c = self.counts()
        counts_line = (
            f"**{c['fixed']} fixed · {c['skipped']} skipped · "
            f"{c['failed']} failed · {c['aborted']} not attempted · "
            f"{c['flagged']} flagged** · "
            f"{len(self.clear)} signals clear"
        )
        lines = [
            f"## clean report {self.report_id} — `{self.variable}`",
            "",
            counts_line,
            "",
        ]
        for rec in self.fixes:
            f = rec["finding"]
            mark = STATUS_MARKS.get(rec["status"], "?")
            lines.append(
                f"- {mark} d{f['disease']:02d} {f['slug']} "
                f"[{', '.join(f['columns']) or 'table'}] · {rec['status']} "
                f"({rec['attempts']} attempt{'s' if rec['attempts'] != 1 else ''}) "
                f"· ev {', '.join(str(e) for e in rec['transcript_evs'])}"
            )
        if self.broken:
            lines += ["", "**signals that did not run**"]
            lines += [
                f"- ✗ d{int(d):02d}: {why}" for d, why in sorted(self.broken.items())
            ]
        if self.indicators:
            lines += ["", "**flagged, not fixed (indicators)**"]
            lines += [
                f"- ⚠ d{f['disease']:02d} {f['slug']}: {f['evidence']}"
                for f in self.indicators
            ]
        if self.skills_admitted:
            lines += [
                "",
                "**skills admitted**",
                *[f"- 🌱 {name} (on probation)" for name in self.skills_admitted],
            ]
        if self.stats.get("persisted") is False:
            lines += [
                "",
                (
                    "> ⚠ **not saved** — the kernel died before the cleaned copy "
                    "was written, so any fix marked ✓ above lived only in that "
                    "kernel."
                ),
            ]
        rollup = _per_column(self.fixes)
        if rollup:
            lines += ["", "**per column, what moved**", *rollup]
        if self.outputs:
            lines += [
                "",
                "**outputs**",
                f"- cleaned: {self.outputs.get('parquet')}",
                f"- lineage: {self.outputs.get('lineage')}",
            ]
        if self.stats:
            lines += ["", f"*{json.dumps(self.stats)}*"]
        chain = " → ".join(str(e) for e in self.event_chain)
        lines += ["", f"*events: {chain} · {self.created}*"]
        return "\n".join(lines)

    def save(self, reports_dir: Path) -> Path:
        reports_dir.mkdir(parents=True, exist_ok=True)
        stem = self.report_id.split("-")[-1]
        json_path = reports_dir / f"{stem}.json"
        json_path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (reports_dir / f"{stem}.md").write_text(
            self.to_markdown() + "\n", encoding="utf-8"
        )
        return json_path


def _moved_cells(stats: dict):
    """How many cells a fix moved, from whichever count the finding carries.
    None when the disease's stats expose no cell count — a blank beats a
    guess."""
    for key in ("sentinel_count", "count", "packed", "collapsed"):
        if isinstance(stats.get(key), int):
            return stats[key]
    if "residue_frac" in stats and "values" in stats:
        return round(stats["residue_frac"] * stats["values"])
    return None


def _per_column(fixes: list) -> list[str]:
    """R7: the scannable answer to 'what changed in MY columns'. Only fixes
    that verified moved anything; failed and skipped stay out."""
    moved: dict[str, list[str]] = {}
    for rec in fixes:
        if rec["status"] != "fixed":
            continue
        finding = rec["finding"]
        cells = _moved_cells(finding.get("stats", {}))
        note = f"d{finding['disease']:02d} {finding['slug']}" + (
            f" ({cells} cells)" if cells else ""
        )
        for col in finding.get("columns") or ["(whole table)"]:
            moved.setdefault(col, []).append(note)
    return [f"- `{col}`: " + " · ".join(notes) for col, notes in sorted(moved.items())]
