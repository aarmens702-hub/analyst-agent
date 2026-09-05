"""bench.compare units (spec: specs/2026-09-04-a1-build-plan.md T1.5).

Offline and keyless: the comparison table is a pure-stdlib report over two
result directories, so it must run anywhere the suite runs.
"""

import json

from bench import compare


def _write(dir_path, name, row):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{name}.json").write_text(json.dumps(row))


def _row(status="ok", f1=None, wall=None, count=None, tokens=None):
    row = {"status": status}
    if f1 is not None:
        row["scores"] = {"repair": {"f1": f1}}
    if wall is not None:
        row["wall_secs"] = wall
    if count is not None:
        row["calls"] = {"count": count, "new_work_tokens": tokens}
    return row


def test_compare_renders_matched_deltas_means_and_unmatched(tmp_path, capsys):
    """T1.5: cases match by filename; the table shows status, repair F1,
    wall clock with delta, calls, and new-work tokens A -> B; means cover
    only cases scored in both; unmatched cases land at the bottom."""
    a, b = tmp_path / "a", tmp_path / "b"
    _write(a, "case_one", _row(f1=0.5, wall=10.0, count=4, tokens=100))
    _write(b, "case_one", _row(f1=0.75, wall=12.5, count=2, tokens=60))
    _write(a, "case_two", _row(f1=0.8, wall=50.0, count=7, tokens=900))
    _write(b, "case_two", _row(status="aborted: wall cap 300s hit", wall=300.0))
    _write(a, "only_a", _row(f1=1.0, wall=1.0))
    _write(b, "only_b", _row(f1=1.0, wall=1.0))

    assert compare.main([str(a), str(b)]) == 0
    out = capsys.readouterr().out

    assert "case_one" in out
    assert "ok -> ok" in out
    assert "0.50 -> 0.75" in out
    assert "10.0 -> 12.5 (+2.5)" in out
    assert "4 -> 2" in out
    assert "100 -> 60" in out

    assert "ok -> aborted" in out
    assert "0.80 -> -" in out
    assert "7 -> -" in out

    # only case_one is scored in both, so the means are its values verbatim
    assert "means (1 scored in both)" in out
    assert "only in A: only_a" in out
    assert "only in B: only_b" in out


def test_compare_table_columns_stay_aligned(tmp_path, capsys):
    """T1.5: the table is aligned; every cell of a column starts at the same
    offset even when case names and values differ in width."""
    a, b = tmp_path / "a", tmp_path / "b"
    _write(a, "tiny", _row(f1=0.5, wall=1.0, count=1, tokens=1))
    _write(b, "tiny", _row(f1=0.5, wall=2.0, count=2, tokens=2))
    _write(
        a, "a-much-longer-case-name", _row(f1=1.0, wall=100.0, count=10, tokens=12345)
    )
    _write(
        b, "a-much-longer-case-name", _row(f1=1.0, wall=200.0, count=20, tokens=54321)
    )

    assert compare.main([str(a), str(b)]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    header, *body = lines
    status_col = header.index("status")
    assert status_col > len("a-much-longer-case-name")
    for line in body:
        assert line[status_col] != " ", f"status column drifted in: {line!r}"


def test_compare_is_a_report_not_a_gate(tmp_path, capsys):
    """T1.5: exit 0 always, even with nothing matched and nothing scored."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert compare.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 0
