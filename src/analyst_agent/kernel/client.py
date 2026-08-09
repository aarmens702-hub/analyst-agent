"""Host-side kernel client: speaks protocol v1 over stdio (spec §2, R9–R15).

Transport is an argv the client attaches to with pipes — the Docker exec path
in production, or `[sys.executable, "-m", analyst_agent.kernel.supervisor]` to
run containerless over the identical wire format (how the conformance suite
runs). The client never imports jupyter_client; the protocol is its whole view
of the kernel.
"""

import base64
import itertools
import json
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
        cmd += ["--label", "analyst-agent"]
        cmd += ["-v", f"{self.workspace_dir.resolve()}:/workspace"]
        if self.data_dir:
            cmd += ["-v", f"{self.data_dir.resolve()}:/data:ro"]
        cmd += [self.image, "sleep", "infinity"]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self._container_id = out.stdout.strip()
        return [
            "docker", "exec", "-i", self._container_id,
            "python", "-m", "analyst_agent.kernel.supervisor",
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
