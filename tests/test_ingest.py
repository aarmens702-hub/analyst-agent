"""Remote ingestion as kernel-resident functions (P5 R11/R12).

`tax = load_url("https://...")` is a gated code cell like any other — never a
parallel tool channel, which would produce an action with no code, no gate,
and no lineage node. The function's job is to fetch, cache a local copy, and
stamp the frame with the facts a remote lineage node needs: the URI, the fetch
time, the row count, and a content hash of what actually arrived — because an
S3 object can be overwritten and "trusted" can only mean trusted-as-of.
"""

import pandas as pd

from crivo import ingest


def test_load_url_stamps_the_facts_a_remote_lineage_node_needs(tmp_path):
    src = tmp_path / "remote.csv"
    src.write_text("supplier,amount\nAcme,12\nGlobex,N/A\n")

    frame = ingest.load_url(src.as_uri(), dest_dir=tmp_path / "cache")

    assert isinstance(frame, pd.DataFrame) and len(frame) == 2
    remote = frame.attrs["remote"]
    assert remote["uri"] == src.as_uri()
    assert len(remote["sha256"]) == 64
    assert remote["rows"] == 2
    assert remote["fetched_at"]
    # same loader policy as everywhere else: the sentinel survives the read
    assert (frame["amount"] == "N/A").sum() == 1
    # the cached copy is on disk, named by content
    assert list((tmp_path / "cache").glob("*.csv"))


def test_a_refetch_with_different_content_is_information_not_an_error(tmp_path):
    src = tmp_path / "remote.csv"
    src.write_text("a,b\n1,2\n")
    first = ingest.load_url(src.as_uri(), dest_dir=tmp_path / "cache")

    src.write_text("a,b\n1,2\n3,4\n")
    second = ingest.load_url(src.as_uri(), dest_dir=tmp_path / "cache")

    assert first.attrs["remote"]["sha256"] != second.attrs["remote"]["sha256"]
    assert len(second) == 2
