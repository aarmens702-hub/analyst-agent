"""Data-quality overview chart — the keyless matplotlib figure behind
`aa.diagnose(df).plot()`.

One horizontal bar per location that has findings (a column name, or "whole
table" for findings that are not column-specific), length = how many findings
sit there, coloured by the worst grade among them (HUMAN > GATE > AUTO), worst
at the top. Same 'verified ledger' grade palette as the notebook card. Returns
an Axes and never calls plt.show, so it renders the same in a notebook, a
script, or CI. matplotlib is imported lazily so `import analyst_agent` stays
cheap.
"""

GRADE_COLOR = {"AUTO": "#5cb98a", "GATE": "#d5a24f", "HUMAN": "#e05a54"}
_SEVERITY = {"AUTO": 0, "GATE": 1, "HUMAN": 2}


def _worst(grades) -> str:
    return max(grades, key=lambda g: _SEVERITY.get(g, 0))


def overview(name, findings, clear, ax=None):
    """Build the overview Axes from a diagnosis's findings + clear list."""
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    # group finding grades by location (a column, or "whole table")
    by_loc: dict[str, list[str]] = {}
    for finding in findings:
        for loc in finding["columns"] or ["whole table"]:
            by_loc.setdefault(loc, []).append(finding["grade"])

    if ax is None:
        _fig, ax = plt.subplots(figsize=(7, max(1.6, 0.4 * len(by_loc) + 1.1)))

    if not by_loc:
        ax.text(
            0.5,
            0.5,
            "no findings — every signal clear",
            ha="center",
            va="center",
            transform=ax.transAxes,
            color=GRADE_COLOR["AUTO"],
        )
        ax.set_axis_off()
        ax.set_title(f"{name} — data quality")
        return ax

    # worst grade first, then most findings; barh draws bottom-up, so this order
    # puts the worst location at the top
    items = sorted(
        by_loc.items(), key=lambda kv: (_SEVERITY[_worst(kv[1])], len(kv[1]))
    )
    labels = [loc for loc, _ in items]
    counts = [len(grades) for _, grades in items]
    colors = [GRADE_COLOR[_worst(grades)] for _, grades in items]

    ax.barh(range(len(labels)), counts, color=colors)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.set_xlabel("findings")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_title(
        f"{name} — {len(findings)} findings across {len(by_loc)} "
        f"column(s) · {len(clear)} signals clear"
    )
    return ax
