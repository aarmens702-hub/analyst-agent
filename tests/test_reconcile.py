"""Keyed row reconciliation (P7 harder-data, design spec decision 3).

The keyed twin of `compare`: `compare` diffs table shape, `reconcile` diffs the
rows themselves matched by a key, sorting them into added / removed / changed /
unchanged with a partition receipt. These tests pin the contract on crafted
frames: the four buckets, composite keys, duplicate-key exclusion, null-key
exclusion, key identity by value, the missing key error, disjoint columns, the
receipt as a real partition invariant, bounded evidence (rows and columns), and
keyless import.
"""

import importlib

import pandas as pd
import pytest

import crivo
from crivo.reconcile_report import reconcile_report

# crivo.reconcile is now the public wrapper function, so reach the submodule
# object explicitly to test its internals (_EXAMPLE_CAP, _COLUMN_CAP).
reconcile = importlib.import_module("crivo.reconcile")


def test_basic_added_removed_changed_unchanged():
    # ids 1..4 vs 2..5: 1 removed, 5 added, id 3 changed, ids 2 and 4 unchanged
    a = pd.DataFrame({"id": [1, 2, 3, 4], "val": [10, 20, 30, 40]})
    b = pd.DataFrame({"id": [2, 3, 4, 5], "val": [20, 99, 40, 50]})

    result = reconcile.reconcile(a, b, "id")

    assert result["keys"] == ["id"]
    assert result["counts"] == {
        "added": 1,
        "removed": 1,
        "changed": 1,
        "unchanged": 2,
        "duplicate_keys": 0,
        "null_keys": 0,
    }
    assert result["added"] == [(5,)]
    assert result["removed"] == [(1,)]
    assert result["receipt"] is True

    (change,) = result["changed"]
    assert change["key"] == (3,)
    assert change["columns"] == ["val"]
    assert change["before"] == {"val": 30}
    assert change["after"] == {"val": 99}


def test_composite_multi_column_keys():
    # a two-column key: (region, sku) identifies a row
    a = pd.DataFrame(
        {
            "region": ["west", "west", "east"],
            "sku": ["A", "B", "A"],
            "qty": [1, 2, 3],
        }
    )
    b = pd.DataFrame(
        {
            "region": ["west", "west", "north"],
            "sku": ["A", "B", "C"],
            "qty": [1, 5, 9],
        }
    )

    result = reconcile.reconcile(a, b, ["region", "sku"])

    assert result["keys"] == ["region", "sku"]
    assert result["counts"] == {
        "added": 1,
        "removed": 1,
        "changed": 1,
        "unchanged": 1,
        "duplicate_keys": 0,
        "null_keys": 0,
    }
    assert result["added"] == [("north", "C")]
    assert result["removed"] == [("east", "A")]
    (change,) = result["changed"]
    assert change["key"] == ("west", "B")
    assert change["columns"] == ["qty"]
    assert change["before"] == {"qty": 2}
    assert change["after"] == {"qty": 5}
    assert result["receipt"] is True


def test_duplicate_keys_excluded_from_buckets_and_reported():
    # id 1 repeats in a, so it cannot be matched one to one: excluded from the
    # four buckets, reported in duplicate_keys, and the receipt stays exact
    a = pd.DataFrame({"id": [1, 1, 2], "val": [10, 10, 20]})
    b = pd.DataFrame({"id": [1, 2, 3], "val": [10, 20, 30]})

    result = reconcile.reconcile(a, b, "id")

    assert result["counts"] == {
        "added": 1,
        "removed": 0,
        "changed": 0,
        "unchanged": 1,
        "duplicate_keys": 1,
        "null_keys": 0,
    }
    assert result["duplicate_keys"] == [(1,)]
    # the duplicate key never leaks into any bucket
    assert (1,) not in result["added"]
    assert (1,) not in result["removed"]
    assert all(change["key"] != (1,) for change in result["changed"])
    # id 3 is the sole add, id 2 the sole unchanged row
    assert result["added"] == [(3,)]
    assert result["receipt"] is True
    assert any("duplicate" in note for note in result["notes"])


def test_missing_key_column_raises_value_error():
    a = pd.DataFrame({"id": [1, 2], "val": [1, 2]})
    b = pd.DataFrame({"identifier": [1, 2], "val": [1, 2]})

    # the key is absent from b, and the message names it
    with pytest.raises(ValueError, match="id"):
        reconcile.reconcile(a, b, "id")

    # and absent from a
    a2 = pd.DataFrame({"other": [1, 2], "val": [1, 2]})
    b2 = pd.DataFrame({"id": [1, 2], "val": [1, 2]})
    with pytest.raises(ValueError, match="id"):
        reconcile.reconcile(a2, b2, "id")


