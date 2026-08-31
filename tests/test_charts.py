"""`aa.diagnose(df).plot()` — a keyless matplotlib data-quality overview.

Deterministic and headless: it returns an Axes (never calls plt.show), so it
renders in a notebook, a script, or CI the same way.
"""

import matplotlib

matplotlib.use("Agg")  # headless: no display needed to build or test the figure

import pandas as pd
from matplotlib.axes import Axes

import crivo as aa


def _messy() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "amount": ["$1,200", "$3,400.50", "$15", "$980"] * 5,
            "note": ["  spaced  ", "clean", "two  spaces", "x"] * 5,
        }
    )


def test_report_plot_returns_a_matplotlib_axes() -> None:
    ax = aa.diagnose(_messy()).plot()

    assert isinstance(ax, Axes)


def test_report_plot_draws_one_bar_per_affected_location() -> None:
    report = aa.diagnose(_messy())
    locations = {
        loc for f in report.findings for loc in (f["columns"] or ["whole table"])
    }

    ax = report.plot()

    assert locations, "the messy fixture should trip findings"
    assert len(ax.patches) == len(locations)


def test_overview_colours_each_bar_by_its_worst_grade() -> None:
    """A column with any HUMAN finding reads red even if it also has AUTO ones —
    severity, not recency, drives the colour."""
    import matplotlib.colors as mcolors

    from crivo import charts

    findings = [
        {"columns": ["x"], "grade": "AUTO"},
        {"columns": ["x"], "grade": "HUMAN"},  # worst on x
        {"columns": ["y"], "grade": "GATE"},
    ]

    ax = charts.overview("t", findings, [])

    # sorted worst-first (barh draws bottom-up): y (GATE) at index 0, x (HUMAN) at 1
    hexes = [mcolors.to_hex(p.get_facecolor()) for p in ax.patches]
    assert hexes == [charts.GRADE_COLOR["GATE"], charts.GRADE_COLOR["HUMAN"]]


def test_overview_renders_a_clean_empty_state() -> None:
    """No findings must render a tidy 'all clear' Axes, not crash on an empty
    bar set."""
    from crivo import charts

    ax = charts.overview("clean.csv", [], list(range(1, 23)))

    assert isinstance(ax, Axes)
    assert len(ax.patches) == 0
