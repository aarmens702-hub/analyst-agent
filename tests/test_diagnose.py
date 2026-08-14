"""Tests for `analyst-agent diagnose` — the report you can get for free.

Everything else in this project needs an API key, a kernel, and trust. This
needs a file. It is the only surface where the strongest part of the codebase,
the detection engine, is reachable without a model being involved at all.
"""

import pandas as pd

from analyst_agent import diagnose


def write_csv(tmp_path):
    path = tmp_path / "beers.csv"
    pd.DataFrame(
        {
            "beer_name": [f"beer {i}" for i in range(40)],
            "ounces": ["12.0 oz", "16.0 oz.", "12.0 ounce", "16.0 OZ."] * 10,
            "ibu": ["N/A"] * 15 + [str(v) for v in range(25)],
        }
    ).to_csv(path, index=False)
    return path


def test_a_report_needs_no_model_no_kernel_and_no_key(tmp_path, monkeypatch) -> None:
    """The whole point: a stranger can run this on their own file before
    deciding whether to trust an agent with it."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    report = diagnose.report(write_csv(tmp_path))

    assert "40 rows" in report and "3 columns" in report
    assert "numbers-as-strings" in report
    assert "ounces" in report


def test_the_cli_runs_without_an_api_key(tmp_path, monkeypatch, capsys) -> None:
    """__main__ exits 1 with no key, which is right for the agent and wrong
    for a report — needing a paid credential to be told your CSV has 'N/A' in
    it is the barrier this subcommand exists to remove."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "sys.argv", ["analyst-agent", "diagnose", str(write_csv(tmp_path))]
    )

    from analyst_agent.__main__ import main

    assert main() == 0
    assert "numbers-as-strings" in capsys.readouterr().out