def test_disjoint_columns_compared_on_shared_only():
    # x is shared and comparable; only_a and only_b are one-sided and must be
    # reported as column_diff, never scored as cell changes
    a = pd.DataFrame({"id": [1, 2], "x": [5, 5], "only_a": ["p", "q"]})
    b = pd.DataFrame({"id": [1, 2], "x": [5, 9], "only_b": ["r", "s"]})

    result = reconcile.reconcile(a, b, "id")

    assert result["column_diff"] == {
        "only_in_a": ["only_a"],
        "only_in_b": ["only_b"],
        "compared": ["x"],
    }
    # only id 2 changed, and only on the shared column x
    assert result["counts"]["changed"] == 1
    assert result["counts"]["unchanged"] == 1
    (change,) = result["changed"]
    assert change["key"] == (2,)
    assert change["columns"] == ["x"]
    assert "only_a" not in change["before"]
    assert "only_b" not in change["after"]
    assert result["receipt"] is True


def test_partition_receipt_sums_to_distinct_non_duplicate_keys():
    # a frame with adds, removes, a change, an unchanged row, and a duplicate;
    # the four buckets must partition exactly the distinct non-duplicate keys
    a = pd.DataFrame({"id": [1, 2, 3, 3, 4], "val": [10, 20, 30, 30, 40]})
    b = pd.DataFrame({"id": [2, 4, 5, 6], "val": [99, 40, 50, 60]})

    result = reconcile.reconcile(a, b, "id")

    # id 3 is duplicated in a, so it is excluded from the key space entirely.
    # distinct non-duplicate keys across a and b: {1, 2, 4, 5, 6} => 5
    distinct_non_duplicate = 5
    counts = result["counts"]
    assert (
        counts["added"] + counts["removed"] + counts["changed"] + counts["unchanged"]
        == distinct_non_duplicate
    )
    assert result["receipt"] is True
    assert counts["duplicate_keys"] == 1


def test_example_lists_are_bounded_with_exact_counts():
    cap = reconcile._EXAMPLE_CAP

    # every b row is an add (a shares no ids), well past the cap
    n_added = cap + 5
    a = pd.DataFrame({"id": [-1], "val": [0]})
    b = pd.DataFrame({"id": list(range(n_added)), "val": list(range(n_added))})

    result = reconcile.reconcile(a, b, "id")
    assert result["counts"]["added"] == n_added
    assert len(result["added"]) == cap  # list bounded, count exact
    assert any("added" in note and "truncated" in note for note in result["notes"])

    # every shared row changed, also past the cap
    n_changed = cap + 3
    a2 = pd.DataFrame({"id": list(range(n_changed)), "val": [0] * n_changed})
    b2 = pd.DataFrame({"id": list(range(n_changed)), "val": [1] * n_changed})

    result2 = reconcile.reconcile(a2, b2, "id")
    assert result2["counts"]["changed"] == n_changed
    assert len(result2["changed"]) == cap
    assert any("changed" in note and "truncated" in note for note in result2["notes"])
    assert result2["receipt"] is True


def test_nulls_match_and_are_not_a_change():
    # a shared key whose compared cell is null on both sides is unchanged, not
    # a change; a null against a present value is a change
    a = pd.DataFrame({"id": [1, 2], "val": [None, 5]})
    b = pd.DataFrame({"id": [1, 2], "val": [None, 6]})

    result = reconcile.reconcile(a, b, "id")

    assert result["counts"]["unchanged"] == 1  # id 1: null == null
    assert result["counts"]["changed"] == 1  # id 2: 5 -> 6
    (change,) = result["changed"]
    assert change["key"] == (2,)


