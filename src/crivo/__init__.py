"""crivo: verified hands for your data.

The public library surface is keyless and kernel-free:

    import crivo
    report = crivo.diagnose("data.xlsx")   # 22 checks, no key, no setup
    print(report)
    df = crivo.read("data.csv"); crivo.write(df, "clean.parquet")

The LLM-authored cleaning with gates and provenance is the agent surface,
`python -m crivo` (or the MCP server, `crivo-mcp`).
"""

# importing the accessor module registers the `df.crivo` accessor as a side effect
from crivo import accessor as _accessor  # noqa: F401

# the reconcile submodules share a name with their public wrappers imported from
# crivo.api below. Load the submodules first so the wrapper names win the
# crivo.reconcile / crivo.reconcile_report attributes: whichever import runs
# last sets the attribute, and a later first import of a submodule would
# otherwise replace the callable wrapper with the module object.
from crivo import reconcile as _reconcile_submodule  # noqa: F401
from crivo import reconcile_report as _reconcile_report_submodule  # noqa: F401
from crivo.api import (
    CleanSummary,
    Report,
    analyze_excel,
    clean,
    compare,
    diagnose,
    drivers,
    export_notebook,
    load_example,
    read,
    read_sql,
    reconcile,
    reconcile_report,
    write,
)
from crivo.query import Answer, ask

__all__ = [
    "Answer",
    "CleanSummary",
    "Report",
    "analyze_excel",
    "ask",
    "clean",
    "compare",
    "diagnose",
    "drivers",
    "export_notebook",
    "load_example",
    "main",
    "read",
    "read_sql",
    "reconcile",
    "reconcile_report",
    "write",
]


def main() -> int:
    """Console-script entry (pyproject [project.scripts])."""
    from crivo.__main__ import main as _main

    return _main()
