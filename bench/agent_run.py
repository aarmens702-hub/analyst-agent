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
from crivo.autoclean import FIXERS
from crivo.policy import PolicyRecord

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
# the terms new_work_tokens ADDS. A call that reported neither reported no
# cost basis: cache counters alone cannot say what new work a call did, and
# subtracting a hit count from nothing would publish a negative token count.
_NEW_WORK_ATTRS = ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")


def _call_stats(path: Path) -> dict:
    """Sum a case's gen_ai.client.call spans into the T1.5 columns: calls,
    model wait, token usage, and new_work_tokens (input minus cache hits
    plus output, the cost basis that excludes cache reads). A missing file,
    junk line, or absent field degrades to zeros/absent keys, never raises:
    telemetry must not be able to fail a case.

    new_work_tokens separates three states rather than two, because 0 and
    None each mean something and the wrong one flatters a cost column:
      - no readable telemetry -> None. We do not know whether a call was made.
      - readable, no call span -> 0. The deterministic path solved the case;
        no call was made, so nothing was spent. Calling this unknown drops
        real, free cases out of the mean and leaves it several times high.
      - a call span that reported no usage -> None for the whole case. The
        span is emitted from telemetry.span's `finally` while llm.generate
        maps usage only after the stream finishes, so a cancelled or failed
        call leaves one behind. Summing the rest would publish a total that
        is knowably short as if it were complete.
    """
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return {"count": 0, "model_wait_s": 0.0, "new_work_tokens": None}
    count = 0
    unpriced = 0
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
        if not any(isinstance(attrs.get(a), (int, float)) for a in _NEW_WORK_ATTRS):
            unpriced += 1
        for key, attr in _TOKEN_ATTRS:
            value = attrs.get(attr)
            if isinstance(value, (int, float)):
                sums[key] = sums.get(key, 0) + value
    stats = {"count": count, "model_wait_s": round(wait, 2), **sums}
    if unpriced:
        stats["new_work_tokens"] = None  # a call of unknown cost is in there
    else:
        stats["new_work_tokens"] = (
            sums.get("input_tokens", 0)
            - sums.get("cache_hit_tokens", 0)
            + sums.get("output_tokens", 0)
        )
    return stats


def _arm_gates(args: argparse.Namespace) -> str:
    """The gate policy this arm actually drives with.

    An autonomy arm is always driven with HUMAN gates skipped. `approve` runs
    every non-admission HUMAN gate, which is a person's authorisation to give,
    and an autonomy arm's reach is meant to come from the Session's seeded
    AUTO policy alone. `main` refuses the combination at the parser and says
    so; this is the same rule at the point of use, because `_arm_suffix` and
    `_run_case` are called with hand-built namespaces that never pass through
    that parser. Reads knobs defensively for the same reason.
    """
    gates = getattr(args, "human_gates", "skip")
    if gates == "approve" and getattr(args, "autonomy", None):
        return "skip"
    return gates


