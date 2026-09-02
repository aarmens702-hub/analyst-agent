"""Corpus specs for the Proving Ground (spec R5).

SMOKE is the CI-sized corpus: one seeded dataset per injectable disease plus
two compound mixes, every entry reproducible byte-for-byte from its seed.
full_corpus() expands the same mixes across many seeds for the on-demand
thousands-scale run — same shapes, more dirt, no new code paths.
"""

from __future__ import annotations

from bench import bases
from bench.corrupt import corrupt

_TX = ("transactions", {})
# diseases transactions can't host: contradictions need a start/end pair,
# broken coordinates need lat/lon
_PAIRED = (
    "typed_frame",
    {"spec": {"case_id": "id", "start": "start", "end": "end", "score": "numeric"}},
)
_GEO = (
    "typed_frame",
    {"spec": {"site_id": "id", "lat": "lat", "lon": "lon", "reading": "numeric"}},
)

SMOKE: list[dict] = [
    {"name": "tx-money-strings", "base": _TX, "diseases": [1], "seed": 101, "n": 250},
    {"name": "tx-dates-frozen", "base": _TX, "diseases": [2], "seed": 102, "n": 250},
    {"name": "tx-dates-mixed", "base": _TX, "diseases": [3], "seed": 103, "n": 250},
    {"name": "tx-sentinels", "base": _TX, "diseases": [4], "seed": 104, "n": 250},
    {"name": "tx-suppression", "base": _TX, "diseases": [5], "seed": 105, "n": 250},
    {"name": "tx-whitespace", "base": _TX, "diseases": [6], "seed": 106, "n": 250},
    {"name": "tx-case-variants", "base": _TX, "diseases": [7], "seed": 107, "n": 250},
    {"name": "tx-mojibake", "base": _TX, "diseases": [8], "seed": 108, "n": 250},
    {"name": "tx-dup-rows", "base": _TX, "diseases": [9], "seed": 109, "n": 250},
    {"name": "tx-near-dups", "base": _TX, "diseases": [10], "seed": 110, "n": 250},
    {"name": "tx-key-violations", "base": _TX, "diseases": [11], "seed": 111, "n": 250},
    {
        "name": "pairs-contradictions",
        "base": _PAIRED,
        "diseases": [12],
        "seed": 112,
        "n": 300,
    },
    {"name": "tx-out-of-domain", "base": _TX, "diseases": [13], "seed": 113, "n": 250},
    {
        "name": "geo-broken-coords",
        "base": _GEO,
        "diseases": [14],
        "seed": 114,
        "n": 300,
    },
    {"name": "tx-outliers", "base": _TX, "diseases": [15], "seed": 115, "n": 250},
    {"name": "tx-unit-mix", "base": _TX, "diseases": [16], "seed": 116, "n": 250},
    {"name": "tx-packed-fields", "base": _TX, "diseases": [17], "seed": 117, "n": 250},
    {"name": "tx-header-damage", "base": _TX, "diseases": [18], "seed": 118, "n": 250},
    {"name": "tx-constant-col", "base": _TX, "diseases": [19], "seed": 119, "n": 250},
    {"name": "tx-aggregate-row", "base": _TX, "diseases": [21], "seed": 121, "n": 250},
    {"name": "tx-excel-ids", "base": _TX, "diseases": [22], "seed": 122, "n": 250},
    # taxonomy v2 (arc W2): one entry per new plant; the date and truncation
    # entries use bases with no earlier-priority column so auto-pick lands
    # where the disease lives
    {
        "name": "flags-bool-chaos",
        "base": (
            "typed_frame",
            {"spec": {"row_id": "id", "active": "flag", "amount": "numeric"}},
        ),
        "diseases": [23],
        "seed": 123,
        "n": 250,
    },
    {"name": "tx-header-echo", "base": _TX, "diseases": [24], "seed": 124, "n": 250},
    {"name": "tx-dup-column", "base": _TX, "diseases": [25], "seed": 125, "n": 250},
    {
        "name": "notes-truncation",
        "base": (
            "typed_frame",
            {"spec": {"note_id": "id", "bio": "text", "amount": "numeric"}},
        ),
        "diseases": [26],
        "seed": 126,
        "n": 250,
    },
    {
        "name": "dates-implausible",
        "base": (
            "typed_frame",
            {"spec": {"case_id": "id", "seen_at": "datetime", "note": "text"}},
        ),
        "diseases": [13],
        "seed": 127,
        "n": 250,
    },
    {"name": "tx-excel-guard", "base": _TX, "diseases": [22], "seed": 128, "n": 250},
    # compound mixes: one deterministic-fixable, one deliberately beyond
    # FIXERS' reach — the honest-split aggregates need both kinds to exist
    {
        "name": "tx-compound-fixable",
        "base": _TX,
        "diseases": [4, 6, 1, 2],
        "seed": 131,
        "n": 350,
    },
    # header damage last: a rename would orphan later column-name lookups
    {
        "name": "tx-compound-hard",
        "base": _TX,
        "diseases": [7, 15, 9, 21, 18],
        "seed": 132,
        "n": 350,
    },
]


def build(entry: dict) -> tuple:
    """(pristine, dirty, truth) for one corpus entry — pure function of the
    entry dict, so two builds must agree byte-for-byte."""
    fn_name, kwargs = entry["base"]
    pristine = getattr(bases, fn_name)(seed=entry["seed"], n=entry["n"], **kwargs)
    dirty, truth = corrupt(
        pristine, entry["diseases"], seed=entry["seed"], base=entry["name"]
    )
    return pristine, dirty, truth


def full_corpus(seeds: int = 50) -> list[dict]:
    """Every SMOKE mix expanded across `seeds` fresh seeds (23 x 50 = 1150
    datasets by default; raise `seeds` for more). Names stay unique and each
    entry remains individually reproducible."""
    out = []
    for entry in SMOKE:
        for k in range(seeds):
            expanded = dict(entry)
            expanded["seed"] = entry["seed"] * 1000 + k
            expanded["name"] = f"{entry['name']}-s{k}"
            out.append(expanded)
    return out


__all__ = ["SMOKE", "build", "full_corpus"]
