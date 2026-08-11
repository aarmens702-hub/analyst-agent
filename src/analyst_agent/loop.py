"""The turn loop: Session implements SessionLike (spec §1, R1-R8).

run_turn() is a UI-agnostic generator. It yields typed events; drivers render
them and answer GateRequest via gen.send(GateDecision). Repair is not a
mechanism here — a traceback is an ordinary observation and the loop continues.
"""

import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

from analyst_agent import llm, prompts
from analyst_agent.card import AnswerCard, lift_checks
from analyst_agent.events import (
    ArtifactSaved,
    CardReady,
    GateDecision,
    GateRequest,
    Notice,
    StreamText,
)
from analyst_agent.kernel.client import DisplayItem, KernelClient, StreamOut
from analyst_agent.transcript import Transcript

MAX_ITERS = 6
EXEC_TIMEOUT_S = 120
OBS_CLIP = 2000
VALUE_PREVIEW = 300

EXEC_RE = re.compile(r"<execute>(.*?)</execute>", re.DOTALL)
ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL)

LOAD_TEMPLATE = """\
import pandas as pd
from analyst_agent.profile import profile_df
{name} = pd.read_csv({path!r}, encoding="utf-8-sig")
print(profile_df({name}, {name!r}))
"""


def parse_tags(text: str) -> tuple[str, str]:
    """Return ("execute"|"answer"|"malformed", body) per R1/R2."""
    execs = EXEC_RE.findall(text)
    answers = ANSWER_RE.findall(text)
    if len(execs) == 1 and not answers:
        return "execute", execs[0].strip()
    if len(answers) == 1 and not execs:
        return "answer", answers[0].strip()
    return "malformed", ""


def _clip(text: str | None, limit: int = OBS_CLIP) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.7), int(limit * 0.25)
    omitted = len(text) - head - tail
    return f"{text[:head]}\n… ({omitted} chars omitted) …\n{text[-tail:]}"


