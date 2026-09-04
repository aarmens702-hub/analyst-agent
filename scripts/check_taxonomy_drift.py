"""Fail on taxonomy drift (prime-agent steal 11 / the owner's own CI pattern).

Two places hand-maintain disease-id lists that must agree with the taxonomy
in crivo.detect: autoclean.FIXERS (a deterministic fixer per disease) and
detect.FAMILY_ONLY (diseases only detected across a file family). A fixer or
family id the taxonomy no longer defines is a typo or dead code that no test
would otherwise catch. This script derives the truth from detect.SLUGS and
exits like a linter: 0 clean, 1 drift (printed).

Usage: uv run python scripts/check_taxonomy_drift.py
"""

from __future__ import annotations

import sys


def find_drift(slugs=None, fixer_ids=None, family_ids=None) -> list[str]:
    """Return one message per id that names a disease outside the taxonomy.

    Arguments default to the live sets so the check has no arguments in CI;
    tests pass small explicit sets. Kept import-light: the live sets are
    imported lazily only when an argument is omitted."""
    if slugs is None:
        from crivo.detect import SLUGS

        slugs = SLUGS
    if fixer_ids is None:
        from crivo.autoclean import FIXERS

        fixer_ids = set(FIXERS)
    if family_ids is None:
        from crivo.detect import FAMILY_ONLY

        family_ids = set(FAMILY_ONLY)

    known = set(slugs)
    problems = []
    for bad in sorted(set(fixer_ids) - known):
        problems.append(f"autoclean.FIXERS has disease {bad}, not in detect.SLUGS")
    for bad in sorted(set(family_ids) - known):
        problems.append(f"detect.FAMILY_ONLY has disease {bad}, not in detect.SLUGS")
    return problems


def main(argv: list[str] | None = None) -> int:
    problems = find_drift()
    if problems:
        print("taxonomy drift:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("taxonomy: fixers and family ids all defined in detect.SLUGS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
