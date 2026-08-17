"""The `df.aa` pandas accessor — a thin, cheap forwarder onto the keyless
engines.

`df.aa.diagnose()` and `df.aa.clean()` are the ergonomic front door for anyone
who already has a DataFrame in hand: they forward, unchanged, to
`analyst_agent.api.diagnose` and `analyst_agent.autoclean.clean`. Two rules keep
this surface honest:

- `__init__` stays cheap. Current pandas re-instantiates the accessor on *every*
  `df.aa` access (there is no caching), so construction does only validate +
  store; all real work lives in the methods.
- The engines are imported lazily, inside the methods, so importing this module
  stays side-effect-free and cycle-free (api imports autoclean, not us).

Importing this module registers the accessor. Registration is guarded so a
double import, ``%autoreload``, or a test reload does not trip pandas' "already
registered" UserWarning.
"""

import pandas as pd


class AnalystAccessor:
    """The object behind `df.aa`. Holds the frame, forwards to the engines."""

    def __init__(self, pandas_obj: pd.DataFrame):
        self._validate(pandas_obj)
        self._obj = pandas_obj

    @staticmethod
    def _validate(obj) -> None:
        # AttributeError (not TypeError) is the pandas accessor convention — it is
        # what pandas expects _validate to raise for a bad object.
        if not isinstance(obj, pd.DataFrame):
            raise AttributeError(  # noqa: TRY004 — pandas accessor _validate convention
                "the 'aa' accessor is only for DataFrames"
            )

    def diagnose(self, name: str | None = None):
        """Diagnose the frame against the 22-check engine — see api.diagnose."""
        from analyst_agent.api import diagnose  # lazy: keep import side-effect-free

        return diagnose(self._obj, name=name)

    def clean(self, policy: str = "auto"):
        """Deterministically clean the frame — see autoclean.clean."""
        from analyst_agent.autoclean import clean  # lazy: avoid an import cycle

        return clean(self._obj, policy=policy)


# Guard registration: pandas' register_dataframe_accessor emits a UserWarning
# every time the name already exists (double import, %autoreload, test reloads).
if getattr(pd.DataFrame, "aa", None) is None:
    pd.api.extensions.register_dataframe_accessor("aa")(AnalystAccessor)
