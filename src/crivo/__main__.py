"""Entry point: wires the hand-written core (loop.Session) to the REPL driver."""

import argparse
import os
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="crivo")
    parser.add_argument(
        "--auto-run", action="store_true", help="skip the accept/run gate (dev only)"
    )
    parser.add_argument(
        "--workspace", default="workspace", help="session workspace directory"
    )
    parser.add_argument(
        "--data-dir", default="data", help="directory of immutable source datasets"
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="run the kernel in the net-none container (default: subprocess)",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION",
        help="resume an earlier session by id (e.g. s16): datasets reloaded, "
        "fixes restored from the snapshot, /why chain intact",
    )
    # `diagnose` deliberately sits before the API-key check: it runs the
    # detection engine in-process with no model and no kernel, so needing a
    # paid credential to be told a CSV holds "N/A" would be the barrier it
    # exists to remove.
    parser.add_argument(
        "--diagnose",
        metavar="FILE",
        help="report what is wrong with a file and exit; no model, no API key",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--fail-on",
        choices=["AUTO", "GATE", "HUMAN", "never"],
        default="GATE",
        help="diagnose as a linter: exit 1 when any finding carries this grade "
        "or one needing MORE human judgment (ordered AUTO < GATE < HUMAN by "
        "judgment required — the default GATE means 'fail me when something "
        "needs a person'); 'never' always exits 0 after reporting. Unreadable "
        "files exit 2, like bad arguments.",
    )
    parser.add_argument(
        "--clean",
        metavar="FILE",
        help="headless one-shot clean: auto policy, judgement calls deferred",
    )
    parser.add_argument("--name", help="variable name for the loaded file (clean mode)")
    parser.add_argument(
        "--policy",
        choices=["auto", "all"],
        default="auto",
        help="auto: run only AUTO-grade fixes; all: approve every gate",
    )
    if sys.argv[1:2] == ["diagnose"]:
        sys.argv = [sys.argv[0], "--diagnose", *sys.argv[2:]]
    if sys.argv[1:2] == ["clean"]:
        sys.argv = [sys.argv[0], "--clean", *sys.argv[2:]]
    args = parser.parse_args()

    if args.diagnose:
        import json as _json

        from crivo import checkup

        try:
            if args.json:
                payload = checkup.report(args.diagnose, as_json=True)
                print(payload)
                findings = _json.loads(payload)["findings"]
            else:
                # styled for a terminal; rich degrades to plain text off a TTY
                frame = checkup.load(args.diagnose)
                result = checkup.detect_all(frame, os.path.basename(args.diagnose))
                checkup.render_console(args.diagnose, frame, result)
                findings = result["findings"]
        except (OSError, ValueError) as exc:
            print(f"could not read {args.diagnose}: {exc}")
            return 2  # could-not-run, like argparse's own bad-argument exit
        # linter semantics: exit 1 when a finding sits at/above the threshold
        # on the judgment ladder; an unknown grade counts as maximally human
        if args.fail_on == "never":
            return 0
        judgment = {"AUTO": 0, "GATE": 1, "HUMAN": 2}
        threshold = judgment[args.fail_on]
        return (
            1 if any(judgment.get(f["grade"], 2) >= threshold for f in findings) else 0
        )

    provider = os.environ.get("CRIVO_PROVIDER", "deepseek")
    key = "ANTHROPIC_API_KEY" if provider == "claude" else "DEEPSEEK_API_KEY"
    if not os.environ.get(key):
        print(f"{key} is not set — put it in .env or export it.")
        return 1

    try:
        from crivo.loop import Session
    except ImportError:
        print(
            "the hand-written core (src/crivo/loop.py) isn't implemented yet"
            " — the plumbing is ready and waiting."
        )
        print(
            "interfaces: specs/2026-08-09-p0-core-design.md §1"
            " and src/crivo/events.py (SessionLike)"
        )
        return 1

    if args.clean:
        # the orchestration surface: chatter to stderr, one JSON object (or a
        # short text summary) to stdout, no prompts anywhere
        import contextlib
        import json as _json

        from crivo.repl import run_clean_once

        with contextlib.redirect_stdout(sys.stderr):
            session = Session(
                workspace=args.workspace,
                data_dir=args.data_dir,
                docker=args.docker,
                preview=False,  # headless: nobody reads a preview
            )
            try:
                summary = run_clean_once(
                    session, args.clean, name=args.name, policy=args.policy
                )
                summary["policy"] = args.policy
            finally:
                session.close()
        if args.json:
            print(_json.dumps(summary))
        else:
            fixes = summary.get("fixes", [])
            done = sum(1 for f in fixes if f["status"] == "fixed")
            print(f"{args.clean}: {done}/{len(fixes)} findings fixed")
            for title in summary.get("needs_human", []):
                print(f"  needs a human: {title}")
            if summary.get("report"):
                print(f"  report: {summary['report']}")
        return 0 if not summary.get("error") else 1

    from crivo.repl import run_repl

    session = Session(
        workspace=args.workspace,
        data_dir=args.data_dir,
        docker=args.docker,
        # previews cost one kernel cell per gate and nobody reads them when
        # every gate auto-approves
        preview=not args.auto_run,
        resume=args.resume,
    )
    run_repl(session, auto_run=args.auto_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
