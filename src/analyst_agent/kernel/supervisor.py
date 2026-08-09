"""In-container NDJSON kernel supervisor (spec §2, R9–R17).

Requests arrive as one JSON object per stdin line; events leave on stdout,
every one tagged with the request id; stderr is diagnostics only. Runs
identically inside the container and as a plain subprocess (conformance
tests). The kernel is driven through jupyter_client locally — this process is
the only thing that ever talks to it.
"""

import ast
import json
import platform
import queue
import re
import sys
import threading
import time
from importlib import metadata

from jupyter_client import KernelManager

from analyst_agent.kernel.bootstrap import BOOTSTRAP_SOURCE

PROTO = 1
IOPUB_POLL_S = 0.05
GRACE_S = 10
REGISTRY_EXPR = {"__registry__": "_analyst_registry_json()"}
VALUE_CAP = 8 * 1024
STREAM_CAP = 64 * 1024
TB_HEAD, TB_TAIL = 5, 30
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _clean_traceback(lines: list[str]) -> list[str]:
    lines = [_strip_ansi(ln) for ln in lines]
    if len(lines) <= TB_HEAD + TB_TAIL:
        return lines
    omitted = len(lines) - TB_HEAD - TB_TAIL
    marker = f"… ({omitted} traceback lines omitted) …"
    return lines[:TB_HEAD] + [marker] + lines[-TB_TAIL:]


def _cap_value(value: str | None) -> tuple[str | None, bool]:
    if value is None or len(value) <= VALUE_CAP:
        return value, False
    omitted = len(value) - 5800 - 1500
    marker = f"\n… ({omitted} chars omitted, 8 KiB cap) …\n"
    return value[:5800] + marker + value[-1500:], True


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _stdin_reader(reqq: queue.Queue) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reqq.put(("req", json.loads(line)))
        except json.JSONDecodeError:
            reqq.put(("malformed", line))
    reqq.put(("eof", None))


