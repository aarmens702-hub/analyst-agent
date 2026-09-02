"""Tests for bench/external.py — the Raha adapter (spec R4).

Offline throughout: hand-built clean/dirty CSV pairs under tmp_path, loaded
through load_pair exactly the way the real fetched data would be, so the
adapter is exercised identically whether the pair came from Raha or a
fixture. The real fetch (scripts/fetch_raha.py against the network) is run
manually, never from this suite.
"""

from pathlib import Path

from bench.external import load_pair, truth_from_pair
from bench.truth import Cell, GroundTruth


def _write_pair(root: Path, name: str, clean_text: str, dirty_text: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "clean.csv").write_text(clean_text)
    (root / name / "dirty.csv").write_text(dirty_text)


def test_truth_from_pair_records_exactly_the_differing_cells(tmp_path: Path) -> None:
    _write_pair(
        tmp_path,
        "widgets",
        clean_text="id,name,amount\n1,alice,10\n2,bob,20\n3,carol,30\n4,dave,40\n",
        dirty_text="id,name,amount\n1,alice,10\n2,bob,2O\n3,carOl,30\n4,dave,\n",
    )
    clean, dirty = load_pair("widgets", root=tmp_path)

    truth = truth_from_pair(clean, dirty, "widgets")

    assert truth.seed == 0
    assert truth.base == "external"
    assert truth.n_rows == 4
    assert truth.n_cols == 3
    assert {c.disease for c in truth.corruptions} == {0}
    assert {c.granularity for c in truth.corruptions} == {"cell"}

    by_column = {c.columns: c for c in truth.corruptions}
    assert set(by_column) == {
        ("name",),
        ("amount",),
    }  # one Corruption per affected column
    assert by_column[("name",)].cells == (
        Cell(row=2, column="name", original="carol", corrupted="carOl"),
    )
    assert set(by_column[("amount",)].cells) == {
        Cell(row=1, column="amount", original="20", corrupted="2O"),
        Cell(row=3, column="amount", original="40", corrupted=""),
    }
    assert all(c.note == "widgets" for c in truth.corruptions)

    truth.verify_frame(dirty)  # stamped from the same dirty frame it was diffed against

    again = GroundTruth.from_json(truth.to_json())
    assert again == truth


def test_shape_or_column_mismatch_raises_and_missing_dataset_raises(
    tmp_path: Path,
) -> None:
    import pandas as pd
    import pytest

    clean = pd.DataFrame({"a": ["1", "2"], "b": ["x", "y"]})
    wrong_shape = pd.DataFrame({"a": ["1"], "b": ["x"]})

    with pytest.raises(ValueError, match="shape"):
        truth_from_pair(clean, wrong_shape, "shapes")
    # renamed-but-same-count columns no longer raise: they align positionally
    # and are recorded as column-granular corruptions (see the alignment test)

    with pytest.raises(FileNotFoundError, match="fetch_raha"):
        load_pair("does-not-exist", root=tmp_path)


def test_fetcher_verify_and_skip_decision(tmp_path: Path, monkeypatch) -> None:
    import hashlib
    import importlib.util

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    spec = importlib.util.spec_from_file_location(
        "fetch_raha", scripts_dir / "fetch_raha.py"
    )
    fetch_raha = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch_raha)

    root = tmp_path / "root"
    dest = root / "widgets" / "clean.csv"
    dest.parent.mkdir(parents=True)
    dest.write_text("a,b\n1,2\n")
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()

    assert fetch_raha.verify(dest, digest) is True  # correct hash passes
    assert fetch_raha.verify(dest, "0" * 64) is False  # wrong hash detected

    def _no_network(*args, **kwargs):
        raise AssertionError("download() must not run when the file already matches")

    monkeypatch.setattr(fetch_raha, "download", _no_network)
    entry = {"url": "unused", "sha256": digest}
    assert fetch_raha.fetch_one("widgets", "clean.csv", entry, root) == "skipped"


def test_load_scored_pair_aligns_renamed_headers_positionally(tmp_path: Path):
    # Raha reality: hospital renames every column between clean and dirty,
    # beers renames two — same order, same count. The rename is itself dirt,
    # so alignment must record it, not crash and not silently ignore it —
    # and the manifest hash must pin the ALIGNED frame, the one scoring eats.
    import pandas as pd

    from bench.external import load_scored_pair

    d = tmp_path / "hospital"
    d.mkdir()
    pd.DataFrame({"provider_number": ["1", "2"], "city": ["a", "b"]}).to_csv(
        d / "clean.csv", index=False
    )
    pd.DataFrame({"ProviderNumber": ["1", "x"], "city": ["a", "b"]}).to_csv(
        d / "dirty.csv", index=False
    )
    _, dirty, truth = load_scored_pair("hospital", root=tmp_path)
    assert list(dirty.columns) == ["provider_number", "city"]  # aligned
    by_granularity = {c.granularity: c for c in truth.corruptions}
    rename = by_granularity["column"]
    assert rename.columns == ("provider_number",)
    assert "ProviderNumber" in rename.note
    cells = by_granularity["cell"].cells
    assert [(c.row, c.column) for c in cells] == [(1, "provider_number")]
    truth.verify_frame(dirty)  # the returned dirty is exactly the pinned frame


def test_fetch_all_drives_every_entry_and_main_respects_strict(
    tmp_path: Path, monkeypatch
) -> None:
    import importlib.util

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    spec = importlib.util.spec_from_file_location(
        "fetch_raha", scripts_dir / "fetch_raha.py"
    )
    fetch_raha = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch_raha)

    fake_datasets = {
        "a": {"f1.csv": {"url": "u1", "sha256": "h1"}},
        "b": {"f2.csv": {"url": "u2", "sha256": "h2"}},
    }
    monkeypatch.setattr(fetch_raha, "DATASETS", fake_datasets)
    calls = []

    def fake_fetch_one(name, filename, entry, root):
        calls.append((name, filename, entry, root))
        return "fetched" if name == "a" else "failed"

    monkeypatch.setattr(fetch_raha, "fetch_one", fake_fetch_one)

    statuses = fetch_raha.fetch_all(tmp_path)

    assert calls == [
        ("a", "f1.csv", fake_datasets["a"]["f1.csv"], tmp_path),
        ("b", "f2.csv", fake_datasets["b"]["f2.csv"], tmp_path),
    ]
    assert statuses == {"a/f1.csv": "fetched", "b/f2.csv": "failed"}

    monkeypatch.setattr(fetch_raha, "fetch_all", lambda root: statuses)
    assert (
        fetch_raha.main(["--root", str(tmp_path)]) == 0
    )  # non-strict tolerates failures
    assert (
        fetch_raha.main(["--root", str(tmp_path), "--strict"]) == 1
    )  # strict does not
