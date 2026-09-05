"""Cross-column contradiction detection (P7, first harder-data detector).

A functional-dependency violation: when one column almost determines another
(within each value of A, a single value of B dominates for at least a
threshold of the rows), the minority rows that break the rule are likely
errors, e.g. a zip that disagrees with its city, a product code whose
category flips on a handful of rows. The signal is deterministic and the
search is over ordered column pairs, so no model nomination is needed here;
3+ column rules arrive later by nomination (design spec P7 R2).

A discovered rule is a hypothesis, not a certainty (some columns really are
many-to-many), so every violation is graded HUMAN: surfaced for a person,
never auto-fixed. Keyless, pandas only.
"""

from __future__ import annotations

import pandas as pd

# below this many rows sharing both columns there is too little to infer a
# rule at all, so the pair is skipped rather than guessed at
_MIN_ROWS = 3
_EXAMPLE_CAP = 5


def _examples(pair: pd.DataFrame, a: str, b: str) -> list[tuple[str, str]]:
    """Up to _EXAMPLE_CAP (a-value, b-value) pairs from rows that break the
    dominant mapping. Bounded, so evidence is never a full dump."""
    out: list[tuple[str, str]] = []
    for aval, grp in pair.groupby(a):
        dominant = grp[b].value_counts().index[0]
        for bval in grp[b][grp[b] != dominant]:
            out.append((str(aval), str(bval)))
            if len(out) >= _EXAMPLE_CAP:
                return out
    return out


def find_fd_violations(
    df: pd.DataFrame, threshold: float = 0.9, max_pairs: int = 200
) -> list[dict]:
    """Findings for near-functional-dependency violations between column
    pairs (P7). For each ordered pair (A, B), if A determines B for at least
    `threshold` of the rows where both are present but not all of them, the
    breaking rows are reported. Perfect dependencies (no exception) and pairs
    below the threshold yield nothing. The ordered-pair search is capped at
    `max_pairs`; if it caps, a `pairs-capped` note rides on the result so the
    bound is never silent (design spec P7 R4)."""
    cols = list(df.columns)
    total_pairs = len(cols) * (len(cols) - 1)
    findings: list[dict] = []
    examined = 0
    capped = False

    for a in cols:
        if examined >= max_pairs:
            capped = True
            break
        for b in cols:
            if a == b:
                continue
            if examined >= max_pairs:
                capped = True
                break
            examined += 1
            pair = df[[a, b]].dropna()
            n = len(pair)
            if n < _MIN_ROWS:
                continue
            kept = int(pair.groupby(a)[b].agg(lambda s: s.value_counts().iloc[0]).sum())
            strength = kept / n
            if threshold <= strength < 1.0:
                violations = n - kept
                findings.append(
                    {
                        "columns": [a, b],
                        "violations": violations,
                        "strength": round(strength, 3),
                        "grade": "HUMAN",
                        "confidence": round(strength, 2),
                        "evidence": (
                            f"{a} determines {b} in {kept}/{n} rows; "
                            f"{violations} row(s) break it"
                        ),
                        "examples": _examples(pair, a, b),
                    }
                )

    if capped:
        findings.append(
            {
                "columns": [],
                "kind": "pairs-capped",
                "violations": 0,
                "grade": "AUTO",
                "confidence": 1.0,
                "evidence": (
                    f"examined {examined} of {total_pairs} column pairs "
                    f"(cap {max_pairs}); widen max_pairs to check the rest"
                ),
                "examples": [],
            }
        )
    return findings
