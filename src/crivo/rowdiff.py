"""Keyed row reconciliation between two frames (P7 harder-data, design spec
decision 3).

The keyed twin of the `compare` report. Where `compare` diffs table SHAPE (row
count, columns, dtypes), `reconcile` diffs the ROWS themselves, matched by a
key. Given a before frame, an after frame, and the key column(s) that identify a
row, it partitions the key space into four buckets: added (keys only in the
after frame), removed (keys only in the before frame), changed (keys in both
whose shared non-key columns disagree), and unchanged (keys in both that agree).

Three disciplines keep it a receipts tool, not a guesser:

- Duplicate keys are never silently collapsed. A key value that repeats within a
  frame cannot be matched one to one, so it is pulled into its own reported set
  and excluded from the four buckets. That keeps the partition exact over the
  key space it does cover.
- Null keys are never silently matched. A null in any key cell (NaN, NaT, None)
  is not a valid one-to-one identifier, so a null-keyed row is pulled into its
  own reported set and excluded too, the same discipline as a duplicate. This is
  deliberate: two rows that both lack an id are not evidence of the same row,
  and a raw float NaN does not even equal itself, so leaving it in the key space
  would split one row across buckets.
- The partition is verified, not assumed. Every distinct key observed in either
  frame must land in exactly one place, one of the four buckets or one of the
  two excluded sets, with none left over and none in two places at once. That
  disjoint-cover invariant rides back on the result as `receipt`. It is a real
  check that can fail, not a cardinality identity that is true by construction,
  and it is null-safe because the key space is canonicalized before it runs.

Key identity is by value, with two documented normalizations. An integral float
is the same key as that integer, so an id column that drifted from int to float
between two extracts (3 and 3.0) still lines up. A bool is kept distinct from the
integers 0 and 1, so a boolean column never silently matches a numeric one.

Evidence is bounded. Example lists are capped, the differing columns inside a
changed row are capped, and full counts always accompany them, so a result is
never a full data dump. This is the data-first v1; the HTML render that reuses
the `compare` styling is a separate fast-follow. Keyless and pure: pandas plus
the standard library, no network, no key.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd

# example lists are capped so a result carries evidence, not a full dump; the
# counts beside them stay exact and a note fires whenever a list is truncated
_EXAMPLE_CAP = 50
# a single changed row reports at most this many differing columns, so a very
# wide row stays evidence too; the differing-column count is noted when capped
_COLUMN_CAP = 50


class _KeySentinel:
    """A unique, reflexive stand-in for a key cell that carries no usable
    value. Identity is the equality (the default object behaviour), so each
    sentinel compares equal only to itself, which is exactly what a float NaN
    fails to do. The repr is what shows up in a reported key tuple."""

    __slots__ = ("_label",)

    def __init__(self, label: str) -> None:
        self._label = label

    def __repr__(self) -> str:
        return self._label


# every null key cell canonicalizes to this one object, so repeated nulls are
# caught by the duplicate rule and a null key is reflexive (a float NaN is not)
_NULL = _KeySentinel("<null>")
# a bool canonicalizes to one of these, so True/False stay distinct from the
# ints 1/0 that Python's == and hash would otherwise merge them with
_TRUE = _KeySentinel("True")
_FALSE = _KeySentinel("False")


def _canon_cell(x):
    """Canonicalize one key cell so key identity is reflexive and by value.

    A null (NaN, NaT, None) becomes the single _NULL sentinel; a bool becomes
    _TRUE/_FALSE so it never merges with 1/0; an integral value (an int, or a
    float like 3.0) becomes that Python int so an int column and a float column
    reconcile. Everything else passes through unchanged."""
    if pd.isna(x):
        return _NULL
    if pd.api.types.is_bool(x):
        return _TRUE if bool(x) else _FALSE
    if pd.api.types.is_integer(x):
        return int(x)
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def _display_cell(x):
    """Map a canonical key cell back to a friendly value for a reported key:
    the null sentinel reads as None and the bool sentinels as True/False."""
    if x is _NULL:
        return None
    if x is _TRUE:
        return True
    if x is _FALSE:
        return False
    return x


def _key_tuples(df: pd.DataFrame, keys: list[str]) -> list[tuple]:
    """One canonical key tuple per row, in row order, so position i lines up
    with df.iloc[i]. A single-column key still yields a one-tuple, so every key
    is represented the same way regardless of arity. Cells are canonicalized
    (see _canon_cell) so the key space is null-safe and identity is by value."""
    raw = df[keys].itertuples(index=False, name=None)
    return [tuple(_canon_cell(x) for x in row) for row in raw]


def _cell_equal(x, y) -> bool:
    """Two cells are equal when both are missing or both hold the same value.
    Two nulls count as equal (an unchanged missing cell is not a change); one
    null against a present value does not."""
    x_na = pd.isna(x)
    y_na = pd.isna(y)
    if x_na or y_na:
        return bool(x_na and y_na)
    return bool(x == y)


def _display_key(k: tuple) -> tuple:
    """A reported key tuple, with the canonical sentinels mapped back to plain
    values (None for null, True/False for bool)."""
    return tuple(_display_cell(x) for x in k)


def _has_null(k: tuple) -> bool:
    """True when any cell of the canonical key tuple is the null sentinel."""
    return _NULL in k


def _sorted_keys(key_set) -> list[tuple]:
    """Stable, deterministic ordering of key tuples. Sorts by value when the
    keys are mutually comparable and falls back to a string ordering when they
    are not, so a mixed-type key space still orders deterministically instead of
    raising."""
    keys = list(key_set)
    try:
        return sorted(keys)
    except TypeError:
        return sorted(keys, key=lambda k: tuple(str(x) for x in k))


def _note_if_truncated(notes: list[str], label: str, shown: int, total: int) -> None:
    if total > shown:
        notes.append(f"{label} list truncated to {shown} of {total}")


def reconcile(a: pd.DataFrame, b: pd.DataFrame, keys: str | list[str]) -> dict:
    """Reconcile two frames row by row on a key (P7 design spec decision 3).

    `a` is the before frame, `b` the after frame, and `keys` the column or
    columns that identify a row. Returns a dict with:

      keys          the key columns, normalized to a list
      counts        {added, removed, changed, unchanged, duplicate_keys,
                    null_keys} ints
      added         key tuples present only in b (bounded example list)
      removed       key tuples present only in a (bounded example list)
      changed       bounded list of {key, columns, before, after}, where
                    `columns` are the shared non-key columns that disagree
                    (capped per row) and `before`/`after` map each such column
                    to its a/b value
      duplicate_keys key tuples that repeat within a or within b, excluded from
                    the four buckets (bounded example list)
      null_keys     key tuples carrying a null in any key cell, excluded from
                    the four buckets (bounded example list); a null shows as None
      column_diff   {only_in_a, only_in_b, compared} column-name lists
      receipt       True when every distinct key lands in exactly one bucket or
                    excluded set (a real disjoint-cover invariant, null-safe)
      notes         strings recording every truncation and every excluded set

    Every key is a tuple in `keys` order, so a single-column key is a one-tuple
    and a composite key is an n-tuple. Key identity is by value: an integral
    float matches the same int (3.0 matches 3) so an int/float dtype drift still
    reconciles, and a bool stays distinct from 0/1. The result is deterministic:
    example lists are sorted by key. A missing key column raises ValueError.
    """
    key_list = [keys] if isinstance(keys, str) else list(keys)

    missing_a = [k for k in key_list if k not in a.columns]
    missing_b = [k for k in key_list if k not in b.columns]
    if missing_a or missing_b:
        parts = []
        if missing_a:
            parts.append(f"a is missing {missing_a}")
        if missing_b:
            parts.append(f"b is missing {missing_b}")
        raise ValueError(f"key column(s) not found: {'; '.join(parts)}")

    a_keys = _key_tuples(a, key_list)
    b_keys = _key_tuples(b, key_list)

    # a null in any key cell is not a valid one-to-one identifier, so a
    # null-keyed row is excluded from every bucket and reported on its own, the
    # same discipline as a duplicate key. canonicalization made every null the
    # one _NULL sentinel, so repeated nulls collapse to a single reported key.
    null_key_set = {k for k in a_keys if _has_null(k)}
    null_key_set |= {k for k in b_keys if _has_null(k)}

    a_valid = [k for k in a_keys if not _has_null(k)]
    b_valid = [k for k in b_keys if not _has_null(k)]
    a_counts = Counter(a_valid)
    b_counts = Counter(b_valid)

    # a key that repeats within either frame cannot be matched one to one, so it
    # is excluded from every bucket and reported on its own
    duplicate = {k for k, n in a_counts.items() if n > 1}
    duplicate |= {k for k, n in b_counts.items() if n > 1}

    a_unique = {k for k in a_counts if k not in duplicate}
    b_unique = {k for k in b_counts if k not in duplicate}

    added_keys = b_unique - a_unique
    removed_keys = a_unique - b_unique
    both_keys = a_unique & b_unique

    a_cols = list(a.columns)
    b_cols = list(b.columns)
    a_col_set = set(a_cols)
    b_col_set = set(b_cols)
    key_set = set(key_list)
    # shared non-key columns are the only ones a row diff can compare; columns
    # on one side only are reported, never scored as cell changes
    compared = [c for c in a_cols if c in b_col_set and c not in key_set]
    only_in_a = [c for c in a_cols if c not in b_col_set and c not in key_set]
    only_in_b = [c for c in b_cols if c not in a_col_set and c not in key_set]

    # position lookups for the matchable rows, so iloc lines up with the key
    a_pos = {k: pos for pos, k in enumerate(a_keys) if k in a_unique}
    b_pos = {k: pos for pos, k in enumerate(b_keys) if k in b_unique}

    changed_examples: list[dict] = []
    changed_total = 0
    unchanged_total = 0
    wide_changed = 0
    widest_changed = 0
    for k in _sorted_keys(both_keys):
        a_row = a.iloc[a_pos[k]]
        b_row = b.iloc[b_pos[k]]
        diff_cols = [c for c in compared if not _cell_equal(a_row[c], b_row[c])]
        if diff_cols:
            changed_total += 1
            if len(changed_examples) < _EXAMPLE_CAP:
                # cap the differing columns so a wide changed row stays evidence,
                # not a full-width dump; the widest count rides back in a note
                shown = diff_cols[:_COLUMN_CAP]
                if len(diff_cols) > _COLUMN_CAP:
                    wide_changed += 1
                    widest_changed = max(widest_changed, len(diff_cols))
                changed_examples.append(
                    {
                        "key": _display_key(k),
                        "columns": shown,
                        "before": {c: a_row[c] for c in shown},
                        "after": {c: b_row[c] for c in shown},
                    }
                )
        else:
            unchanged_total += 1

    added_list = [_display_key(k) for k in _sorted_keys(added_keys)[:_EXAMPLE_CAP]]
    removed_list = [_display_key(k) for k in _sorted_keys(removed_keys)[:_EXAMPLE_CAP]]
    duplicate_list = [_display_key(k) for k in _sorted_keys(duplicate)[:_EXAMPLE_CAP]]
    null_list = [_display_key(k) for k in _sorted_keys(null_key_set)[:_EXAMPLE_CAP]]

    counts = {
        "added": len(added_keys),
        "removed": len(removed_keys),
        "changed": changed_total,
        "unchanged": unchanged_total,
        "duplicate_keys": len(duplicate),
        "null_keys": len(null_key_set),
    }

    # the receipt: a real disjoint-cover invariant over the whole key space,
    # not a cardinality identity. every distinct key observed in either frame
    # must land in exactly one group (a bucket or an excluded set), with none
    # missing and none counted twice, and the changed/unchanged split must
    # account for every matched key. canonicalization makes this null-safe, so
    # the float NaN key that used to slip the old check is caught here.
    observed = set(a_keys) | set(b_keys)
    placements: Counter = Counter()
    for group in (added_keys, removed_keys, both_keys, duplicate, null_key_set):
        placements.update(group)
    partition_ok = set(placements) == observed and all(
        n == 1 for n in placements.values()
    )
    classify_ok = changed_total + unchanged_total == len(both_keys)
    receipt = partition_ok and classify_ok

    notes: list[str] = []
    if duplicate:
        notes.append(
            f"{len(duplicate)} duplicate key(s) excluded from the row diff; "
            "a repeated key cannot be matched one to one"
        )
    if null_key_set:
        notes.append(
            f"{len(null_key_set)} null key(s) excluded from the row diff; "
            "a null key cannot identify a row one to one"
        )
    if both_keys and not compared:
        notes.append(
            "no shared non-key columns to compare; keys present in both frames "
            "are counted as unchanged"
        )
    if wide_changed:
        notes.append(
            f"{wide_changed} changed row(s) had a differing-column list capped "
            f"at {_COLUMN_CAP} of up to {widest_changed}"
        )
    _note_if_truncated(notes, "added", len(added_list), counts["added"])
    _note_if_truncated(notes, "removed", len(removed_list), counts["removed"])
    _note_if_truncated(notes, "changed", len(changed_examples), counts["changed"])
    _note_if_truncated(
        notes, "duplicate_keys", len(duplicate_list), counts["duplicate_keys"]
    )
    _note_if_truncated(notes, "null_keys", len(null_list), counts["null_keys"])

    return {
        "keys": key_list,
        "counts": counts,
        "added": added_list,
        "removed": removed_list,
        "changed": changed_examples,
        "duplicate_keys": duplicate_list,
        "null_keys": null_list,
        "column_diff": {
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
            "compared": compared,
        },
        "receipt": receipt,
        "notes": notes,
    }
