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


_TOKEN_ATTRS = (
    ("input_tokens", "gen_ai.usage.input_tokens"),
    ("output_tokens", "gen_ai.usage.output_tokens"),
    ("cache_hit_tokens", "crivo.cache.hit_tokens"),
    ("cache_miss_tokens", "crivo.cache.miss_tokens"),
)


def _call_stats(path: Path) -> dict:
    """Sum a case's gen_ai.client.call spans into the T1.5 columns: calls,
    model wait, token usage, and new_work_tokens (input minus cache hits
    plus output, the cost basis that excludes cache reads). A missing file,
    junk line, or absent field degrades to zeros/absent keys, never raises:
    telemetry must not be able to fail a case."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    count = 0
    wait = 0.0
    sums: dict = {}
    for line in lines:
        try:
            span = json.loads(line)
        except ValueError:
            continue
        if not isinstance(span, dict) or span.get("name") != "gen_ai.client.call":
            continue
        count += 1
        dur = span.get("dur_s")
        if isinstance(dur, (int, float)):
            wait += dur
        attrs = span.get("attrs") or {}
        for key, attr in _TOKEN_ATTRS:
            value = attrs.get(attr)
            if isinstance(value, (int, float)):
                sums[key] = sums.get(key, 0) + value
    stats = {"count": count, "model_wait_s": round(wait, 2), **sums}
    stats["new_work_tokens"] = (
        sums.get("input_tokens", 0)
        - sums.get("cache_hit_tokens", 0)
        + sums.get("output_tokens", 0)
    )
    return stats


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
    # T1.5: per-case telemetry file, per arm; the generate() calls run in
    # this host process and read CRIVO_TELEMETRY per call (crivo/telemetry.py)
    suffix = ".ceiling" if args.human_gates == "approve" else ""
    tele_path = RESULTS_DIR / "telemetry" / f"{name}{suffix}.jsonl"
    tele_path.parent.mkdir(parents=True, exist_ok=True)
    tele_path.unlink(missing_ok=True)  # stale spans must not count in this run
    prev_tele = os.environ.get("CRIVO_TELEMETRY")
    os.environ["CRIVO_TELEMETRY"] = str(tele_path)
    try:
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
    finally:
        # restore, never leak: the next case (and the test suite) must see
        # the environment it started with
        if prev_tele is None:
            os.environ.pop("CRIVO_TELEMETRY", None)
        else:
            os.environ["CRIVO_TELEMETRY"] = prev_tele
    row["calls"] = _call_stats(tele_path)
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
        calls = row.get("calls") or {}
        print(
            f"{row['name']}: {row['status']}"
            f" | repair F1 {repair.get('f1')}"
            f" | {row['wall_secs']}s | {row.get('events', 0)} events"
            f" | {calls.get('count', 0)} calls"
            f" | {calls.get('new_work_tokens', 0)} new-work tok"
        )
        rows.append(row)

    ok = [r for r in rows if r.get("status") == "ok" and r.get("scores")]
    ok_calls = [r.get("calls") or {} for r in ok]
    print(
        f"\nagent lane: {len(ok)}/{len(rows)} scored"
        f" | repair F1 mean {_mean([r['scores']['repair'].get('f1') for r in ok])}"
        f" | repair recall mean "
        f"{_mean([r['scores']['repair'].get('recall') for r in ok])}"
        f" | calls mean {_mean([c.get('count') for c in ok_calls])}"
        f" | new-work tokens mean "
        f"{_mean([c.get('new_work_tokens') for c in ok_calls])}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
