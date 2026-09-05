"""Session-to-notebook export tests (capability roadmap B3.2).

A diagnosis becomes a runnable .ipynb receipt built by hand as nbformat v4
JSON with the standard library alone. These tests parse the written file with
`json` only, so they never require jupyter or nbformat to be installed. The
security property under test: the source path enters generated Python through
`json.dumps`, so a path carrying a quote or a backslash cannot break out of
the string literal it belongs in.
"""

import ast
import json
from pathlib import Path

from crivo.notebook_export import diagnosis_to_notebook


def _findings():
    """Two findings, three finding-columns total (1 + 2)."""
    return [
        {
            "slug": "sentinel-missing",
            "columns": ["units"],
            "grade": "AUTO",
            "evidence": "-999 appears 12x",
        },
        {
            "slug": "case-variants",
            "columns": ["region", "city"],
            "grade": "GATE",
            "evidence": "3 case variants in region",
        },
    ]


def _load_code(nb):
    """The one code cell that reads the source and diagnoses it."""
    codes = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    return next(c for c in codes if "crivo.read(" in c)


def test_written_notebook_is_valid_json_nbformat_v4(tmp_path):
    path = diagnosis_to_notebook("data/sales.csv", _findings(), tmp_path / "nb.ipynb")
    assert path.exists()

    nb = json.loads(path.read_text())  # parses as JSON, no nbformat needed

    assert nb["nbformat"] == 4
    assert nb["nbformat_minor"] >= 5
    assert nb["metadata"] == {}
    types = [c["cell_type"] for c in nb["cells"]]
    assert "markdown" in types and "code" in types


def test_every_cell_has_the_required_v4_keys(tmp_path):
    path = diagnosis_to_notebook("data/sales.csv", _findings(), tmp_path / "nb.ipynb")
    nb = json.loads(path.read_text())

    for cell in nb["cells"]:
        assert cell["cell_type"] in {"markdown", "code"}
        assert isinstance(cell["source"], list)
        assert all(isinstance(line, str) for line in cell["source"])
        if cell["cell_type"] == "code":
            assert cell["metadata"] == {}
            assert cell["outputs"] == []
            assert cell["execution_count"] is None


def test_load_cell_reads_the_source_path_json_quoted(tmp_path):
    source = "data/sales.csv"
    path = diagnosis_to_notebook(source, _findings(), tmp_path / "nb.ipynb")
    nb = json.loads(path.read_text())

    load = _load_code(nb)

    assert "import crivo" in load
    assert "import pandas as pd" in load
    assert "crivo.diagnose(df)" in load
    assert "print(report)" in load
    # the path is a json-quoted literal, never bare string concatenation
    assert f"crivo.read({json.dumps(source)})" in load


def test_one_value_counts_inspection_cell_per_finding_column(tmp_path):
    findings = _findings()
    path = diagnosis_to_notebook("d.csv", findings, tmp_path / "nb.ipynb")
    nb = json.loads(path.read_text())

    codes = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    inspect = [c for c in codes if ".value_counts(dropna=False).head()" in c]

    assert len(inspect) == sum(len(f["columns"]) for f in findings)
    for finding in findings:
        for col in finding["columns"]:
            assert any(f"df[{json.dumps(col)}]" in c for c in inspect)


def test_source_with_quote_or_backslash_round_trips_safely(tmp_path):
    source = r'data\weird "name".csv'
    path = diagnosis_to_notebook(source, _findings(), tmp_path / "nb.ipynb")

    nb = json.loads(path.read_text())  # whole notebook is still valid JSON
    load = _load_code(nb)

    literal = json.dumps(source)
    assert f"crivo.read({literal})" in load
    # the embedded literal is valid Python and evaluates back to the exact path
    assert ast.literal_eval(literal) == source


def test_no_findings_still_produces_a_runnable_notebook(tmp_path):
    path = diagnosis_to_notebook("d.csv", [], tmp_path / "nb.ipynb")
    nb = json.loads(path.read_text())

    types = [c["cell_type"] for c in nb["cells"]]
    assert "markdown" in types and "code" in types  # title, load, summary survive
    codes = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    assert not [c for c in codes if ".value_counts(dropna=False)" in c]


def test_module_imports_only_stdlib_no_jupyter_or_nbformat():
    import crivo.notebook_export as mod

    tree = ast.parse(Path(mod.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    # keyless and dependency-free: stdlib only, no notebook library, no crivo
    # core module, so importing triggers no network and needs no API key. The
    # generated `import crivo` lives in a string the reader runs, not here.
    assert imported <= {"__future__", "json", "pathlib"}
    assert {"crivo", "nbformat", "jupyter"}.isdisjoint(imported)
