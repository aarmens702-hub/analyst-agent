"""Synthesise a transaction file carrying the four diseases a finance take-home names.

Unlike `fetch_raha.py` and `fetch_vancouver.py` this generates rather than
downloads, because the interesting property here is not realism — it is that we
know the ground truth. Every defect below is planted deliberately, so a run of
`crivo diagnose` can be scored: what it found, and what it walked past.

The four target diseases, and where each is planted:

  money as text        `amount` in five incompatible formats, `balance` in one
  mixed timestamps     `posted_at` in four formats including a bare epoch int
  currency as unit     `currency` with case variants and blanks, no FX applied
  schema drift         q2 renames two columns, drops one, adds `fx_rate`

`amount` and `balance` are the pair that matters. `balance` is uniformly
'$X,XXX.XX' — one format, cleanly detected. `amount` carries the same disease
five ways over, which is strictly worse data, and is the harder case by
construction. A detector that reports `balance` and stays quiet on `amount` has
its sensitivity pointed backwards; keeping both columns in one file is what
makes that visible in a single report.

Usage:  uv run python scripts/make_transactions.py [outdir]
"""

import csv
import pathlib
import random
import sys

SEED = 7
ROWS = 1200

MERCHANTS = [
    "ACME Corp",
    "acme corp",
    "ACME  Corp.",  # doubled space
    "Globex",
    "GLOBEX",
    "Initech",
    "Initech Inc",
    "Umbrella Corp",  # NBSP, not a space
    "Umbrella Corp",
]
CATEGORIES = ["groceries", "Groceries", "GROCERIES", "travel", "Travel", "fuel", ""]
CURRENCIES = ["USD", "usd", "EUR", "GBP", ""]


def money(value: float, style: int) -> str:
    """One amount, rendered five mutually incompatible ways."""
    if style == 0:
        return f"${value:,.2f}"  # currency symbol + thousands separator
    if style == 1:
        return f"({abs(value):,.2f})" if value < 0 else f"{value:,.2f}"  # accounting
    if style == 2:
        return f"{value:.2f}".replace(".", ",")  # European decimal comma
    if style == 3:
        return f"USD {value:.2f}"  # ISO code as a prefix
    return str(value)  # bare float


def stamp(i: int, style: int) -> str:
    """One instant, rendered four mutually incompatible ways."""
    if style == 0:
        return f"2024-0{i % 9 + 1}-1{i % 9} 14:3{i % 6}:00"  # ISO, space separator
    if style == 1:
        return f"0{i % 9 + 1}/1{i % 9}/2024"  # slash, ambiguous D/M
    if style == 2:
        return f"171{i % 9}0000{i % 9}0"  # epoch seconds, unlabelled
    return f"2024-0{i % 9 + 1}-1{i % 9}T14:3{i % 6}:00Z"  # ISO 8601 proper


def rows(count: int = ROWS) -> list[dict]:
    random.seed(SEED)
    out = []
    for i in range(count):
        amount = round(random.uniform(-500, 5000), 2)
        out.append(
            {
                # every 97th row repeats its predecessor's id: a duplicate key
                # in a column whose whole job is to be unique
                "txn_id": f"TX{i:06d}" if i % 97 else f"TX{i - 1:06d}",
                "posted_at": stamp(i, i % 4),
                "merchant": random.choice(MERCHANTS),
                "category": random.choice(CATEGORIES),
                "amount": money(amount, i % 5),
                "currency": random.choice(CURRENCIES),
                "account_no": f"{random.randint(1000, 9999)}" if i % 11 else "N/A",
                "balance": money(round(random.uniform(0, 90000), 2), 0),
                # a linebreak inside a quoted cell: the shape that splits a
                # report bullet in half if evidence is rendered unsanitised
                "notes": "" if i % 3 else "reversed\nsee ticket #4471",
            }
        )
    return out


def drift(record: dict) -> dict:
    """Q2's schema: two renames, one drop, one addition.

    Nothing about the data changed — only the header. This is the drift a
    family-mode run has to reconcile before the two quarters can be stacked.
    """
    out = dict(record)
    out["transaction_id"] = out.pop("txn_id")
    out["timestamp"] = out.pop("posted_at")
    out.pop("notes")
    out["fx_rate"] = "1.0"
    return out


def write(directory) -> list[pathlib.Path]:
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    all_rows = rows()
    half = len(all_rows) // 2
    plan = [
        ("txn-2024-q1.csv", all_rows[:half]),
        ("txn-2024-q2.csv", [drift(r) for r in all_rows[half:]]),
    ]
    written = []
    for name, records in plan:
        path = directory / name
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
        written.append(path)
    return written


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "data/transactions"
    for path in write(target):
        print(f"wrote {path}")
