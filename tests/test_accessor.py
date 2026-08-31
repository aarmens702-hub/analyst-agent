"""The `df.crivo` DataFrame accessor — a thin, cheap forwarder onto the keyless
engines. Importing the module is enough to register `crivo` on every DataFrame;
the real work stays in api.diagnose and autoclean.clean, lazily imported so
this surface adds no import cost and no cycle.
"""

import importlib
import warnings

import pandas as pd

import crivo.accessor
import crivo.api
import crivo.autoclean


def _messy_df() -> pd.DataFrame:
    return pd.DataFrame({"amount": ["$1,200", "$3,400.50", "$15", "$980"] * 5})


def test_importing_the_module_registers_the_aa_accessor() -> None:
    """The registration is a side effect of import — no setup call needed."""
    assert isinstance(pd.DataFrame().crivo, crivo.accessor.AnalystAccessor)


def test_diagnose_forwards_to_api_diagnose() -> None:
    """`df.crivo.diagnose()` is exactly `api.diagnose(df)` — same findings."""
    df = _messy_df()

    via_accessor = df.crivo.diagnose()

    assert isinstance(via_accessor, crivo.api.Report)
    assert via_accessor.findings == crivo.api.diagnose(df).findings


def test_clean_forwards_to_autoclean_clean() -> None:
    """`df.crivo.clean()` returns the same (frame, summary) pair as
    autoclean.clean — verified by the fixes each reports applied."""
    df = _messy_df()

    cleaned, summary = df.crivo.clean()

    assert isinstance(cleaned, pd.DataFrame)
    assert isinstance(summary, crivo.autoclean.CleanSummary)
    assert summary.applied == crivo.autoclean.clean(df)[1].applied


def test_reregistration_is_guarded_against_the_pandas_warning() -> None:
    """A reload re-runs the module body. The guard must skip re-registration so
    pandas does not warn about overriding the preexisting 'crivo' accessor — and
    `df.crivo` must still resolve afterwards."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.reload(crivo.accessor)

    messages = [str(w.message) for w in caught]
    assert not any("registration of accessor" in m for m in messages), messages
    assert pd.DataFrame().crivo is not None
