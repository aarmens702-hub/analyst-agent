"""Host-side kernel client: speaks protocol v1 over stdio (spec §2, R9–R15).

Transport is an argv the client attaches to with pipes — the Docker exec path
in production, or `[sys.executable, "-m", crivo.kernel.supervisor]` to
run containerless over the identical wire format (how the conformance suite
runs). The client never imports jupyter_client; the protocol is its whole view
of the kernel.
"""

import base64
import itertools
import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

PROTO = 1
GRACE_S = 10
OUTER_SLACK_S = 30

DOCKER_RUN = [
    "docker", "run", "-d",
    "--network", "none",
    "--memory", "2g", "--cpus", "1.5", "--pids-limit", "128",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    "--read-only", "--tmpfs", "/tmp",
    "--user", "1000:1000",
]  # fmt: skip

KERNEL_ENV_PASSTHROUGH = (
    # process basics the interpreter and anything it spawns need
    "PATH", "HOME", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE",
    # local time. Dropping it does not fail, it silently disagrees: the cell
    # reports one zone and the CLI printing the answer reports another
    "TZ",
    # how the supervisor's interpreter resolves crivo and its virtualenv
    "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
    # jupyter_client: kernelspec discovery, config, connection files
    "JUPYTER_PATH", "JUPYTER_DATA_DIR", "JUPYTER_CONFIG_DIR", "JUPYTER_CONFIG_PATH",
    "JUPYTER_RUNTIME_DIR", "JUPYTER_PLATFORM_DIRS", "JUPYTER_PREFER_ENV_PATH",
    "XDG_DATA_HOME", "XDG_CONFIG_HOME",
    "IPYTHONDIR", "MPLCONFIGDIR",
    # the operator's own reader tuning, read inside the cell by
    # readers.remote (crivo.readers.remote.READER_ENV_VARS). Without these a
    # cell silently uses the defaults while remote.py's refusal tells the user
    # to raise a limit that no longer reaches it
    "CRIVO_HTTP_TIMEOUT_S", "CRIVO_HTTP_MAX_BYTES",
    # where a cell's own TLS trust comes from. A wrong-looking certificate is
    # an opaque verify failure, not a timeout, so this is worth carrying
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
)  # fmt: skip

# Only the docker transport gets these, and only because the child there is
# the docker CLI on the host, not a process running model-authored code. In
# subprocess mode the child IS that process: DOCKER_CONFIG points at registry
# credentials, DOCKER_CERT_PATH at a TLS client key, and SSH_AUTH_SOCK is a
# live agent that will sign with the user's keys on request.
DOCKER_ENV_PASSTHROUGH = (
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
    "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
    "SSH_AUTH_SOCK",  # DOCKER_HOST=ssh://... has no other way to authenticate
)  # fmt: skip

# HTTP_PROXY / HTTPS_PROXY / NO_PROXY are deliberately absent: a proxy URL
# routinely carries user:password, and nothing here would notice, because the
# name is not key-shaped. A cell behind a corporate egress therefore cannot
# fetch. That is a known cost, not an oversight.


def _child_env(docker: bool = False) -> dict[str, str]:
    """The environment for the kernel transport: an allowlist, never inheritance.

    Model-authored code runs in that process and can read os.environ, so the
    host's secrets must not be in it: `load_dotenv` puts DEEPSEEK_API_KEY and
    ANTHROPIC_API_KEY there for the whole session. Only names the supervisor,
    jupyter_client, or a cell actually reads are carried over; a name the host
    has not set stays unset rather than becoming empty.

    This is not containment. In subprocess mode the child is a host process
    with this user's files, so a cell that wants the key can read .env
    directly. Trimming the environment removes the easy route, and the
    sandbox is what removes the rest.
    """
    names = KERNEL_ENV_PASSTHROUGH + (DOCKER_ENV_PASSTHROUGH if docker else ())
    return {k: os.environ[k] for k in names if k in os.environ}


@dataclass(frozen=True)
class HelloInfo:
    proto: int
    python: str
    ipykernel: str


@dataclass(frozen=True)
class StreamOut:
    name: str
    text: str


@dataclass(frozen=True)
class DisplayItem:
    mime: str
    payload: str  # artifact path for saved images, text otherwise
    dropped: bool = False


@dataclass(frozen=True)
class ExecResult:
    status: str
    value: str | None = None
    error: dict | None = None
    registry: list = field(default_factory=list)
    registry_omitted: int = 0
    exec_count: int = 0
    elapsed_s: float = 0.0
    truncated: dict = field(default_factory=dict)


KernelEvent = StreamOut | DisplayItem | ExecResult