class Supervisor:
    def __init__(self) -> None:
        self.km = KernelManager(kernel_name="python3")
        self.kc = None
        self.reqq: queue.Queue = queue.Queue()
        self._deferred: list = []
        self._client_interrupted = False
        self._watchdog_fired = False
        self._hung = False

    def run(self) -> None:
        self.km.start_kernel()
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=120)
        self._run_silent(BOOTSTRAP_SOURCE)
        threading.Thread(target=_stdin_reader, args=(self.reqq,), daemon=True).start()
        while True:
            if self._deferred:
                kind, req = self._deferred.pop(0)
            else:
                kind, req = self.reqq.get()
            if kind == "eof":
                break
            if kind == "malformed":
                self._bad_request(None, "malformed JSON line")
                continue
            rid, op = req.get("id"), req.get("op")
            if op == "hello":
                _emit(
                    {
                        "id": rid,
                        "ev": "result",
                        "status": "ok",
                        "proto": PROTO,
                        "python": platform.python_version(),
                        "ipykernel": metadata.version("ipykernel"),
                    }
                )
            elif op == "execute":
                if self._hung:
                    self._result(rid, "hung", None, None, 0, time.monotonic())
                else:
                    self._execute(rid, req)
            elif op == "interrupt":
                self.km.interrupt_kernel()
                _emit({"id": rid, "ev": "result", "status": "ok"})
            elif op == "restart":
                self._restart(rid)
            elif op == "shutdown":
                _emit({"id": rid, "ev": "result", "status": "ok"})
                break
            else:
                self._bad_request(rid, f"unknown op: {op!r}")
        self._teardown()

    def _bad_request(self, rid, reason: str) -> None:
        _emit(
            {
                "id": rid,
                "ev": "result",
                "status": "bad_request",
                "error": {"ename": "BadRequest", "evalue": reason, "traceback": []},
            }
        )

    def _poll_control(self) -> None:
        """Handle requests arriving while an execute is in flight (R9)."""
        try:
            kind, req = self.reqq.get_nowait()
        except queue.Empty:
            return
        if kind == "eof":
            self._deferred.append((kind, req))
            return
        if kind == "malformed":
            self._bad_request(None, "malformed JSON line")
            return
        op, rid = req.get("op"), req.get("id")
        if op == "execute":
            self._bad_request(rid, "an execute is already in flight")
        elif op == "interrupt":
            self.km.interrupt_kernel()
            self._client_interrupted = True
            _emit({"id": rid, "ev": "result", "status": "ok"})
        else:
            self._deferred.append((kind, req))

    # -- execute -------------------------------------------------------------

    def _execute(self, rid, req: dict) -> None:
        code = req.get("code", "")
        timeout_s = float(req.get("timeout_s", 120))
        t0 = time.monotonic()
        msg_id = self.kc.execute(code, user_expressions=REGISTRY_EXPR)
        value, error, exec_count = None, None, 0
        stream_bytes, stream_capped = 0, False
        self._client_interrupted = False
        self._watchdog_fired = False
        grace_deadline = None
        while True:
            self._poll_control()
            now = time.monotonic()
            if not self._watchdog_fired and now - t0 > timeout_s:
                self.km.interrupt_kernel()
                self._watchdog_fired = True
                grace_deadline = now + GRACE_S
            if grace_deadline is not None and now > grace_deadline:
                self._hung = True
                self._result(rid, "hung", None, None, exec_count, t0)
                return
            try:
                msg = self.kc.get_iopub_msg(timeout=IOPUB_POLL_S)
            except queue.Empty:
                if not self.km.is_alive():
                    self._result(rid, "kernel_died", value, error, exec_count, t0)
                    return
                continue
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            mtype, content = msg["msg_type"], msg["content"]
            if mtype == "stream":
                text = _strip_ansi(content["text"])
                stream_bytes += len(text)
                if stream_bytes > STREAM_CAP:
                    stream_capped = True  # stop relaying; keep draining to idle
                else:
                    _emit(
                        {
                            "id": rid,
                            "ev": "stream",
                            "name": content["name"],
                            "text": text,
                        }
                    )
            elif mtype == "display_data":
                self._display(rid, content.get("data", {}))
            elif mtype == "execute_result":
                exec_count = content.get("execution_count") or exec_count
                value = content.get("data", {}).get("text/plain")
            elif mtype == "error":
                error = {
                    "ename": content["ename"],
                    "evalue": _strip_ansi(content["evalue"]),
                    "traceback": _clean_traceback(content["traceback"]),
                }
            elif mtype == "status" and content["execution_state"] == "idle":
                break
        reply = self._shell_reply(msg_id)
        registry, omitted = self._registry_from(reply)
        if reply and not exec_count:
            exec_count = reply["content"].get("execution_count") or 0
        status = "error" if error else "ok"
        if error and error["ename"] == "KeyboardInterrupt":
            if self._watchdog_fired:
                status, error = "timeout", None
            elif self._client_interrupted:
                status, error = "interrupted", None
        value, value_capped = _cap_value(value)
        truncated = {}
        if value_capped:
            truncated["value"] = True
        if stream_capped:
            truncated["stream"] = True
        self._result(
            rid, status, value, error, exec_count, t0,
            registry=registry, registry_omitted=omitted, truncated=truncated,
        )  # fmt: skip

    def _display(self, rid, data: dict) -> None:
        if "image/png" in data:
            _emit(
                {
                    "id": rid,
                    "ev": "display",
                    "mime": "image/png",
                    "b64": data["image/png"].replace("\n", ""),
                }
            )
        elif "text/plain" in data:
            _emit(
                {
                    "id": rid,
                    "ev": "display",
                    "mime": "text/plain",
                    "text": data["text/plain"],
                }
            )

    def _result(
        self, rid, status, value, error, exec_count, t0,
        registry=None, registry_omitted=0, truncated=None,
    ) -> None:  # fmt: skip
        _emit(
            {
                "id": rid,
                "ev": "result",
                "status": status,
                "value": value,
                "error": error,
                "exec_count": exec_count,
                "elapsed_s": round(time.monotonic() - t0, 3),
                "registry": registry or [],
                "registry_omitted": registry_omitted,
                "truncated": truncated or {},
            }
        )

    # -- helpers -------------------------------------------------------------

    def _restart(self, rid) -> None:
        try:
            self.kc.stop_channels()
        except Exception as exc:  # noqa: BLE001 — old channels may be wedged
            print(f"restart: stop_channels: {exc}", file=sys.stderr)
        self.km.restart_kernel(now=True)
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=120)
        self._run_silent(BOOTSTRAP_SOURCE)
        self._hung = False
        _emit({"id": rid, "ev": "result", "status": "ok", "registry": []})

    def _run_silent(self, code: str) -> None:
        msg_id = self.kc.execute(code, silent=True, store_history=False)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                msg = self.kc.get_iopub_msg(timeout=IOPUB_POLL_S)
            except queue.Empty:
                continue
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            if msg["msg_type"] == "error":
                print(f"bootstrap error: {msg['content']}", file=sys.stderr)
            if (
                msg["msg_type"] == "status"
                and msg["content"]["execution_state"] == "idle"
            ):
                return

    def _shell_reply(self, msg_id) -> dict | None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                reply = self.kc.get_shell_msg(timeout=0.5)
            except queue.Empty:
                continue
            if reply["parent_header"].get("msg_id") == msg_id:
                return reply
        return None

    def _registry_from(self, reply: dict | None) -> tuple[list, int]:
        try:
            expr = reply["content"]["user_expressions"]["__registry__"]
            if expr["status"] != "ok":
                return [], 0
            payload = json.loads(ast.literal_eval(expr["data"]["text/plain"]))
            return payload.get("entries", []), payload.get("omitted", 0)
        except (KeyError, TypeError, ValueError, SyntaxError):
            return [], 0

    def _teardown(self) -> None:
        try:
            self.kc.stop_channels()
            self.km.shutdown_kernel(now=True)
        except Exception as exc:  # noqa: BLE001 — dying anyway, log and go
            print(f"teardown: {exc}", file=sys.stderr)


def main() -> None:
    Supervisor().run()


if __name__ == "__main__":
    main()
