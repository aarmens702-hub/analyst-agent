"""Remote (HTTP) reader — the http(s) branch of `readers.read`, a keyless
`read_url(url)` that returns a DataFrame.

Mirrors ingest.py's fetch discipline (stdlib urllib only, a User-Agent, a byte
cap, and a clear error under `--network none` instead of a bare timeout) but
returns a frame straight from the response body rather than caching to disk.

Format is decided by the response Content-Type first (application/json vs
text/csv/text/plain), then the URL's .json/.csv extension, so a server that
answers with a generic type still routes correctly. Sentinels survive on both
paths — CSV via keep_default_na=False/dtype=str, JSON via .astype(object) —
because the detection engine can only report a missing value it can still see.
"""

import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

USER_AGENT = "analyst-agent-remote/0.1"
MAX_FETCH_BYTES = 512 * 1024 * 1024


def read_url(url, records_path=None, **kwargs) -> pd.DataFrame:
    """Fetch `url` and return a DataFrame, format inferred from Content-Type then
    the URL extension. For JSON, `records_path` is a dotted path (e.g.
    'data.items') walked into the parsed object to the records list."""
    text, content_type = _fetch(url)
    if _decide_format(content_type, url) == "json":
        records = json.loads(text)
        for key in records_path.split(".") if records_path else []:
            records = records[key]
        return pd.DataFrame(records).astype(object)
    return pd.read_csv(io.StringIO(text), keep_default_na=False, dtype=str, **kwargs)


def _fetch(url: str) -> tuple[str, str]:
    """Fetch the response body (capped) as text plus its Content-Type. A network
    failure raises a clear error, never a bare urllib error or silent timeout."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request) as response:
            text = response.read(MAX_FETCH_BYTES).decode()
            content_type = response.headers.get_content_type()
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"fetch failed for {url}: {exc.reason} — in the docker sandbox "
            "(--network none) remote fetches are unreachable by design"
        ) from exc
    return text, content_type


def _decide_format(content_type: str, url: str) -> str:
    """Content-Type first (application/json vs text/csv/text/plain), then fall
    back to the URL's .json/.csv extension."""
    if content_type == "application/json":
        return "json"
    if content_type in {"text/csv", "text/plain"}:
        return "csv"
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"
    raise ValueError(
        f"cannot decide format for {url}: content-type {content_type!r} is not "
        "json/csv and the URL has no .json/.csv extension"
    )
