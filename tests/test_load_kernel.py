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