def test_null_key_rows_are_excluded_and_reported_not_split_across_buckets():
    # regression: a float NaN key used to slip matching (NaN != NaN), so a
    # null-keyed row could land in BOTH added and removed while the receipt
    # stayed True. A null in a key cell is not a valid one-to-one identifier,
    # so the row is now excluded from the four buckets and reported in
    # null_keys, the same discipline as a duplicate key.
    a = pd.DataFrame({"id": [1.0, float("nan"), float("nan")], "val": [10, 20, 30]})
    b = pd.DataFrame({"id": [1.0, float("nan")], "val": [10, 99]})

    result = reconcile.reconcile(a, b, "id")

    # id 1.0 is the only matchable key and is unchanged; every NaN-keyed row is
    # excluded and the repeated NaN collapses to a single reported null key
    assert result["counts"] == {
        "added": 0,
        "removed": 0,
        "changed": 0,
        "unchanged": 1,
        "duplicate_keys": 0,
        "null_keys": 1,
    }
    assert result["null_keys"] == [(None,)]  # a null key shows as None
    # the null key never leaks into any bucket (the old both-buckets bug)
    assert result["added"] == []
    assert result["removed"] == []
    assert all(change["key"] != (None,) for change in result["changed"])
    assert result["receipt"] is True
    assert any("null" in note for note in result["notes"])


def test_key_identity_is_by_value_bool_distinct_int_matches_float():
    # documented key identity: an integral float is the same key as that int,
    # so an id column that drifted from int to float across two extracts still
    # lines up; a bool is kept distinct from 0/1 so a boolean column never
    # silently merges with a numeric one.

    # (a) int keys in a match integral-float keys in b
    a = pd.DataFrame({"id": [1, 2, 3], "val": [10, 20, 30]})
    b = pd.DataFrame({"id": [1.0, 2.0, 3.0], "val": [10, 20, 30]})
    r = reconcile.reconcile(a, b, "id")
    assert r["counts"] == {
        "added": 0,
        "removed": 0,
        "changed": 0,
        "unchanged": 3,
        "duplicate_keys": 0,
        "null_keys": 0,
    }
    assert r["receipt"] is True

    # (b) a bool key does not collapse with the int 1 into a fabricated
    # duplicate: 1 and True are two distinct keys, so both rows match unchanged
    a2 = pd.DataFrame({"id": pd.Series([1, True], dtype=object), "val": [10, 20]})
    b2 = pd.DataFrame({"id": pd.Series([1, True], dtype=object), "val": [10, 20]})
    r2 = reconcile.reconcile(a2, b2, "id")
    assert r2["counts"]["duplicate_keys"] == 0
    assert r2["counts"]["unchanged"] == 2
    assert r2["receipt"] is True

    # (c) 1 and 1.0 are the SAME key by value, so a frame carrying both has a
    # genuine duplicate id, reported not silently collapsed
    a3 = pd.DataFrame({"id": pd.Series([1, 1.0], dtype=object), "val": [10, 20]})
    b3 = pd.DataFrame({"id": [2], "val": [30]})
    r3 = reconcile.reconcile(a3, b3, "id")
    assert r3["counts"]["duplicate_keys"] == 1
    assert r3["duplicate_keys"] == [(1,)]
    assert r3["receipt"] is True


def test_receipt_is_a_real_disjoint_cover_over_the_whole_key_space():
    # a rich frame exercising every group at once: an add, a remove, a change,
    # an unchanged row, a duplicate key, and a null key. The receipt is now a
    # disjoint-cover invariant, not a cardinality identity, so it verifies that
    # every reported key lands in exactly one place.
    a = pd.DataFrame(
        {"id": [1, 2, 3, 4, 4, float("nan")], "val": [10, 20, 30, 40, 40, 50]}
    )
    b = pd.DataFrame({"id": [2, 3, 5, float("nan")], "val": [20, 99, 60, 70]})

    r = reconcile.reconcile(a, b, "id")

    assert r["counts"] == {
        "added": 1,  # id 5
        "removed": 1,  # id 1
        "changed": 1,  # id 3: 30 -> 99
        "unchanged": 1,  # id 2
        "duplicate_keys": 1,  # id 4 repeats in a
        "null_keys": 1,  # NaN in both frames, one reported null key
    }
    assert r["receipt"] is True
    # every reported key falls in exactly one group, nothing double-counted
    reported = (
        r["added"]
        + r["removed"]
        + [change["key"] for change in r["changed"]]
        + r["duplicate_keys"]
        + r["null_keys"]
    )
    assert len(reported) == len(set(reported))


