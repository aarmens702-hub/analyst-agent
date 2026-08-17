"""analyst-agent: verified hands for your data.

The public library surface is keyless and kernel-free:

    import analyst_agent as aa
    report = aa.diagnose("data.xlsx")   # 22 checks, no key, no setup
    print(report)
    df = aa.read("data.csv"); aa.write(df, "clean.parquet")

The LLM-authored cleaning with gates and provenance is the agent surface,
`python -m analyst_agent` (or the MCP server, `analyst-agent-mcp`).
"""

# importing the accessor module registers the `df.aa` accessor as a side effect
from analyst_agent import accessor as _accessor  # noqa: F401
from analyst_agent.api import (
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
    from analyst_agent.__main__ import main as _main

    return _main()
