"""`crivo diagnose` as a data linter (bulletproof-core arc R4): exit codes a
CI pipeline can gate on, like ruff for data. 0 = nothing at/above the
threshold, 1 = findings the threshold cares about, 2 = could not run at all.

The threshold order is about judgment required, not severity: AUTO findings
are safe-to-fix housekeeping; GATE and HUMAN need a person. `--fail-on GATE`
(the default) means "fail me when something needs a human"."""

import pandas as pd


def _dirty_gate_csv(tmp_path):
    """A file whose findings include at least one GATE-or-above grade:
    an age column with impossible values trips d13 (GATE)."""
    path = tmp_path / "people.csv"
    pd.DataFrame({"age": [34, 45, 29, 150, -3, 61, 22, 58, 40, 33]}).to_csv(
        path, index=False
    )
    return path


def test_dirty_file_fails_at_the_default_gate_threshold(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["crivo", "diagnose", str(_dirty_gate_csv(tmp_path))]
    )
    from crivo.__main__ import main

    assert main() == 1
    assert "out-of-domain" in capsys.readouterr().out


def test_fail_on_never_and_clean_files_exit_zero(tmp_path, monkeypatch, capsys) -> None:
    """The zero paths: --fail-on never restores today's report-only behavior
    on a dirty file, and a genuinely clean file exits 0 at the default."""
    from crivo.__main__ import main

    dirty = _dirty_gate_csv(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["crivo", "diagnose", str(dirty), "--fail-on", "never"]
    )
    assert main() == 0

    clean_path = tmp_path / "clean.csv"
    pd.DataFrame({"height_cm": [171.2, 165.8, 180.1, 156.4, 177.7, 169.3]}).to_csv(
        clean_path, index=False
    )
    monkeypatch.setattr("sys.argv", ["crivo", "diagnose", str(clean_path)])
    assert main() == 0
    capsys.readouterr()  # drain


def test_unreadable_file_exits_two_like_bad_arguments(
    tmp_path, monkeypatch, capsys
) -> None:
    """2 means 'could not run', matching argparse's own bad-argument exit —
    distinct from 1 ('ran, found problems') so CI can tell a broken pipeline
    from a dirty dataset."""
    from crivo.__main__ import main

    monkeypatch.setattr(
        "sys.argv", ["crivo", "diagnose", str(tmp_path / "no-such-file.csv")]
    )
    assert main() == 2
    assert "could not read" in capsys.readouterr().out


def test_json_output_survives_and_auto_threshold_is_stricter(
    tmp_path, monkeypatch, capsys
) -> None:
    """--json still prints one parseable object whatever the exit code; and
    --fail-on AUTO fails a file whose findings are all safe-to-fix, while the
    default GATE lets the same file pass (housekeeping isn't a person's
    problem)."""
    import json

    from crivo.__main__ import main

    words = [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
        "iota",
        "kappa",
        "mu",
        "nu",
    ]
    padded = tmp_path / "padded.csv"
    pd.DataFrame({"note": [f"  {w}  " for w in words]}).to_csv(padded, index=False)

    monkeypatch.setattr("sys.argv", ["crivo", "diagnose", str(padded), "--json"])
    code = main()
    payload = json.loads(capsys.readouterr().out)
    grades = {f["grade"] for f in payload["findings"]}
    assert grades == {"AUTO"}, f"fixture must be AUTO-only, got {grades}"
    assert code == 0, "default GATE ignores safe-to-fix housekeeping"

    monkeypatch.setattr(
        "sys.argv", ["crivo", "diagnose", str(padded), "--json", "--fail-on", "AUTO"]
    )
    assert main() == 1
    json.loads(capsys.readouterr().out)  # still one parseable object
