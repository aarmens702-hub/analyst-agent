"""Fetch the real-world datasets behind the Loop take-home appendix.

Three sources, all free, all keyless, all verified live on 2026-08-13:

  HM Treasury spend over £25,000 — one department's monthly disclosure,
    16 years apart. The June 2010 and March 2026 files share a 9-column
    shape with 5 columns renamed between them (Date -> Payment Date,
    Narrative -> Publication Description, ...), the date format changed
    (01/06/2010 -> 03-Mar-26), amounts went from bare floats to quoted
    thousands-comma strings, and the 2026 file is Windows-1252 — it
    carries a literal £ at byte 0xA3 that kills a utf-8 reader. None of
    that was planted. Open Government Licence.

  Government of Canada contracts over $10,000 — the consolidated
    proactive-disclosure file is 641MB, so this streams the first N rows
    and stops; the sample is a prefix, not a random draw, and the
    filename says so. Open Government Licence - Canada.

  Bank of Canada Valet daily FX — CAD rates for the currency
    normalisation step. NOTE: this CSV is not a table; it is a
    multi-section document (terms, series definitions, then
    OBSERVATIONS), which is itself a lesson in why loaders sniff before
    they trust. Terms: https://www.bankofcanada.ca/terms/

Usage:
    uv run python scripts/fetch_takehome.py            # into data/takehome/
    uv run python scripts/fetch_takehome.py --rows 50000
"""

import argparse
import urllib.request
from pathlib import Path

USER_AGENT = "crivo-fetch-takehome/0.1"

HMT = {
    # publication pages resolved via the gov.uk content API; asset URLs are
    # stable media ids on assets.publishing.service.gov.uk
    "hmt-spend-2010-06.csv": (
        "https://assets.publishing.service.gov.uk/media/"
        "5a7b5f79e5274a319e77edb8/transparency_june.csv"
    ),
    "hmt-spend-2026-03.csv": (
        "https://assets.publishing.service.gov.uk/media/"
        "69f20bef2fae53a037096855/HMT_spending_over_25000_for_Mar_26.csv"
    ),
}
CANADA = (
    "https://open.canada.ca/data/dataset/d8f85d91-7dec-4fd1-8055-483b77225d8b/"
    "resource/fac950c0-00d5-4ec1-a4d3-9cbebf98a305/download/contracts.csv"
)
BOC_FX = (
    "https://www.bankofcanada.ca/valet/observations/group/FX_RATES_DAILY/csv"
    "?start_date=2024-01-01&end_date=2024-12-31"
)


def take_prefix(stream, rows: int) -> bytes:
    """Header plus the first `rows` lines of a byte stream, then stop.

    Iterating line-by-line means the connection is abandoned after ~N reads
    rather than after 641MB; the laptop this runs on cannot hold the whole
    file, and the sample does not need it to.
    """
    kept = []
    for i, line in enumerate(stream):
        kept.append(line)
        if i >= rows:
            break
    return b"".join(kept)


def _open(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=20_000)
    parser.add_argument("--dest", default="data/takehome")
    args = parser.parse_args()
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    for name, url in HMT.items():
        with _open(url) as response:
            (dest / name).write_bytes(response.read())
        print(f"wrote {dest / name}")
    with _open(BOC_FX) as response:
        (dest / "boc-fx-daily-2024.csv").write_bytes(response.read())
    print(f"wrote {dest / 'boc-fx-daily-2024.csv'}")
    with _open(CANADA) as response:
        sample = take_prefix(response, args.rows)
    name = f"canada-contracts-first{args.rows}.csv"
    (dest / name).write_bytes(sample)
    print(f"wrote {dest / name} (prefix of a 641MB source)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