class Session:
    """One analyst session: kernel + transcript + conversation state."""

    def __init__(
        self,
        workspace: str = "workspace",
        data_dir: str = "data",
        docker: bool = False,
        transport_argv: list | None = None,
    ) -> None:
        self.workspace_root = Path(workspace)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.session_id = self._next_session_id()
        self.session_dir = self.workspace_root / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir)
        self.docker = docker

        if transport_argv is None and not docker:
            transport_argv = [sys.executable, "-m", "analyst_agent.kernel.supervisor"]
        self.client = KernelClient(
            workspace_dir=self.session_dir,
            transport_argv=transport_argv,
            data_dir=self.data_dir if docker else None,
        )
        print(f"starting kernel ({'docker' if docker else 'subprocess'}) …")
        hello = self.client.start()

        self.transcript = Transcript(self.session_dir / "transcript.jsonl")
        self.transcript.append(
            "session_meta",
            session=self.session_id,
            model=llm.model_info(),
            python=hello.python,
            ipykernel=hello.ipykernel,
        )
        print(
            f"session {self.session_id} · kernel python {hello.python} · "
            f"model {llm.model_name()}"
        )

        self.history: list[dict] = []  # model conversation after the system prompt
        self.datasets: list[dict] = []  # lineage entries (path, sha256, variable, ev)
        self.loads: list[tuple[str, str]] = []  # (name, code) for crash replay
        self.origins: dict[str, int] = {}  # variable -> creating transcript ev
        self._registry: list[dict] = []
        self._registry_prev: dict[str, tuple] = {}
        self.card_seq = 0

    # -- SessionLike ---------------------------------------------------------

    def load(self, path: str, name: str | None = None) -> None:
        src = Path(path)
        if not src.exists():
            print(f"load: no such file: {path}")
            return
        name = name or re.sub(r"\W+", "_", src.stem).strip("_") or "df"
        code = LOAD_TEMPLATE.format(name=name, path=self._kernel_path(src))

        stream_parts: list[str] = []
        result = None
        for ev in self.client.execute(code, timeout_s=EXEC_TIMEOUT_S):
            if isinstance(ev, StreamOut):
                stream_parts.append(ev.text)
            elif not isinstance(ev, DisplayItem):
                result = ev
        ev_id = self.transcript.append(
            "exec",
            code=code,
            status=result.status,
            value=result.value,
            error=result.error,
            exec_count=result.exec_count,
            kind_note="load",
        )
        if result.status != "ok":
            err = result.error or {}
            print(f"load failed: {err.get('ename')}: {err.get('evalue')}")
            return

        profile = "".join(stream_parts).strip()
        self._stamp_registry(result.registry, ev_id)
        self.datasets.append(
            {
                "path": str(src),
                "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                "variable": name,
                "loaded_event": ev_id,
            }
        )
        self.loads.append((name, code))
        self.history.append(
            {
                "role": "user",
                "content": f"<dataset variable={name!r}>\n{profile}\n</dataset>",
            }
        )
        print(profile)
        print(f"loaded {src} → {name} (ev {ev_id})")

    def run_turn(self, question: str):
        q_ev = self.transcript.append("user", text=question)
        self.history.append({"role": "user", "content": question})
        cells: list[dict] = []
        exec_evs: list[int] = []
        flags = {
            "capped": False,
            "malformed_answer": False,
            "truncated": False,
            "unchecked": False,
        }
        answer_text = None
        iters, nudged, deaths = 0, False, 0
        checks_bounced = False

        while iters < MAX_ITERS:
            try:
                resp = yield from self._generate_streaming()
            except Exception as exc:  # noqa: BLE001 — surface, end turn cleanly
                yield Notice("llm_error", f"{type(exc).__name__}: {exc}")
                return
            self.transcript.append("model", text=resp)
            self.history.append({"role": "assistant", "content": resp})
            kind, body = parse_tags(resp)

            if kind == "malformed":
                if not nudged:
                    nudged = True  # first one is free (R2)
                    yield Notice("nudge", "malformed response — free retry")
                else:
                    iters += 1
                self.history.append({"role": "user", "content": prompts.NUDGE_PROMPT})
                continue

            if kind == "answer":
                last_ok = next(
                    (c for c in reversed(cells) if c.get("status") == "ok"), None
                )
                if last_ok and not lift_checks(last_ok["code"]) and not checks_bounced:
                    checks_bounced = True  # R18: cards must not ship unchecked
                    self.history.append(
                        {"role": "user", "content": prompts.CHECKS_PROMPT}
                    )
                    continue
                answer_text = body
                break

            iters += 1
            decision = yield GateRequest(body, iters)
            if not isinstance(decision, GateDecision):
                decision = GateDecision("run")
            gate_ev = self.transcript.append(
                "gate", action=decision.action, note=decision.note
            )
            if decision.action == "reject":
                cells.append(
                    {
                        "event_id": gate_ev,
                        "exec_count": None,
                        "code": body,
                        "status": None,
                        "gate": {"rejected": decision.note},
                        "value_preview": None,
                        "display_paths": [],
                    }
                )
                self.history.append(
                    {
                        "role": "user",
                        "content": (
                            "<observation>user rejected the cell: "
                            f"{decision.note}</observation>"
                        ),
                    }
                )
                continue

            cell, status = yield from self._execute_cell(body)
            cells.append(cell)
            if cell["event_id"] is not None:
                exec_evs.append(cell["event_id"])
            if cell.get("truncated"):
                flags["truncated"] = True

            if status in ("kernel_died", "hung"):
                deaths += 1
                if deaths > 1:
                    break
                yield Notice(
                    "kernel_died" if status == "kernel_died" else "restart_offer",
                    f"kernel {status} — restarting and replaying loads",
                )
                self._restart_and_replay(dead=status == "kernel_died")
                self.history.append(
                    {
                        "role": "user",
                        "content": (
                            "<observation>the kernel was restarted; datasets were "
                            "reloaded, but other variables are gone</observation>"
                        ),
                    }
                )

        if answer_text is None:
            flags["capped"] = True
            yield Notice("cap", f"cell budget ({MAX_ITERS}) reached — forcing answer")
            self.history.append(
                {"role": "user", "content": prompts.FORCED_ANSWER_PROMPT}
            )
            resp = ""
            for _attempt in range(2):
                try:
                    resp = yield from self._generate_streaming()
                except Exception as exc:  # noqa: BLE001
                    yield Notice("llm_error", f"{type(exc).__name__}: {exc}")
                    resp = f"(model unavailable: {exc})"
                    break
                self.transcript.append("model", text=resp)
                self.history.append({"role": "assistant", "content": resp})
                kind, body = parse_tags(resp)
                if kind == "answer":
                    answer_text = body
                    break
                self.history.append({"role": "user", "content": prompts.NUDGE_PROMPT})
            if answer_text is None:
                flags["malformed_answer"] = True
                answer_text = resp

        final_ok = next((c for c in reversed(cells) if c.get("status") == "ok"), None)
        checks = lift_checks(final_ok["code"]) if final_ok else []
        flags["unchecked"] = bool(final_ok) and not checks
        self.card_seq += 1
        card_id = f"{self.session_id}-c{self.card_seq:03d}"
        card_ev = self.transcript.append(
            "card",
            card_id=card_id,
            answer=answer_text,
            checks=checks,
            cell_events=exec_evs,
        )
        card = AnswerCard(
            card_id=card_id,
            session=self.session_id,
            question=question,
            answer=answer_text,
            cells=cells,
            checks=checks,
            lineage={
                "datasets": list(self.datasets),
                "event_chain": [q_ev, *exec_evs, card_ev],
            },
            model=llm.model_info(),
            flags=flags,
            created=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        card.save(self.session_dir / "cards")
        yield CardReady(card)

    def close(self) -> None:
        try:
            self.transcript.append("session_meta", event="close")
        finally:
            self.client.close()

    # -- internals -----------------------------------------------------------

    def _generate_streaming(self):
        parts: list[str] = []
        for chunk in llm.generate(self._context()):
            parts.append(chunk)
            yield StreamText("model", chunk)
        yield StreamText("model", "\n")
        return "".join(parts)

    def _execute_cell(self, code: str):
        stream_parts: list[str] = []
        display_paths: list[str] = []
        result = None
        for ev in self.client.execute(code, timeout_s=EXEC_TIMEOUT_S):
            if isinstance(ev, StreamOut):
                stream_parts.append(ev.text)
                yield StreamText(ev.name, ev.text)
            elif isinstance(ev, DisplayItem):
                if ev.mime == "image/png" and not ev.dropped:
                    display_paths.append(ev.payload)
                    yield ArtifactSaved(ev.payload)
                else:
                    stream_parts.append(f"[display] {ev.payload}")
            else:
                result = ev
        ev_id = self.transcript.append(
            "exec",
            code=code,
            status=result.status,
            value=result.value,
            error=result.error,
            exec_count=result.exec_count,
            elapsed_s=result.elapsed_s,
            artifacts=display_paths,
            truncated=result.truncated,
        )
        delta = self._stamp_registry(result.registry, ev_id)
        self.history.append(
            {
                "role": "user",
                "content": self._observation(
                    ev_id, result, "".join(stream_parts), display_paths, delta
                ),
            }
        )
        cell = {
            "event_id": ev_id,
            "exec_count": result.exec_count,
            "code": code,
            "status": result.status,
            "gate": "run",
            "value_preview": _clip(result.value, VALUE_PREVIEW),
            "display_paths": display_paths,
            "truncated": result.truncated,
        }
        return cell, result.status

    def _observation(self, ev_id, result, stream_text, display_paths, delta) -> str:
        header = (
            f'<observation cell_ev="{ev_id}" status="{result.status}" '
            f'exec_count="{result.exec_count}">'
        )
        lines = [header]
        if result.value:
            lines.append("value:\n" + _clip(result.value))
        if stream_text.strip():
            lines.append("stdout/stderr:\n" + _clip(stream_text))
        if result.error:
            tb = "\n".join(result.error.get("traceback", []))
            lines.append(
                f"error: {result.error.get('ename')}: {result.error.get('evalue')}\n"
                + _clip(tb)
            )
        if display_paths:
            names = ", ".join(Path(p).name for p in display_paths)
            lines.append(f"charts saved: {names}")
        if delta:
            lines.append("registry changes: " + "; ".join(delta))
        if result.truncated:
            lines.append(f"(outputs truncated by caps: {result.truncated})")
        lines.append("</observation>")
        return "\n".join(lines)

    def _context(self) -> list[dict]:
        return [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            *self.history,
            {"role": "user", "content": self._registry_block()},
        ]

    def _registry_block(self) -> str:
        if not self._registry:
            body = "(no variables yet)"
        else:
            rows = []
            for e in self._registry:
                size = e.get("shape") if e.get("shape") is not None else e.get("len")
                origin = self.origins.get(e["name"])
                rows.append(
                    f"{e['name']}: {e.get('type')} shape={size} "
                    f"mem_mb={e.get('mem_mb')} (ev {origin})"
                )
            body = "\n".join(rows)
        return f"<registry>\n{body}\n</registry>"

    def _stamp_registry(self, registry: list[dict], ev_id: int) -> list[str]:
        delta = []
        for entry in registry:
            name = entry["name"]
            key = (entry.get("type"), str(entry.get("shape") or entry.get("len")))
            if name not in self._registry_prev:
                self.origins[name] = ev_id
                delta.append(f"+ {name} ({key[0]} {key[1]})")
            elif self._registry_prev[name] != key:
                self.origins[name] = ev_id
                delta.append(f"~ {name} ({key[0]} {key[1]})")
        self._registry_prev = {
            e["name"]: (e.get("type"), str(e.get("shape") or e.get("len")))
            for e in registry
        }
        self._registry = registry
        return delta

    def _restart_and_replay(self, dead: bool) -> None:
        if dead:
            self.client.close()
            self.client.start()
        else:
            self.client.restart()
        self._registry_prev = {}
        for _name, code in self.loads:
            for _ev in self.client.execute(code, timeout_s=EXEC_TIMEOUT_S):
                pass

    def _kernel_path(self, src: Path) -> str:
        if not self.docker:
            return str(src.resolve())
        rel = src.resolve().relative_to(self.data_dir.resolve())
        return f"/data/{rel}"

    def _next_session_id(self) -> str:
        taken = {d.name for d in self.workspace_root.glob("s[0-9][0-9]")}
        n = 1
        while f"s{n:02d}" in taken:
            n += 1
        return f"s{n:02d}"
