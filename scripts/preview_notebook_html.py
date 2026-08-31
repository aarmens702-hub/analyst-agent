"""Write the notebook cards to a standalone .html file for eyeballing.

    uv run python scripts/preview_notebook_html.py [out.html]

Renders a real `report_html` and a real `clean_html` on a small messy frame and
drops both into one page. Because the cards are inline-only (no <style>/<script>
so GitHub/nbconvert can't strip them), the *only* way to catch a regression is to
look: open the file in a browser AND, ideally, verify it also renders in at least
one more target — JupyterLab or `jupyter nbconvert --to html` — since "same
content, different environment, silently degraded" is the failure class this
design guards against.
"""

import sys
import tempfile
from pathlib import Path

import pandas as pd

from crivo.autoclean import clean
from crivo.detect import detect_all
from crivo.notebook import clean_html, report_html


def _sample() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "amount": ["$1,200", "$3,400.50", "$15", "$980"] * 5,
            "status": ["OPEN", "open", " OPEN "] * 6 + ["OPEN", "open"],
            "region": ["  north", "south  ", "north", "east"] * 5,
            "constant": ["ACME"] * 20,
        }
    )


def main() -> int:
    df = _sample()
    report = report_html("spending.csv", df, detect_all(df, "spending.csv"))
    _cleaned, summary = clean(df)
    summary_card = clean_html(summary)

    # a neutral page ground on purpose — the cards must look right on it without
    # borrowing anything from it
    page = (
        "<!doctype html><meta charset='utf-8'>"
        "<body style='background:#f4f4f5;margin:0;padding:28px;'>"
        "<p style='font-family:sans-serif;color:#333'>diagnose &rarr;</p>"
        f"{report}"
        "<p style='font-family:sans-serif;color:#333;margin-top:28px'>clean &rarr;</p>"
        f"{summary_card}"
        "</body>"
    )

    out = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(tempfile.gettempdir()) / "aa_notebook_preview.html"
    )
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out}")
    print("open it in a browser, then also check JupyterLab or `jupyter nbconvert`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
