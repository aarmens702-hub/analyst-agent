"""Session-to-notebook export (capability roadmap B3.2).

Turn a diagnosis into a runnable Jupyter notebook so the analysis is a
reproducible receipt: open it, run every cell, and the same report comes back.
The notebook is written by hand as nbformat v4 JSON with the standard library
`json` alone. No `nbformat`, no `jupyter`, and no crivo core module is
imported, so producing the receipt stays pure and keyless (importing this
module triggers no network and needs no API key).

The one place a value crosses into generated Python, the source path in the
loader and each inspected column name, goes through `json.dumps`, whose output
is a valid Python string literal. A path carrying a quote or a backslash
therefore cannot break out of the string it belongs in.
"""

from __future__ import annotations

import json
from pathlib import Path

NBFORMAT = 4
NBFORMAT_MINOR = 5  # 4.5 gives cells a stable `id`; write one so it is genuine


def _lines(text: str) -> list[str]:
    """Split a source block into nbformat's list-of-lines, newlines kept, so
    that "".join(lines) rebuilds the block exactly."""
    return text.splitlines(keepends=True)


def _markdown_cell(cell_id: str, text: str) -> dict:
    return {
        "id": cell_id,
        "cell_type": "markdown",
        "metadata": {},
        "source": _lines(text),
    }


def _code_cell(cell_id: str, text: str) -> dict:
    return {
        "id": cell_id,
        "cell_type": "code",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": _lines(text),
    }


def _load_code(source_literal: str) -> str:
    """Import crivo and pandas, read the frame, diagnose it, print the report."""
    return (
        "import crivo\n"
        "import pandas as pd\n"
        "\n"
        f"df = crivo.read({source_literal})\n"
        "report = crivo.diagnose(df)\n"
        "print(report)"
    )


def _findings_markdown(findings: list[dict]) -> str:
    """Bullet the findings as slug, columns, grade, and evidence."""
    if not findings:
        return "## Findings\n\nNo findings were reported for this dataset."
    parts = ["## Findings", ""]
    for finding in findings:
        columns = ", ".join(finding["columns"]) or "whole table"
        parts.append(
            f"- **{finding['slug']}** columns [{columns}], "
            f"grade {finding['grade']}. {finding['evidence']}"
        )
    return "\n".join(parts)


def diagnosis_to_notebook(source, findings: list[dict], path) -> Path:
    """Write the diagnosis of `source` as a runnable .ipynb and return its path.

    `source` is the file path the notebook loads with `crivo.read`; `findings`
    is a list of finding dicts (slug, columns, grade, evidence). The notebook
    holds a title, a load-and-diagnose cell, a findings summary, and one
    value_counts inspection cell per finding column. Parent directories are
    created. The source path and every column name reach generated code only
    through `json.dumps` (see the module docstring).
    """
    cells: list[dict] = []

    def add_markdown(text: str) -> None:
        cells.append(_markdown_cell(f"cell{len(cells)}", text))

    def add_code(text: str) -> None:
        cells.append(_code_cell(f"cell{len(cells)}", text))

    add_markdown(
        "# Crivo diagnosis\n\n"
        "A runnable receipt: run every cell to reproduce the diagnosis below."
    )
    add_code(_load_code(json.dumps(str(source))))
    add_markdown(_findings_markdown(findings))
    for finding in findings:
        for column in finding["columns"]:
            add_code(f"df[{json.dumps(column)}].value_counts(dropna=False).head()")

    notebook = {
        "cells": cells,
        "metadata": {},
        "nbformat": NBFORMAT,
        "nbformat_minor": NBFORMAT_MINOR,
    }

    written = Path(path)
    written.parent.mkdir(parents=True, exist_ok=True)
    written.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
    return written
