"""Tests for `analyst-agent diagnose` — the report you can get for free.

Everything else in this project needs an API key, a kernel, and trust. This
needs a file. It is the only surface where the strongest part of the codebase,
the detection engine, is reachable without a model being involved at all.
"""

import pandas as pd

from analyst_agent import checkup


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
    report = checkup.report(write_csv(tmp_path))

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


def test_the_clean_subcommand_is_headless_and_machine_readable(
    monkeypatch, capsys
) -> None:
    """`analyst-agent clean <file> --json` is the orchestration surface: no
    prompts, chatter on stderr, one JSON object on stdout. The session runs
    with previews off (nobody reads them) and the auto policy (judgement
    calls deferred, never decided)."""
    import json

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("ANALYST_PROVIDER", raising=False)
    monkeypatch.setattr(
        "sys.argv", ["analyst-agent", "clean", "data/messy.csv", "--json"]
    )
    session_kw: dict = {}

    class FakeSession:
        def __init__(self, **kw):
            session_kw.update(kw)

        def close(self):
            pass

    monkeypatch.setattr("analyst_agent.loop.Session", FakeSession)
    monkeypatch.setattr(
        "analyst_agent.repl.run_clean_once",
        lambda session, path, name=None, policy="auto": {
            "file": path,
            "policy": policy,
            "needs_human": ["fix 2/3 · merge variants"],
        },
    )
    from analyst_agent.__main__ import main

    assert main() == 0
    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["file"] == "data/messy.csv"
    assert payload["policy"] == "auto"
    assert session_kw.get("preview") is False


def test_resume_flag_reaches_the_session(monkeypatch) -> None:
    """P5 R9's last mile: --resume s16 must construct the Session with
    resume='s16'. Everything past the constructor is covered by the real
    kernel resume test; this pins the plumbing between argv and Session."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.delenv("ANALYST_PROVIDER", raising=False)
    monkeypatch.setattr("sys.argv", ["analyst-agent", "--resume", "s16"])
    seen: dict = {}

    class Captures:
        def __init__(self, **kw):
            seen.update(kw)
            raise SystemExit  # constructor reached; nothing else should run

    monkeypatch.setattr("analyst_agent.loop.Session", Captures)
    import pytest as _pytest

    from analyst_agent.__main__ import main

    with _pytest.raises(SystemExit):
        main()
    assert seen.get("resume") == "s16"


def test_the_key_gate_matches_the_provider(monkeypatch, capsys) -> None:
    """R10 follow-through: with ANALYST_PROVIDER=claude, holding a DeepSeek
    key must not satisfy the gate, and the message must name the key the
    session would actually use."""
    monkeypatch.setenv("ANALYST_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-the-right-provider")
    monkeypatch.setattr("sys.argv", ["analyst-agent"])

    from analyst_agent.__main__ import main

    assert main() == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_a_windows_encoded_export_still_gets_a_report(tmp_path) -> None:
    """HM Treasury's real March 2026 spend CSV carries a literal £ sign in
    Windows-1252; utf-8 dies at byte 0xa3 before a single detector runs — an
    encoding disease the loader itself was manufacturing into a crash. The
    fallback must be *loud*: a silently switched encoding is the exact
    found-nothing/didn't-check conflation this project exists to kill."""
    path = tmp_path / "spend.csv"
    rows = "Supplier,Amount\n" + "\n".join(f"Vendor {i},£{i}25.00" for i in range(30))
    path.write_bytes(rows.encode("cp1252"))

    report = checkup.report(path)

    assert "30 rows" in report
    assert "cp1252" in report, "the fallback must be reported, not silent"


def test_the_transaction_fixture_scores_against_its_own_ground_truth(tmp_path) -> None:
    """scripts/make_transactions.py plants four diseases on purpose so this
    report can be scored, not admired. The columns that matter most — amount
    in five money formats, posted_at in four timestamp formats — were the
    exact two the pre-A1 gate filed under "checked and clean"."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import make_transactions

    path = make_transactions.write(tmp_path)[0]  # q1, the un-drifted schema
    report = checkup.report(path)

    assert "amount" in report, "five money formats must not read as clean"
    assert "posted_at" in report, "four timestamp formats must not read as clean"
    assert "merchant" in report
    assert "N/A" in report or "sentinel" in report
