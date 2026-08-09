"""Entry point: wires the hand-written core (loop.Session) to the REPL driver.

Runs before the core exists: exits 1 with a pointer at the spec instead of a
traceback, so `uv run python -m analyst_agent` is honest at every stage of P0.
"""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="analyst-agent")
    parser.add_argument(
        "--auto-run", action="store_true", help="skip the accept/run gate (dev only)"
    )
    parser.add_argument(
        "--workspace", default="workspace", help="session workspace directory"
    )
    args = parser.parse_args()

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

    session = Session(workspace=args.workspace)
    run_repl(session, auto_run=args.auto_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
