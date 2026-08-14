"""Loading real-world files into a real kernel.

diagnose.load learned the cp1252 fallback when HM Treasury's March 2026 file
crashed it; the agent's own LOAD_TEMPLATE is a second copy of the same read and
kept the hardcoded utf-8-sig. A live /load of the same real file dies inside
the kernel before the variable exists — this pins the fallback at the seam
diagnose's test cannot reach.
"""

import sys

from analyst_agent.loop import Session

SUBPROCESS_ARGV = [sys.executable, "-m", "analyst_agent.kernel.supervisor"]


def test_a_windows_encoded_export_loads_into_the_kernel(tmp_path) -> None:
    csv = tmp_path / "spend.csv"
    rows = "Supplier,Amount\n" + "\n".join(f"V{i},£{i}25.00" for i in range(30))
    csv.write_bytes(rows.encode("cp1252"))

    session = Session(
        workspace=tmp_path / "ws",
        data_dir=tmp_path,
        transport_argv=SUBPROCESS_ARGV,
        skills_dir=tmp_path / "skills",
    )
    try:
        session.load(str(csv), "spend")
        assert any(d["variable"] == "spend" for d in session.datasets), (
            "the cp1252 file never became a kernel variable"
        )
    finally:
        session.close()


def test_the_agent_loads_with_the_same_policy_diagnose_does(tmp_path) -> None:
    """LOAD_TEMPLATE was a second, weaker copy of the loader: no delimiter
    sniff (a semicolon CSV became one column) and pandas' default NA handling
    (read_csv coerced 'N/A' to NaN at load time, silently repairing d04's
    evidence before diagnosis ever ran — so diagnose and a live /clean of the
    same file disagreed about sentinel counts). One loader, one policy: the
    kernel cell now calls diagnose.load."""
    csv = tmp_path / "eu.csv"
    csv.write_text("region;amount\nnorth;12\nsouth;N/A\neast;9\nwest;N/A\n")

    session = Session(
        workspace=tmp_path / "ws",
        data_dir=tmp_path,
        transport_argv=SUBPROCESS_ARGV,
        skills_dir=tmp_path / "skills",
    )
    try:
        session.load(str(csv), "eu")
        result = None
        for ev in session.client.execute(
            "assert eu.shape[1] == 2, f'sniff failed: {list(eu.columns)}'\n"
            "assert (eu['amount'] == 'N/A').sum() == 2, 'N/A must survive load'\n"
            "'ok'",
            timeout_s=60,
        ):
            result = ev
        assert result.status == "ok", result.error
    finally:
        session.close()