class KernelClient:
    def __init__(
        self, workspace_dir, transport_argv=None, image="analyst-kernel", data_dir=None
    ):
        self.workspace_dir = Path(workspace_dir)
        self.transport_argv = transport_argv
        self.image = image
        self.data_dir = Path(data_dir) if data_dir else None
        self.artifacts_dir = self.workspace_dir / "artifacts"
        self._proc = None
        self._container_id = None
        self._ids = itertools.count(1)
        self._queues: dict[int, queue.Queue] = {}
        self._queues_lock = threading.Lock()
        self._stderr_file = None

    def start(self) -> HelloInfo:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        docker = self.transport_argv is None  # else the child is the docker CLI
        argv = self.transport_argv or self._container_argv()
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._stderr_file = (self.workspace_dir / "supervisor.stderr.log").open("ab")
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=_child_env(docker),
        )
        threading.Thread(target=self._read_events, daemon=True).start()
        result = self._request({"op": "hello"}, timeout=90)
        if result.get("status") != "ok" or result.get("proto") != PROTO:
            self.close()
            raise RuntimeError(f"hello failed or proto mismatch: {result}")
        return HelloInfo(
            proto=result["proto"],
            python=result.get("python", ""),
            ipykernel=result.get("ipykernel", ""),
        )

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._send({"id": next(self._ids), "op": "shutdown"})
                self._proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                self._proc.kill()
        self._rm_container()
        if self._stderr_file:
            self._stderr_file.close()
            self._stderr_file = None

    def _container_argv(self) -> list[str]:
        cmd = list(DOCKER_RUN)
        cmd += ["--label", "crivo"]
        cmd += ["-v", f"{self.workspace_dir.resolve()}:/workspace"]
        if self.data_dir:
            cmd += ["-v", f"{self.data_dir.resolve()}:/data:ro"]
        cmd += [self.image, "sleep", "infinity"]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self._container_id = out.stdout.strip()
        return [
            "docker", "exec", "-i", self._container_id,
            "python", "-m", "crivo.kernel.supervisor",
        ]  # fmt: skip

    def _rm_container(self) -> None:
        if self._container_id:
            subprocess.run(
                ["docker", "rm", "-f", self._container_id],
                capture_output=True,
                check=False,
            )
            self._container_id = None

    def execute(self, code: str, timeout_s: float = 120) -> Iterator[KernelEvent]:
        rid = self._register()
        deadline = time.monotonic() + timeout_s + GRACE_S + OUTER_SLACK_S
        self._send({"id": rid, "op": "execute", "code": code, "timeout_s": timeout_s})
        image_n = itertools.count(1)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill_transport()
                    yield ExecResult(status="kernel_died")
                    return
                try:
                    msg = self._queues[rid].get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    if self._proc.poll() is not None:
                        yield ExecResult(status="kernel_died")
                        return
                    continue
                ev = msg.get("ev")
                if ev == "stream":
                    yield StreamOut(msg.get("name", "stdout"), msg.get("text", ""))
                elif ev == "display":
                    yield self._display_item(msg, rid, image_n)
                elif ev == "result":
                    yield ExecResult(
                        status=msg.get("status", "error"),
                        value=msg.get("value"),
                        error=msg.get("error"),
                        registry=msg.get("registry", []),
                        registry_omitted=msg.get("registry_omitted", 0),
                        exec_count=msg.get("exec_count", 0),
                        elapsed_s=msg.get("elapsed_s", 0.0),
                        truncated=msg.get("truncated", {}),
                    )
                    return
        finally:
            self._unregister(rid)

    def interrupt(self) -> None:
        self._request({"op": "interrupt"}, timeout=30)

    def restart(self) -> None:
        self._request({"op": "restart"}, timeout=180)

    # -- internals -----------------------------------------------------------

    def _register(self) -> int:
        rid = next(self._ids)
        with self._queues_lock:
            self._queues[rid] = queue.Queue()
        return rid

    def _unregister(self, rid: int) -> None:
        with self._queues_lock:
            self._queues.pop(rid, None)

    def _send(self, obj: dict) -> None:
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _send_raw(self, line: str) -> None:
        """Conformance-test seam: write an arbitrary raw line to the supervisor."""
        self._proc.stdin.write(line if line.endswith("\n") else line + "\n")
        self._proc.stdin.flush()

    def _request(self, op: dict, timeout: float) -> dict:
        rid = self._register()
        deadline = time.monotonic() + timeout
        try:
            self._send({"id": rid, **op})
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"no result for op {op.get('op')!r} within {timeout}s"
                    )
                try:
                    return self._queues[rid].get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    if self._proc.poll() is not None:
                        raise RuntimeError(
                            f"supervisor exited (rc={self._proc.returncode}) before "
                            f"answering {op.get('op')!r}; see supervisor.stderr.log"
                        ) from None
        finally:
            self._unregister(rid)

    def _read_events(self) -> None:
        try:
            for line in self._proc.stdout:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                with self._queues_lock:
                    q = self._queues.get(msg.get("id"))
                if q is not None:
                    q.put(msg)
        except ValueError:
            pass  # pipe closed during shutdown

    def _display_item(self, msg: dict, rid: int, image_n) -> DisplayItem:
        mime = msg.get("mime", "text/plain")
        if msg.get("dropped"):
            return DisplayItem(mime, f"(dropped: {msg.get('bytes', 0)} bytes)", True)
        if mime == "image/png":
            path = self.artifacts_dir / f"cell_{rid}_{next(image_n)}.png"
            path.write_bytes(base64.b64decode(msg.get("b64", "")))
            return DisplayItem(mime, str(path))
        return DisplayItem(mime, msg.get("text", ""))

    def _kill_transport(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
        self._rm_container()