def test_changed_row_before_after_are_bounded_in_width():
    # regression: the changed ROW list was capped, but before/after inside a
    # changed entry listed EVERY differing column with no cap and no note, so a
    # wide row was a full-width dump. The differing columns are now capped and
    # the truncation is noted.
    cap = reconcile._COLUMN_CAP
    n_cols = cap + 20
    a = pd.DataFrame({"id": [1], **{f"c{i}": [0] for i in range(n_cols)}})
    b = pd.DataFrame({"id": [1], **{f"c{i}": [1] for i in range(n_cols)}})

    result = reconcile.reconcile(a, b, "id")

    assert result["counts"]["changed"] == 1  # still one changed row, count exact
    (change,) = result["changed"]
    assert len(change["columns"]) == cap  # column list bounded
    assert len(change["before"]) == cap  # before dict bounded
    assert len(change["after"]) == cap  # after dict bounded
    assert any("column" in note for note in result["notes"])
    assert result["receipt"] is True


def test_import_is_keyless_and_pulls_no_core_module():
    import os
    import subprocess
    import sys

    code = (
        "import sys, crivo.reconcile\n"
        "bad=[m for m in ('crivo.loop','crivo.prompts','crivo.skills',"
        "'crivo.provenance','crivo.llm') if m in sys.modules]\n"
        "print(bad); sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_reconcile_surface_is_exported():
    # reconcile and its HTML twin are wired onto the top-level package and named
    # in crivo.__all__, the same public-surface contract as compare and drivers.
    # Both resolve to the callable wrappers, not the same-named submodules.
    assert {"reconcile", "reconcile_report"} <= set(crivo.__all__)
    assert callable(crivo.reconcile)
    assert callable(crivo.reconcile_report)


# --- reconcile_report: the self-contained HTML render (fast-follow) ---
# The keyed twin of the compare report. These pin the render contract: one
# self-contained document (no external asset, no script), the four bucket
# counts, a changed row with its changed columns and before-to-after, the
# duplicate-key note and column diff, and HTML escaping of hostile values.


def test_report_is_one_self_contained_document():
    a = pd.DataFrame({"id": [1, 2, 3], "val": [10, 20, 30]})
    b = pd.DataFrame({"id": [2, 3, 4], "val": [20, 99, 40]})
    html = reconcile_report(a, b, "id")
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "</html>" in html
    # self-contained: nothing to fetch, no script to run, emailable
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html.lower()


def test_report_shows_a_changed_row_and_its_changed_columns():
    # id 3 changes val 30 -> 99; the changed key, the changed column, and the
    # before-to-after values must all appear in the rendered document
    a = pd.DataFrame({"id": [1, 2, 3], "val": [10, 20, 30]})
    b = pd.DataFrame({"id": [1, 2, 3], "val": [10, 20, 99]})
    html = reconcile_report(a, b, "id")
    assert "<code>3</code>" in html  # the changed row's key
    assert "<code>val</code>" in html  # the differing column
    assert "30 &rarr; 99" in html  # before -> after


def test_report_shows_the_four_bucket_counts():
    # id 1 removed, id 5 added, id 4 changed (40 -> 99), ids 2 and 3 unchanged
    a = pd.DataFrame({"id": [1, 2, 3, 4], "val": [10, 20, 30, 40]})
    b = pd.DataFrame({"id": [2, 3, 4, 5], "val": [20, 30, 99, 50]})
    html = reconcile_report(a, b, "id")
    for label in ("added", "removed", "changed", "unchanged"):
        assert f"<th>{label}</th>" in html
    # the unchanged count (2) is rendered in the buckets row
    assert '<td class="delta flat">2</td>' in html


def test_report_surfaces_duplicate_key_note_and_column_diff():
    # id 1 repeats in a (a duplicate key: excluded and noted); only_a / only_b
    # are one-sided columns and land in the column diff, x is the compared one
    a = pd.DataFrame({"id": [1, 1, 2], "x": [5, 5, 5], "only_a": ["p", "q", "r"]})
    b = pd.DataFrame({"id": [1, 2, 3], "x": [5, 9, 1], "only_b": ["s", "t", "u"]})
    html = reconcile_report(a, b, "id")
    assert "duplicate" in html.lower()  # the exclusion is surfaced as a note
    assert "<code>x</code>" in html  # compared
    assert "<code>only_a</code>" in html  # only in before
    assert "<code>only_b</code>" in html  # only in after


def test_report_escapes_hostile_values():
    # a hostile value in a changed cell must be escaped, never injected as markup
    a = pd.DataFrame({"id": [1], "note": ["clean"]})
    b = pd.DataFrame({"id": [1], "note": ["<script>alert(1)</script>"]})
    html = reconcile_report(a, b, "id")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