def _arm_suffix(args: argparse.Namespace) -> str:
    """The filename tag naming this arm, for both the result and the telemetry
    file. Every knob that changes what an arm measures has to appear here: the
    result name is also the resume key (`main` reuses a case whose file exists,
    R5), so an arm missing from the name reads the other arm's row and reports
    its numbers as its own. The default arm keeps the empty suffix and the
    ceiling arm keeps `.ceiling`, so the results already on disk stay
    addressable. Reads knobs defensively, since callers build the namespace by
    hand. Names the gate policy that will actually be driven, not the one that
    was asked for, so the filename never claims a ceiling arm the run refused
    to be."""
    parts = []
    if _arm_gates(args) == "approve":
        parts.append("ceiling")
    policies = getattr(args, "policies", "none")
    if policies and policies != "none":
        parts.append(f"policies-{policies}")
    autonomy = getattr(args, "autonomy", None)
    if autonomy:
        parts.append(autonomy)
    return "".join(f".{part}" for part in parts)


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

    # An unset --autonomy is the baseline arm: pin "careful" rather than
    # inherit the Session's autonomous default, so the empty-suffix results
    # keep meaning what the ones already on disk say (every AUTO finding
    # gated). The arms are opted into, never fallen into.
    autonomy = getattr(args, "autonomy", None) or "careful"
    human_gates = _arm_gates(args)
    row: dict = {
        "name": name,
        "diseases": entry["diseases"],
        "date": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "handoff": fmt,
        "human_gates": human_gates,
        "policies": getattr(args, "policies", "none"),
        "autonomy": autonomy,
        "model": llm.model_info(),
        "status": "ok",
    }
    t0 = time.monotonic()
    gates: list = []
    # T1.5: per-case telemetry file, per arm; the generate() calls run in
    # this host process and read CRIVO_TELEMETRY per call (crivo/telemetry.py)
    suffix = _arm_suffix(args)
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
            policies=getattr(args, "policy_records", None),
            autonomy=autonomy,
        )
        try:
            session.load(str(dirty_path), "df")
            row["events"] = _drive(
                session.clean("df"),
                args.max_events,
                args.wall_cap,
                t0,
                gates=gates,
                human_gates=human_gates,
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


def _tokens_text(value: float | None) -> str:
    """Render a token count for the terminal. None is `unknown`, never 0: no
    span reported usage, so what the case cost is not known, and a 0 in the
    cost column would read as free."""
    return "unknown" if value is None else str(value)


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
    parser.add_argument(
        "--policies",
        choices=("none", "auto"),
        default="none",
        help="auto = one ENFORCE policy batching every AUTO disease with a "
        "registered fixer (the M1 batched arm; T1.4)",
    )
    parser.add_argument(
        "--autonomy",
        choices=("autonomous", "careful"),
        default=None,
        help="the autonomy arm to measure: autonomous applies AUTO findings "
        "with a registered fixer without showing a gate, careful gates them. "
        "Unset keeps the baseline arm (careful, empty file suffix). "
        "report-only is not an arm here: the bench scores cleaned output and "
        "a report-only run cleans nothing",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run each case N times and report pass^k consistency (A4); "
        "N model runs per case, so this is a paid choice",
    )
    args = parser.parse_args(argv)
    if args.autonomy and args.human_gates == "approve":
        # An autonomy arm is measured with HUMAN gates skipped. `approve` runs
        # every non-admission HUMAN gate, which is a person's authorisation to
        # give, and the autonomous arm's reach is meant to come from the
        # Session's seeded AUTO policy alone. Coerce to the safe arm, and say
        # so: the row and the filename both record what actually ran. Saying
        # it here is for the operator who typed both flags; _arm_gates is what
        # enforces it, at every point of use.
        print("--autonomy is measured with --human-gates skip; approve ignored")
        args.human_gates = "skip"
    args.policy_records = []
    if args.policies == "auto":
        args.policy_records = [
            PolicyRecord(
                id="bench-auto",
                disease_ids=tuple(sorted(FIXERS)),
                approver="bench",
                expires="2099-01-01",
                mode="ENFORCE",
                valid_disease_ids=set(FIXERS),
            )
        ]

    _load_dotenv()
    _require_key()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    entries = corpus.full_corpus() if args.full else list(corpus.SMOKE)
    picked = random.Random(args.seed).sample(entries, min(args.sample, len(entries)))
    if args.only:
        wanted = {n.strip() for n in args.only.split(",") if n.strip()}
        picked = [e for e in picked if e["name"] in wanted]

    suffix = _arm_suffix(args)
    repeat = max(1, args.repeat)
    rows = []
    for entry in picked:
        for run_i in range(repeat):
            # a repeated case needs a distinct file per run so pass^k has k
            # rows to score; a single run keeps the plain name (resume/skip)
            tag = f".r{run_i + 1}" if repeat > 1 else ""
            out = RESULTS_DIR / f"{entry['name']}{suffix}{tag}.json"
            if out.exists() and not args.force:
                rows.append(json.loads(out.read_text()))
                print(f"{entry['name']}{tag}: already on disk, skipped (R5)")
                continue
            row = _run_case(entry, args)
            if repeat > 1:
                row["run"] = run_i + 1
            out.write_text(json.dumps(row, indent=2, default=str))
            repair = (row.get("scores") or {}).get("repair", {})
            calls = row.get("calls") or {}
            print(
                f"{row['name']}{tag}: {row['status']}"
                f" | repair F1 {repair.get('f1')}"
                f" | {row['wall_secs']}s | {row.get('events', 0)} events"
                f" | {calls.get('count', 0)} calls"
                f" | {_tokens_text(calls.get('new_work_tokens'))} new-work tok"
            )
            rows.append(row)

    ok = [r for r in rows if r.get("status") == "ok" and r.get("scores")]
    ok_calls = [r.get("calls") or {} for r in ok]
    # the token mean covers only the scored cases that reported usage, so it
    # is published with the denominator it was taken over, and that
    # denominator names its own scope: an aborted case spends real tokens and
    # is in neither half of it
    tokens = [c.get("new_work_tokens") for c in ok_calls]
    known_tokens = [t for t in tokens if t is not None]
    print(
        f"\nagent lane: {len(ok)}/{len(rows)} scored"
        f" | repair F1 mean {_mean([r['scores']['repair'].get('f1') for r in ok])}"
        f" | repair recall mean "
        f"{_mean([r['scores']['repair'].get('recall') for r in ok])}"
        f" | calls mean {_mean([c.get('count') for c in ok_calls])}"
        f" | new-work tokens mean {_tokens_text(_mean(tokens))}"
        f" ({len(known_tokens)}/{len(tokens)} scored cases with token data)"
    )

    if repeat > 1:
        from bench import consistency

        agg = consistency.aggregate_consistency(consistency.group_by_case(rows))
        print(
            f"\nconsistency (k={repeat}): mean pass^k "
            f"{round(agg['mean_pass_k'], 3)} | mean pass@1 "
            f"{round(agg['mean_pass_at_1'], 3)}"
        )
        if agg["flaky"]:
            print(f"  flaky (solved some runs, not all): {', '.join(agg['flaky'])}")
        if agg["stable_solved"]:
            print(f"  stable solved every run: {', '.join(agg['stable_solved'])}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
