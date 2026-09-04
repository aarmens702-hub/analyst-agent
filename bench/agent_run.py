"""Agent-mode bench lane (spec: specs/2026-09-03-agent-bench-design.md).

Drives the real `Session.clean` loop headlessly over the same corpus as the
deterministic bench, then scores the cleaned frame with the same
`score_end_to_end`. Plain gates are auto-approved; HUMAN gates (skill
admissions, judgement calls) are skipped, never fake-approved. Sampled runs
print to the terminal only; storefront numbers come from a full, blessed
run (R6).

`uv run python -m bench.agent_run --sample 2`
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from bench import corpus
from bench.score import score_end_to_end
from crivo import llm

KEY_VARS = ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY")
RESULTS_DIR = Path("bench/results/agent")


class CaseAborted(Exception):
    """A cap fired; the case is recorded and the run moves on (R4)."""


def _load_dotenv(path: Path = Path(".env")) -> None:
    """The project's own .env, no dependency: KEY=VALUE lines, no expansion."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def _require_key() -> None:
    if not any(os.environ.get(v) for v in KEY_VARS):
        sys.exit(
            "agent bench needs a model key; none of "
            f"{', '.join(KEY_VARS)} set (checked env and .env) — R7"
        )


def _drive(
    gen,
    max_events: int,
    wall_cap: float,
    t0: float,
    gates: list | None = None,
    human_gates: str = "skip",
) -> int:
    """repl._drive, headless: run plain gates, swallow the rendering.

    HUMAN gates default to skip — a person's authorisation is not the bench's
    to give. `human_gates="approve"` applies the owner's standing
    pre-authorisation to judgement-call fixes (the ceiling arm), but a skill
    admission (title "admit skill …") is governance and stays skipped in
    every mode."""
    from crivo.events import GateDecision, GateRequest

    events = 0
    try:
        event = next(gen)
        while True:
            events += 1
            if events > max_events:
                gen.close()
                raise CaseAborted(f"event cap {max_events} hit")
            if time.monotonic() - t0 > wall_cap:
                gen.close()
                raise CaseAborted(f"wall cap {wall_cap:.0f}s hit")
            answer = None
            if isinstance(event, GateRequest):
                approved = human_gates == "approve" and not event.title.startswith(
                    "admit skill "
                )
                action = "run" if event.grade != "HUMAN" or approved else "skip"
                answer = GateDecision(action)
                if gates is not None:
                    gates.append(action)
            event = gen.send(answer)
    except StopIteration:
        return events


def _handoff(dirty: pd.DataFrame, target: Path) -> str:
    """Dtype-exact parquet (R2); CSV only when arrow refuses, and recorded."""
    try:
        dirty.to_parquet(target.with_suffix(".parquet"))
        return "parquet"
    except Exception:  # noqa: BLE001 — whatever arrow refuses, CSV must carry
        dirty.to_csv(target.with_suffix(".csv"), index=False)
        return "csv"


def _run_case(entry: dict, args: argparse.Namespace) -> dict:
    from crivo.loop import Session

    name = entry["name"]
    pristine, dirty, truth = corpus.build(entry)
    work = RESULTS_DIR / "work" / name
    if work.exists():
        shutil.rmtree(work)
    (work / "data").mkdir(parents=True)
    fmt = _handoff(dirty, work / "data" / name)
    dirty_path = next((work / "data").glob(f"{name}.*"))

    row: dict = {
        "name": name,
        "diseases": entry["diseases"],
        "date": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "handoff": fmt,
        "human_gates": args.human_gates,
        "model": llm.model_info(),
        "status": "ok",
    }
    t0 = time.monotonic()
    gates: list = []
    session = Session(
        workspace=str(work / "ws"),
        data_dir=str(work / "data"),
        docker=args.docker,
        skills_dir=str(work / "skills"),
        preview=False,
        snapshots=False,
    )
    try:
        session.load(str(dirty_path), "df")
        row["events"] = _drive(
            session.clean("df"),
            args.max_events,
            args.wall_cap,
            t0,
            gates=gates,
            human_gates=args.human_gates,
        )
        cleaned_path = Path(session.session_dir) / "cleaned" / "df.parquet"
        if cleaned_path.exists():
            cleaned = pd.read_parquet(cleaned_path)
            row["scores"] = score_end_to_end(pristine, dirty, cleaned, truth)
        else:
            row["status"] = "no_cleaned_output"
    except CaseAborted as exc:
        row["status"] = f"aborted: {exc}"
    except Exception as exc:  # noqa: BLE001 — one broken case must not kill the run (R4)
        row["status"] = f"error: {type(exc).__name__}: {exc}"
    finally:
        session.close()
    row["gates"] = {"run": gates.count("run"), "skip": gates.count("skip")}
    row["wall_secs"] = round(time.monotonic() - t0, 1)
    return row


def _mean(values: list) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 4) if present else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent-mode Proving Ground lane")
    parser.add_argument("--sample", type=int, default=3, help="cases to run")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--full", action="store_true", help="seeds-expanded corpus")
    parser.add_argument("--force", action="store_true", help="rerun finished cases")
    parser.add_argument("--docker", action="store_true", help="sandbox kernel")
    parser.add_argument("--max-events", type=int, default=8000)
    parser.add_argument("--wall-cap", type=float, default=300.0, help="secs/case")
    parser.add_argument(
        "--human-gates",
        choices=("skip", "approve"),
        default="skip",
        help="approve = owner pre-authorises judgement-call fixes (ceiling arm); "
        "skill admissions are skipped in every mode",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated case names; restricts the sampled set",
    )
    args = parser.parse_args(argv)

    _load_dotenv()
    _require_key()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    entries = corpus.full_corpus() if args.full else list(corpus.SMOKE)
    picked = random.Random(args.seed).sample(entries, min(args.sample, len(entries)))
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        picked = [e for e in picked if e["name"] in wanted]

    suffix = ".ceiling" if args.human_gates == "approve" else ""
    rows = []
    for entry in picked:
        out = RESULTS_DIR / f"{entry['name']}{suffix}.json"
        if out.exists() and not args.force:
            rows.append(json.loads(out.read_text()))
            print(f"{entry['name']}: already on disk, skipped (R5)")
            continue
        row = _run_case(entry, args)
        out.write_text(json.dumps(row, indent=2, default=str))
        repair = (row.get("scores") or {}).get("repair", {})
        print(
            f"{row['name']}: {row['status']}"
            f" | repair F1 {repair.get('f1')}"
            f" | {row['wall_secs']}s | {row.get('events', 0)} events"
        )
        rows.append(row)

    ok = [r for r in rows if r.get("status") == "ok" and r.get("scores")]
    print(
        f"\nagent lane: {len(ok)}/{len(rows)} scored"
        f" | repair F1 mean {_mean([r['scores']['repair'].get('f1') for r in ok])}"
        f" | repair recall mean "
        f"{_mean([r['scores']['repair'].get('recall') for r in ok])}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
