"""The remote (HTTP) reader: `read_url` fetches a URL with stdlib urllib and
returns a sentinel-safe DataFrame, format decided by Content-Type then the URL
extension. The servers here serve real bytes over localhost so the fetch path is
genuinely exercised — no urllib mocking."""

import functools
import json
import threading
from http.server import (
    BaseHTTPRequestHandler,
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)

import pytest


@pytest.fixture
def http_server(tmp_path):
    """A localhost HTTP server over a temp dir holding `data.json` (an object
    with a nested records array) and `data.csv` (with an 'N/A' sentinel). Yields
    the base URL and shuts the server down after."""
    (tmp_path / "data.json").write_text(
        json.dumps(
            {
                "data": {
                    "items": [
                        {"id": "1", "city": "Vancouver"},
                        {"id": "2", "city": "Toronto"},
                    ]
                }
            }
        )
    )
    (tmp_path / "data.csv").write_text("id,note\n1,ok\n2,N/A\n")

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class _ControlHandler(BaseHTTPRequestHandler):
    """Serves a body and Content-Type each test shapes, and records the last
    request's User-Agent — so the fetch discipline (header, byte cap, format
    decision) can be asserted against real bytes."""

    body = b""
    content_type = "application/octet-stream"
    last_user_agent = None

    def do_GET(self):
        cls = type(self)
        cls.last_user_agent = self.headers.get("User-Agent")
        self.send_response(200)
        self.send_header("Content-Type", cls.content_type)
        self.end_headers()
        self.wfile.write(cls.body)

    def log_message(self, *args):  # keep the test output quiet
        pass


@pytest.fixture
def control_server():
    """A localhost server whose one response every test shapes via
    `_ControlHandler` class attributes. Yields (base_url, handler_class)."""
    _ControlHandler.body = b""
    _ControlHandler.content_type = "application/octet-stream"
    _ControlHandler.last_user_agent = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ControlHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", _ControlHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_read_url_json_walks_records_path(http_server) -> None:
    """A JSON object with a nested records array: `records_path='data.items'`
    walks into the parsed object to that list and builds the frame from it."""
    from crivo.readers.remote import read_url

    df = read_url(f"{http_server}/data.json", records_path="data.items")

    assert list(df.columns) == ["id", "city"]
    assert df["city"].tolist() == ["Vancouver", "Toronto"]


def test_read_url_csv_keeps_sentinel(http_server) -> None:
    """A CSV served over HTTP: rows come back and the 'N/A' cell survives as a
    string the detection engine can see, not silently coerced to NaN."""
    from crivo.readers.remote import read_url

    df = read_url(f"{http_server}/data.csv")

    assert list(df.columns) == ["id", "note"]
    assert (df["note"] == "N/A").sum() == 1


def test_read_url_json_toplevel_array_without_records_path(
    http_server, tmp_path
) -> None:
    """A top-level JSON array with no `records_path` is used as the records list
    directly."""
    from crivo.readers.remote import read_url

    (tmp_path / "arr.json").write_text(json.dumps([{"a": "1"}, {"a": "2"}]))

    df = read_url(f"{http_server}/arr.json")

    assert df["a"].tolist() == ["1", "2"]


def test_read_url_network_failure_raises_clear_error() -> None:
    """A fetch that cannot connect raises a clear RuntimeError naming the source,
    not a bare urllib error or a silent timeout (the `--network none` sandbox)."""
    import socket

    from crivo.readers.remote import read_url

    # Bind a port, then close it, so the connection is refused.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()

    with pytest.raises(RuntimeError, match="fetch failed"):
        read_url(f"http://127.0.0.1:{closed_port}/data.json", records_path="data")


def test_read_url_sends_a_user_agent(control_server) -> None:
    """The request carries our own User-Agent, not urllib's default — the fetch
    discipline mirrors ingest.py."""
    from crivo.readers import remote

    base, handler = control_server
    handler.content_type = "application/json"
    handler.body = json.dumps([{"a": "1"}]).encode()

    remote.read_url(f"{base}/data.json")

    assert handler.last_user_agent == remote.USER_AGENT


def test_read_url_caps_bytes_read(control_server, monkeypatch) -> None:
    """The fetch reads at most MAX_FETCH_BYTES, mirroring ingest.py's cap: a body
    past the cap is truncated, not read in full."""
    from crivo.readers import remote

    base, handler = control_server
    handler.content_type = "text/csv"
    handler.body = b"id,note\n1,a\n2,b\n3,c\n"
    monkeypatch.setattr(
        remote, "MAX_FETCH_BYTES", len(b"id,note\n1,a\n"), raising=False
    )

    df = remote.read_url(f"{base}/data.csv")

    assert df["note"].tolist() == ["a"]


def test_read_url_decides_format_by_content_type_then_extension(
    control_server,
) -> None:
    """Content-Type decides first (application/json vs text/csv/text/plain);
    a generic type falls back to the URL's .json/.csv extension; neither raises
    a clear ValueError."""
    from crivo.readers.remote import read_url

    base, handler = control_server

    # Generic content-type: the .json extension routes it to JSON.
    handler.content_type = "application/octet-stream"
    handler.body = json.dumps([{"a": "1"}]).encode()
    assert read_url(f"{base}/x.json")["a"].tolist() == ["1"]

    # Generic content-type: the .csv extension routes it to CSV (sentinel kept).
    handler.content_type = "application/octet-stream"
    handler.body = b"a\nN/A\n"
    assert read_url(f"{base}/x.csv")["a"].tolist() == ["N/A"]

    # text/plain is treated as CSV even with no useful extension.
    handler.content_type = "text/plain"
    handler.body = b"a\nhi\n"
    assert read_url(f"{base}/x")["a"].tolist() == ["hi"]

    # Content-Type wins over extension: application/json on a .csv URL is JSON.
    handler.content_type = "application/json"
    handler.body = json.dumps([{"a": "1"}]).encode()
    assert read_url(f"{base}/x.csv")["a"].tolist() == ["1"]

    # Neither a known content-type nor a known extension: a clear error.
    handler.content_type = "application/octet-stream"
    handler.body = b"mystery"
    with pytest.raises(ValueError):
        read_url(f"{base}/mystery")
