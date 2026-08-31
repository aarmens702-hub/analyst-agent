"""Remote ingestion as kernel-resident functions (P5 R11).

`tax = load_url("https://...")` runs as a gated code cell, logged and
provenance-tracked exactly like `read_csv` — never a parallel tool channel,
which would produce an action with no code, no gate, and no lineage node,
leaving `/why` to call the result trusted because it cannot see what it does
not know.

R12's contract: an S3 object can be overwritten and a query differs tomorrow,
so a remote source records more and claims less — the URI, the fetch time,
the row count, and a content hash of what actually arrived, stamped on the
frame for the lineage node to consume. Re-fetching to a different hash is
information, not an error.

In the docker sandbox (`--network none`) a fetch fails by design; the error
says so instead of timing out silently.
"""

import hashlib
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from crivo.checkup import load as _load_file

USER_AGENT = "crivo-ingest/0.1"
MAX_FETCH_BYTES = 512 * 1024 * 1024


def load_url(url: str, dest_dir="data/remote"):
    """Fetch a remote table, cache it by content hash, and load it with the
    same policy every other path uses (sniffed delimiter, sentinels kept,
    encoding fallback said out loud)."""
    if url.startswith("s3://"):
        raise ValueError(
            "s3:// needs boto3 credentials this kernel does not carry; "
            "use the object's https URL, or fetch it outside and /load the file"
        )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request) as response:
            payload = response.read(MAX_FETCH_BYTES)
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"fetch failed for {url}: {exc.reason} — in the docker sandbox "
            "(--network none) remote fetches are unreachable by design"
        ) from exc

    digest = hashlib.sha256(payload).hexdigest()
    suffix = Path(url.split("?", 1)[0]).suffix or ".csv"
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    cached = dest / f"{digest[:16]}{suffix}"
    cached.write_bytes(payload)

    frame = _load_file(cached)
    frame.attrs["remote"] = {
        "uri": url,
        "fetched_at": datetime.now(UTC).isoformat(),
        "sha256": digest,
        "rows": len(frame),
        "cached": str(cached),
    }
    return frame
