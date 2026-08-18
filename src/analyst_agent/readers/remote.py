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
            try:
                records = records[key]
            except (KeyError, TypeError, IndexError) as exc:
                raise ValueError(
                    f"records_path {records_path!r} does not fit the JSON from "
                    f"{url}: no {key!r} at that level"
                ) from exc
        return pd.DataFrame(records).astype(object)
    return pd.read_csv(io.StringIO(text), keep_default_na=False, dtype=str, **kwargs)


def _fetch(url: str) -> tuple[str, str]:
    """Fetch the response body (capped) as text plus its Content-Type. A network
    failure raises a clear error, never a bare urllib error or silent timeout."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            text = response.read(MAX_FETCH_BYTES).decode(charset, errors="replace")
            content_type = response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        # a real HTTP status (404/500/…): the server answered, so don't blame the
        # sandbox — HTTPError is a URLError subclass and must be caught first
        raise RuntimeError(
            f"fetch failed for {url}: HTTP {exc.code} {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"fetch failed for {url}: {exc.reason} — host unreachable (in the "
            "docker sandbox, --network none makes remote fetches fail by design)"
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
