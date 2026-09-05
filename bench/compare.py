"""Compare two agent-bench result directories (T1.5 comparison table).

`uv run python -m bench.compare DIR_A DIR_B` matches per-case result JSONs
(the bench/results/agent shape) by filename and prints an aligned table:
status, repair F1, wall clock with delta, model calls, and new-work tokens
A -> B, then a means row over cases scored in both, then unmatched cases.
Pure stdlib, no pandas. A report, not a gate: it always exits 0
(spec: specs/2026-09-04-a1-build-plan.md T1.5).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HEADER = ("case", "status", "repair F1", "wall_secs", "calls", "new_work_tokens")


def _load(path: Path) -> dict[str, dict]:
    """Per-case rows keyed by filename stem, so a ceiling arm (name.ceiling)
    never pairs with the governed arm of the same case."""
    return {
        p.name.removesuffix(".json"): json.loads(p.read_text())
        for p in sorted(path.glob("*.json"))
    }


def _mean(values: list) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _scored(row: dict) -> bool:
    """Same definition as the lane's aggregate: ok status with scores."""
    return row.get("status") == "ok" and bool(row.get("scores"))


def _f1(row: dict) -> float | None:
    return ((row.get("scores") or {}).get("repair") or {}).get("f1")


def _calls(row: dict, key: str) -> float | None:
    return (row.get("calls") or {}).get(key)


def _head(row: dict) -> str:
    """Status summary: the part before any detail ('aborted: wall cap ...'
    becomes 'aborted') so the column stays table-width."""
    return str(row.get("status") or "?").split(":")[0]


def _fmt(value: float | None, nd: int) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{nd}f}"
    return str(value)


def _pair(a: float | None, b: float | None, nd: int = 2) -> str:
    return f"{_fmt(a, nd)} -> {_fmt(b, nd)}"


def _pair_delta(a: float | None, b: float | None, nd: int = 1) -> str:
    cell = _pair(a, b, nd)
    if a is not None and b is not None:
        cell += f" ({b - a:+.{nd}f})"
    return cell


def _case_row(name: str, ra: dict, rb: dict) -> tuple[str, ...]:
    return (
        name,
        f"{_head(ra)} -> {_head(rb)}",
        _pair(_f1(ra), _f1(rb)),
        _pair_delta(ra.get("wall_secs"), rb.get("wall_secs")),
        _pair(_calls(ra, "count"), _calls(rb, "count")),
        _pair(_calls(ra, "new_work_tokens"), _calls(rb, "new_work_tokens")),
    )


def _means_row(pairs: list[tuple[dict, dict]]) -> tuple[str, ...]:
    a_rows = [p[0] for p in pairs]
    b_rows = [p[1] for p in pairs]

    def side_means(pick):
        return _mean([pick(r) for r in a_rows]), _mean([pick(r) for r in b_rows])

    return (
        f"means ({len(pairs)} scored in both)",
        "-",
        _pair(*side_means(_f1)),
        _pair_delta(*side_means(lambda r: r.get("wall_secs"))),
        _pair(*side_means(lambda r: _calls(r, "count")), nd=1),
        _pair(*side_means(lambda r: _calls(r, "new_work_tokens")), nd=0),
    )


def _print_table(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(row[i]) for row in rows) for i in range(len(HEADER))]
    for row in rows:
        line = "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True))
        print(line.rstrip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two agent-bench result directories (T1.5)"
    )
    parser.add_argument("dir_a", type=Path)
    parser.add_argument("dir_b", type=Path)
    args = parser.parse_args(argv)

    a, b = _load(args.dir_a), _load(args.dir_b)
    matched = sorted(set(a) & set(b))
    rows = [HEADER, *(_case_row(name, a[name], b[name]) for name in matched)]
    both = [(a[n], b[n]) for n in matched if _scored(a[n]) and _scored(b[n])]
    if both:
        rows.append(_means_row(both))
    _print_table(rows)
    for label, extra in (
        ("A", sorted(set(a) - set(b))),
        ("B", sorted(set(b) - set(a))),
    ):
        if extra:
            print(f"only in {label}: {', '.join(extra)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
