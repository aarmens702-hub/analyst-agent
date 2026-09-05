"""Side-by-side dataset comparison report (capability roadmap B3.3). One
self-contained HTML file (inline styles, no external URLs, no script) that
surfaces the row-count delta, columns added and removed, dtype changes, and a
compact before-to-after of shared numeric columns. Pure, no new dependency."""

import pandas as pd

from crivo.compare_report import compare_to_html


def test_output_is_one_self_contained_document():
    before = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    after = pd.DataFrame({"a": [1, 2, 3], "b": [5, 6, 7]})
    html = compare_to_html(before, after)
    assert html.lstrip().startswith("<!doctype html>")
    assert "</html>" in html
    # self-contained: nothing to fetch, no script to run, emailable
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html.lower()


def test_added_column_is_listed_under_added():
    before = pd.DataFrame({"a": [1]})
    after = pd.DataFrame({"a": [1], "email": ["x@y.com"]})
    html = compare_to_html(before, after)
    added = html.index("Columns added")
    removed = html.index("Columns removed")
    assert "email" in html[added:removed]


def test_removed_column_is_listed_under_removed():
    before = pd.DataFrame({"a": [1], "legacy_id": [9]})
    after = pd.DataFrame({"a": [1]})
    html = compare_to_html(before, after)
    removed = html.index("Columns removed")
    dtypes = html.index("Dtype changes")
    assert "legacy_id" in html[removed:dtypes]


def test_dtype_change_is_flagged():
    before = pd.DataFrame({"amt": [1, 2, 3]})
    after = pd.DataFrame({"amt": ["1", "2", "3"]})
    html = compare_to_html(before, after)
    dtypes = html.index("Dtype changes")
    numeric = html.index("Shared numeric columns")
    section = html[dtypes:numeric]
    assert "amt" in section
    # the two dtype names come straight from the frames, so the assertion is
    # robust across pandas string-dtype spellings
    assert str(before["amt"].dtype) in section
    assert str(after["amt"].dtype) in section


def test_row_count_delta_is_shown_with_sign():
    before = pd.DataFrame({"a": [1, 2, 3]})
    after = pd.DataFrame({"a": [1, 2, 3, 4, 5]})
    html = compare_to_html(before, after)
    assert "+2" in html


def test_hostile_column_name_is_escaped():
    before = pd.DataFrame({"a": [1]})
    after = pd.DataFrame({"a": [1], "<script>alert(1)</script>": [2]})
    html = compare_to_html(before, after)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_identical_frames_report_no_differences_gracefully():
    df = pd.DataFrame({"n": [1, 2, 3], "label": ["a", "b", "c"]})
    html = compare_to_html(df, df)
    assert "</html>" in html
    assert "No columns added." in html
    assert "No columns removed." in html
    assert "No dtype changes." in html
    assert "(0)" in html  # row count unchanged, delta reads zero
