"""Entry point: wires the hand-written core (loop.Session) to the REPL driver."""

import argparse
import os
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="analyst-agent")
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
    # `diagnose` deliberately sits before the API-key check: it runs the
    # detection engine in-process with no model and no kernel, so needing a
    # paid credential to be told a CSV holds "N/A" would be the barrier it
    # exists to remove.
    parser.add_argument(
        "--diagnose",
        metavar="FILE",
        help="report what is wrong with a file and exit; no model, no API key",
    )
    parser.add_argument(
        "--json", action="store_true", help="machine-readable diagnose output"
    )
    if sys.argv[1:2] == ["diagnose"]:
        sys.argv = [sys.argv[0], "--diagnose", *sys.argv[2:]]
    args = parser.parse_args()

    if args.diagnose:
        from analyst_agent.diagnose import report

        try:
            print(report(args.diagnose, as_json=args.json))
        except (OSError, ValueError) as exc:
            print(f"could not read {args.diagnose}: {exc}")
            return 1
        return 0

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set — put it in .env or export it.")
        return 1

    try:
        from analyst_agent.loop import Session
    except ImportError:
        print(
            "the hand-written core (src/analyst_agent/loop.py) isn't implemented yet"
            " — the plumbing is ready and waiting."
        )
        print(
            "interfaces: specs/2026-08-09-p0-core-design.md §1"
            " and src/analyst_agent/events.py (SessionLike)"
        )
        return 1

    from analyst_agent.repl import run_repl

    session = Session(
        workspace=args.workspace, data_dir=args.data_dir, docker=args.docker
    )
    run_repl(session, auto_run=args.auto_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
