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
from crivo.api import (
    CleanSummary,
    Report,
    clean,
    diagnose,
    read,
    read_sql,
    write,
)

__all__ = [
    "CleanSummary",
    "Report",
    "clean",
    "diagnose",
    "main",
    "read",
    "read_sql",
    "write",
]


def main() -> int:
    """Console-script entry (pyproject [project.scripts])."""
    from crivo.__main__ import main as _main

    return _main()
