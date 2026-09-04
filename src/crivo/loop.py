"""The turn loop: Session implements SessionLike (spec §1, R1-R8).

run_turn() is a UI-agnostic generator. It yields typed events; drivers render
them and answer GateRequest via gen.send(GateDecision). Repair is not a
mechanism here — a traceback is an ordinary observation and the loop continues.
"""

import ast
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from crivo import llm, policy, prompts, router, skills, snapshot, verify
from crivo import plan as plan_mod
from crivo.card import AnswerCard, lift_checks
from crivo.events import (
    ArtifactSaved,
    CardReady,
    GateDecision,
    GateRequest,
    Notice,
    StreamText,
)
from crivo.kernel.client import DisplayItem, KernelClient, StreamOut
from crivo.library import Library, unattended
from crivo.report import CleanReport
from crivo.transcript import Transcript

MAX_ITERS = 6
CLEAN_MAX_ATTEMPTS = 3
HEARTBEAT_EVERY = 20  # reasoning chunks per progress tick
SKILL_MAX_ATTEMPTS = 2  # one proposal, one revision (R6)
# Ranked candidates tried per finding (R11) before falling through to the
# model — bounds a large library to a few failing cells, not a long chain.
SKILL_ATTEMPT_CAP = 3
EXEC_TIMEOUT_S = 120
OBS_CLIP = 2000
VALUE_PREVIEW = 300
# R13: only QUERY turns accumulate (clean builds throwaway per-finding lists),
# and one oversized history used to fail every later turn until the process
# was killed. Past the threshold, everything but the tail is summarised.
COMPACT_AT_CHARS = 60_000
COMPACT_KEEP_TURNS = 8  # trailing messages kept verbatim


def _m1_enabled() -> bool:
    """CRIVO_M1=off restores the pre-M1 flow exactly (the bench legacy arm,
    T1.4 hunk D)."""
    return os.environ.get("CRIVO_M1", "on") != "off"


def _plan_first_enabled() -> bool:
    """CRIVO_PLAN_FIRST=on turns on M2-min: build a plan, approve it as one
    unit, arm a policy over its AUTO steps, then run M1's loop under it.
    Default off, so the shipped flow is unchanged until it is chosen
    (specs/2026-09-04-m2-core-packet.md, M2-min)."""
    return os.environ.get("CRIVO_PLAN_FIRST", "off") == "on"


def _plan_table(plan) -> str:
    """One line per step: order, executor, grade, expected check (T2.2)."""
    lines = [plan.summary()]
    for i, s in enumerate(plan.steps, 1):
        lines.append(
            f"  {i:2d}. {s.executor:9s} {s.grade:5s} d{s.disease:02d} "
            f"{s.expected_check}"
        )
    return "\n".join(lines)


class KernelLost(RuntimeError):
    """The kernel died or hung. Every cell after this one is meaningless,
    so callers must stop rather than read the failure as a bad result."""


EXEC_RE = re.compile(r"<execute>(.*?)</execute>", re.DOTALL)
ANSWER_RE = re.compile(r"<answer>(.*?)</answer>", re.DOTALL)

LOAD_TEMPLATE = """\
from crivo.checkup import load as _load_file
from crivo.profile import profile_df
{name} = _load_file({path!r})
_enc = {name}.attrs.get("encoding")
if _enc not in (None, "utf-8-sig"):
    print("warning: not utf-8, read as " + _enc + " — check accented text survived")
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
        skills_dir: str = "skills",
        preview: bool = True,
        snapshots: bool = True,
        resume: str | None = None,
        policies: list | None = None,
    ) -> None:
        # R3: gates show consequence computed on a sampled scratch copy;
        # drivers that auto-approve can turn it off, since nobody reads it
        self.preview = preview
        # T1.4 hunk D: standing approval policies (bench-only in M1; the
        # interactive arming UX is M2's coherent-unit work)
        self.policies = list(policies or [])
        # R8: verified fixes survive a kernel death via namespace snapshots
        self.snapshots = snapshots
        self.workspace_root = Path(workspace)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if resume:
            # R9: a resumed session is a new process reading an old log
            self.session_id = resume
            self.session_dir = self.workspace_root / resume
            if not self.session_dir.exists():
                raise FileNotFoundError(
                    f"no session {resume!r} under {self.workspace_root}"
                )
        else:
            self.session_id = self._next_session_id()
            self.session_dir = self.workspace_root / self.session_id
            self.session_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir)
        self.docker = docker
        self.skills_dir = Path(skills_dir)
        self.library = Library.load(self.skills_dir)

        if transport_argv is None and not docker:
            transport_argv = [sys.executable, "-m", "crivo.kernel.supervisor"]
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
        self.report_seq = 0
        if resume:
            self._resume_state()

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
            # R9: everything resume needs to rebuild the dataset entry from
            # the log alone — the transcript is the only durable record
            path=str(src),
            sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
            variable=name,
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

    def _compact_history(self):
        """R13: keep the session answerable forever. Dataset profile blocks
        are protected verbatim — the model's view of the data is not
        negotiable — the recent tail stays as-is, and everything older
        becomes one summary block written by the same scoped call the intent
        check uses. A failed summarisation keeps the history: better a slow
        session than a lobotomised one."""
        total = sum(len(str(m.get("content", ""))) for m in self.history)
        if total < COMPACT_AT_CHARS:
            return
        protected: list[dict] = []
        rest: list[dict] = []
        for message in self.history:
            content = str(message.get("content", "")).lstrip()
            (protected if content.startswith("<dataset ") else rest).append(message)
        if len(rest) <= COMPACT_KEEP_TURNS:
            return
        old, recent = rest[:-COMPACT_KEEP_TURNS], rest[-COMPACT_KEEP_TURNS:]
        digest = "\n\n".join(
            f"[{m.get('role', '?')}] {str(m.get('content', ''))[:1500]}" for m in old
        )
        try:
            summary = yield from self._generate_scoped(
                [
                    {
                        "role": "user",
                        "content": (
                            "Summarise the earlier conversation below into one "
                            "compact block for your own future reference: the "
                            "questions asked, the answers given with their key "
                            "numbers, and any decisions made. No preamble.\n\n" + digest
                        ),
                    }
                ],
                stream=False,
            )
        except Exception as exc:  # noqa: BLE001 — a failed summary must not kill the turn
            yield Notice("compaction", f"skipped ({type(exc).__name__}: {exc})")
            return
        block = {
            "role": "user",
            "content": f"<history-summary>\n{summary.strip()}\n</history-summary>",
        }
        self.history = protected + [block] + recent
        yield Notice(
            "compaction",
            f"history compacted: {len(old)} messages became one summary block",
        )

    def run_turn(self, question: str):
        q_ev = self.transcript.append("user", text=question)
        self.history.append({"role": "user", "content": question})
        yield from self._compact_history()
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
            frames = [e["name"] for e in self._registry if e.get("type") == "DataFrame"]
            pv = yield from self._preview(frames, body)
            decision = yield GateRequest(body, iters, preview=pv)
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
        intent = yield from self._intent_check(question, cells, answer_text or "")
        flags["intent_mismatch"] = intent.get("verdict") == "mismatch"
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
            intent=intent,
            created=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        card.save(self.session_dir / "cards")
        yield CardReady(card)

    def clean(self, var: str):
        """P1 CLEAN flow (spec R5): host drives the checklist, model fixes."""
        if var not in self._registry_prev:
            yield Notice("error", f"unknown variable {var!r} — /load it first")
            return False
        # One handler, at one level, doing the whole job. There were two — an
        # outer one that restarted and an inner one that saved the report — and
        # because the inner caught first, every death did exactly half of what
        # a death needs to do, and which half depended on where it happened.
        # Each was written against its own reproduction and each test proved
        # only its own path.
        state = {
            "var": var,
            "records": [],
            "fixable": [],
            "indicators": [],
            "clear": [],
            "evs": [],
            "outputs": {},
            "stats": {},
            "admitted": [],
        }
        try:
            yield from self._clean(state)
        except KernelLost as lost:
            yield Notice("kernel_died", str(lost))
            done = {id(r["finding"]) for r in state["records"]}
            inflight = state.get("inflight") or {}
            for f in state["fixable"]:
                if id(f) in done:
                    continue
                rec = self._aborted(f)
                if inflight.get("finding") is f:
                    rec["transcript_evs"] = list(
                        range(inflight["since"] + 1, self.transcript._last_id + 1)
                    )
                state["records"].append(rec)
            if state["records"] or state["fixable"]:
                # a verified fix that never reached disk is not a durable result
                state["stats"]["persisted"] = False
            yield from self._save_report(state)
            self._recover(lost)
            return False
        # the return value is for programmatic callers (family mode counts a
        # slice as cleaned only when its clean ran to completion); interactive
        # drivers ignore it
        return True

    def _clean(self, state: dict):
        """The flow. Every kernel touch is inside its caller's guard, and all
        shared results live in `state` so the handler can report whatever
        progress was made — including progress made before the diagnosis, which
        the previous boundary discarded."""
        var = state["var"]
        state["evs"].append(self.transcript.append("user", text=f"/clean {var}"))

        code = (
            "from crivo.detect import detect_all\n"
            "import json\n"
            f"print(json.dumps(detect_all({var}, {var!r})))"
        )
        result, stream, _, diag_ev = yield from self._exec_events(code, quiet=True)
        state["evs"].append(diag_ev)
        if result.status != "ok":
            err = (result.error or {}).get("evalue", result.status)
            yield Notice("error", f"diagnosis failed: {err}")
            return
        diagnosis = json.loads(stream.strip().splitlines()[-1])
        state["fixable"] = [f for f in diagnosis["findings"] if not f["indicator"]]
        state["indicators"] = [f for f in diagnosis["findings"] if f["indicator"]]
        state["clear"] = diagnosis["clear"]
        state["broken"] = diagnosis.get("broken", {})
        yield StreamText(
            "stdout",
            self._diagnosis_text(
                var,
                state["fixable"],
                state["indicators"],
                state["clear"],
                state["broken"],
            ),
        )

        baseline_cols = yield from self._snapshot_baseline(var)
        fixable = state["fixable"]
        plan_obj = None
        if _plan_first_enabled() and fixable:
            plan_obj, proceed = yield from self._build_and_approve_plan(fixable)
            state["plan"] = plan_obj.to_dict()
            if not proceed:
                yield from self._save_report(state)
                return
        for i, finding in enumerate(fixable, 1):
            # watermark before the attempts: if the kernel dies mid-fix, the
            # handler attributes every event after this point to THIS finding,
            # so the cell that mutated the frame stays reachable from /why
            state["inflight"] = {"finding": finding, "since": self.transcript._last_id}
            # the library first: a proven skill costs no model call at all
            rec = yield from self._skill_attempt(
                var, finding, i, len(fixable), baseline_cols
            )
            if rec is None and _m1_enabled():
                routed = router.route(finding)
                if routed["executor"] == "autoclean":
                    rec = yield from self._autoclean_attempt(
                        var, finding, i, len(fixable), baseline_cols
                    )
            if rec is None:
                rec = yield from self._fix_mini_turn(
                    var, finding, i, len(fixable), baseline_cols
                )
            state["records"].append(rec)
            state["evs"].extend(rec["transcript_evs"])
            if rec["status"] == "fixed" and self.snapshots:
                # R8: a verified fix must survive a kernel death — the replay
                # reloads raw files only. Best-effort and death-tolerant: a
                # failed snapshot never fails the fix it follows.
                yield from self._exec_events(
                    snapshot.snapshot_cell(str(self.session_dir / "kernel_state.pkl")),
                    quiet=True,
                    tolerate_death=True,
                )
            if rec["status"] == "fixed":
                baseline_cols = yield from self._snapshot_baseline(var)
        state["inflight"] = None

        if plan_obj is not None:
            # record what each planned step became, so the plan artifact tells
            # the truth about the run, not just the intent (M2-min)
            statuses = {}
            for j, rec in enumerate(state["records"]):
                if rec["status"] in plan_mod.STATUSES:
                    statuses[plan_mod.step_id(fixable[j], j)] = rec["status"]
            plan_obj = plan_mod.diff_plan(plan_obj, statuses)
            state["plan"] = plan_obj.to_dict()
            self.transcript.append(
                "plan", version=plan_obj.version, plan=plan_obj.to_dict()
            )

        yield from self._skill_pass(var, state["records"], state["admitted"])
        if any(r["status"] == "fixed" for r in state["records"]):
            outputs, stats, out_ev = yield from self._write_cleaned(
                var, state["records"]
            )
            state["outputs"], state["stats"] = outputs, stats
            state["evs"].append(out_ev)

        yield from self._save_report(state)

    def _resume_state(self) -> None:
        """R9: rebuild history, datasets, and loads from the transcript, then
        replay the loads into the fresh kernel and restore the R8 snapshot.
        Numbering continues (Transcript reopens its log; card and report
        sequences count what is already on disk), so a resumed session never
        overwrites what the original wrote. QUERY observations are not
        reconstructed — the model's own prior answers carry what mattered."""
        for ev in self.transcript.events():
            kind = ev.get("kind")
            if (
                kind == "exec"
                and ev.get("kind_note") == "load"
                and ev.get("status") == "ok"
            ):
                name = ev.get("variable")
                if not name:
                    continue  # a pre-R9 transcript: this load predates resume
                self.loads.append((name, ev["code"]))
                self.origins[name] = ev["ev_id"]
                if ev.get("path"):
                    self.datasets.append(
                        {
                            "path": ev["path"],
                            "sha256": ev.get("sha256", ""),
                            "variable": name,
                            "loaded_event": ev["ev_id"],
                        }
                    )
            elif kind == "user":
                self.history.append({"role": "user", "content": ev.get("text", "")})
            elif kind == "model":
                self.history.append(
                    {"role": "assistant", "content": ev.get("text", "")}
                )
        profiles: list[dict] = []
        for name, code in self.loads:
            stream_parts: list[str] = []
            result = None
            for item in self.client.execute(code, timeout_s=EXEC_TIMEOUT_S):
                if isinstance(item, StreamOut):
                    stream_parts.append(item.text)
                elif not isinstance(item, DisplayItem):
                    result = item
            if getattr(result, "status", None) == "ok" and getattr(
                result, "registry", None
            ):
                self._stamp_registry(result.registry, self.origins.get(name, 0))
                profile = "".join(stream_parts).strip()
                profiles.append(
                    {
                        "role": "user",
                        "content": f"<dataset variable={name!r}>\n{profile}\n</dataset>",
                    }
                )
        # the model's view of the data leads the conversation, as it did live
        self.history = profiles + self.history
        self._restore_snapshot()
        self.card_seq = len(list((self.session_dir / "cards").glob("c*.json")))
        self.report_seq = len(
            list((self.session_dir / "clean_reports").glob("r*.json"))
        )

    def _preview(self, names, cell_source: str):
        """R3: consequence before consent, on sampled scratch copies inside
        the kernel. Best effort at both layers — a preview that cannot run
        degrades the gate to code-only with a reason, never blocks it."""
        if not self.preview:
            return ""
        # the screen runs first: a preview executes model code BEFORE the
        # human approves it, and the scratch copy protects only the data —
        # anything reaching the process, files, or network waits for the gate
        reason = verify.preview_screen(cell_source)
        if reason:
            return f"(preview skipped: {reason})"
        result, stream, _, _ev = yield from self._exec_events(
            verify.preview_cell(names, cell_source), quiet=True, tolerate_death=True
        )
        if result.status != "ok":
            evalue = (result.error or {}).get("evalue", result.status)
            return f"(preview unavailable: {evalue})"
        return stream.strip()

    def _recover(self, lost: "KernelLost") -> None:
        """Restart after a death, so the session is not poisoned for good.

        The supervisor latches `hung` and only a restart clears it, so without
        this one timeout made every later /clean abort instantly and forever,
        with nothing telling the user why. QUERY mode has always recovered this
        way; CLEAN simply gave up. The dataframe's in-flight state is gone
        either way — what a restart buys back is the ability to reload and try
        again.
        """
        try:
            self._restart_and_replay(dead=True)
        except Exception as exc:  # noqa: BLE001 — recovery is best-effort
            self.transcript.append(
                "session_meta", event="restart_failed", error=str(exc)
            )

    def _aborted(self, finding: dict) -> dict:
        """A finding we never got to. Distinct from `failed`, which means the
        model tried and the verification refused it."""
        return {
            "finding": finding,
            "status": "aborted",
            "attempts": 0,
            "fix_source": None,
            "verify": {},
            "transcript_evs": [],
            "elapsed_s": 0.0,
            "origin": "none",
            "case": {},
        }

    def _save_report(self, state: dict):
        """Written on every exit, from whatever the run managed to establish."""
        var = state["var"]
        self.report_seq += 1
        report_id = f"{self.session_id}-r{self.report_seq:03d}"
        source = next(
            (d for d in self.datasets if d["variable"] == var),
            {"path": None, "sha256": None},
        )
        rep_ev = self.transcript.append(
            "card", report_id=report_id, variable=var, kind_note="clean_report"
        )
        report = CleanReport(
            report_id=report_id,
            session=self.session_id,
            variable=var,
            source={"path": source.get("path"), "sha256": source.get("sha256")},
            fixes=state["records"],
            indicators=state["indicators"],
            clear=state["clear"],
            broken=state.get("broken") or {},
            outputs=state["outputs"],
            stats=state["stats"],
            skills_admitted=state["admitted"],
            event_chain=[*state["evs"], rep_ev],
            created=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        report.save(self.session_dir / "clean_reports")
        yield StreamText("stdout", "\n" + report.to_markdown() + "\n")

    def _slice_var(self, name: str, slice_key: str) -> str:
        """The variable a slice is cleaned under. Both the loader and the
        cleaner must derive this identically — when they disagreed, every
        family slice recorded a null source and no family skill could ever be
        promoted, because promotion counts distinct sources.

        The sanitised form alone is not injective (tax-2007 and tax_2007 both
        become tax_2007), and a collision silently overwrites the first
        slice's frame and credits its file to the second's lineage. A short
        digest of the raw key keeps distinct slices distinct; it is applied
        unconditionally because "only when lossy" is itself a collision
        surface."""
        base = re.sub(r"\W+", "_", f"{name}_{slice_key}").strip("_")
        digest = hashlib.sha256(slice_key.encode("utf-8")).hexdigest()[:6]
        return f"{base}_{digest}"

    def load_family(self, pattern: str, name: str):
        """Load a glob of same-family files into one dict variable (P2.5 R1)."""
        result, stream, _, ev_id = yield from self._exec_events(
            verify.family_load_cell(name, pattern), quiet=True
        )
        if result.status != "ok":
            err = (result.error or {}).get("evalue", result.status)
            yield Notice("error", f"family load failed: {err}")
            return []
        try:
            meta = json.loads(stream.strip().splitlines()[-1])
        except (ValueError, IndexError):
            yield Notice("error", "family load produced no manifest")
            return []
        if not meta:
            yield Notice("error", f"no files matched {pattern!r}")
            return []
        self._stamp_registry(result.registry, ev_id)
        for entry in meta:
            src = Path(entry["path"])
            if src.exists():
                self.datasets.append(
                    {
                        "path": str(src),
                        "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                        "variable": self._slice_var(name, entry["slice"]),
                        "loaded_event": ev_id,
                    }
                )
        yield StreamText("stdout", self._drift_text(name, meta))
        return meta

    def clean_family(self, pattern: str, name: str):
        """Harmonize a family, then clean each slice. Guarded like clean():
        every step here touches the kernel, and an unguarded KernelLost unwinds
        through the driver, skipping session.close() and orphaning the
        container."""
        try:
            yield from self._clean_family(pattern, name)
        except KernelLost as lost:
            yield Notice("kernel_died", str(lost))
            self._recover(lost)

    def _clean_family(self, pattern: str, name: str):
        """P2.5: harmonize a family once, then clean each slice with the library.

        The second half is deliberately the ordinary CLEAN flow. Harmonizing is
        what makes the slices comparable; the compounding is what happens next,
        when one skill fixes the same disease in twenty-one files.
        """
        meta = yield from self.load_family(pattern, name)
        if not meta:
            return
        # One mutable record, so the summary below sees whatever progress was
        # made before an exception. It is written on every exit: this used to
        # be the method's last statement with no guard, so a death in the
        # diagnosis or the harmonize turn lost skill_hits entirely — the one
        # number this phase exists to produce.
        run = {"harmonized": False, "drift": [], "hits": {}, "cleaned": []}
        try:
            yield from self._family_body(name, meta, run)
        finally:
            # a pure write, deliberately: yielding here while GeneratorExit
            # propagates (Ctrl-C at a gate, a UI rerun abandoning the
            # generator) is a RuntimeError, which failed the save on exactly
            # the exit this finally was added to survive
            path, summary = self._write_family(name, pattern, meta, run)
        replayed = sum(run["hits"].values())
        yield StreamText(
            "stdout",
            f"\nfamily {name}: {len(run['cleaned'])}/{len(meta)} slices cleaned, "
            f"{summary['rows']:,} rows · {replayed} fix(es) served by "
            f"{len(run['hits'])} skill(s) · {path}\n",
        )

    def _family_body(self, name: str, meta: list, run: dict):
        code = (
            "from crivo.detect import detect_family\n"
            "import json\n"
            f"print(json.dumps(detect_family({name})))"
        )
        result, stream, _, _ev = yield from self._exec_events(code, quiet=True)
        if result.status == "ok":
            try:
                run["drift"] = json.loads(stream.strip().splitlines()[-1])
            except (ValueError, IndexError):
                pass

        if run["drift"]:
            # a mapping this family already confirmed replays for free (R6)
            run["harmonized"] = yield from self._replay_mapping(name)
            if not run["harmonized"]:
                run["harmonized"] = yield from self._harmonize(name, meta, run["drift"])
            if not run["harmonized"]:
                yield Notice(
                    "family", "harmonizing failed — cleaning slices as they are"
                )
        else:
            run["harmonized"] = True  # nothing to reconcile is a kind of harmonized
            yield Notice("family", "slices already share one schema")

        for entry in meta:
            var = self._slice_var(name, entry["slice"])
            res, _, _, ev_id = yield from self._exec_events(
                f"{var} = {name}[{entry['slice']!r}]\n{var}.shape", quiet=True
            )
            if res.status != "ok":
                # a slice that never bound was never cleaned; the summary said
                # otherwise, because it was built from `meta` regardless
                err = (res.error or {}).get("evalue", res.status)
                yield Notice("family", f"slice {entry['slice']} skipped: {err}")
                continue
            self._stamp_registry(res.registry, ev_id)
            yield StreamText("stdout", f"\n── slice {entry['slice']} ──\n")
            before = dict(self._skill_uses())
            completed = yield from self.clean(var)
            if completed:
                run["cleaned"].append(entry["slice"])
            for skill_name, count in self._skill_uses().items():
                gained = count - before.get(skill_name, 0)
                if gained:
                    run["hits"][skill_name] = run["hits"].get(skill_name, 0) + gained

    def _write_family(self, name: str, pattern: str, meta: list, run: dict):
        """Persist the family summary. Never yields: it runs inside a finally,
        where a yield during GeneratorExit is a RuntimeError."""
        cleaned, hits = run["cleaned"], run["hits"]
        summary = {
            "family": name,
            "pattern": pattern,
            "slices": cleaned,  # what was actually cleaned, not what was found
            "found": [m["slice"] for m in meta],
            "rows": sum(m["rows"] for m in meta if m["slice"] in cleaned),
            "harmonized": run["harmonized"],
            "drift_findings": len(run["drift"]),
            # the number the whole phase exists to produce: one skill, many files
            "skill_hits": hits,
        }
        path = self.session_dir / f"family_{name}.json"
        path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return path, summary

    def _skill_uses(self) -> dict:
        return {n: e["uses"] for n, e in self.library.entries.items()}

    def _replay_mapping(self, name: str):
        """Apply a mapping this family already confirmed, if one exists (R6).

        Retrieval for disease 20 cannot go through the single-frame path —
        detect_all never runs a family signal — so the family flow asks for it
        directly. Verification is the same family cell either way: a replayed
        mapping earns no more trust than a fresh one.
        """
        for entry in self.library.candidates(20):
            try:
                skill = skills.load(self.skills_dir / entry["name"])
            except (OSError, ValueError):
                continue
            yield from self._exec_events(verify.family_baseline_cell(name), quiet=True)
            code = (
                f"{skill.fix_source}\n"
                f"{name} = {{k: fix(v, []) for k, v in {name}.items()}}\n"
                f'"applied"'
            )
            res, _, _, ev_id = yield from self._exec_events(code, quiet=True)
            if res.status == "ok":
                vres, _, _, _v = yield from self._exec_events(
                    verify.family_verify_cell(name), quiet=True
                )
                if vres.status == "ok":
                    self.library.record(
                        entry["name"], success=True, dataset=name, events=[ev_id]
                    )
                    self.library.save()
                    yield Notice(
                        "family",
                        f"{entry['name']} harmonized the family from a confirmed "
                        "mapping — no model call",
                    )
                    return True
            yield from self._exec_events(verify.family_revert_cell(name), quiet=True)
            self.library.record(
                entry["name"], success=False, dataset=name, events=[ev_id]
            )
            self.library.save()
            yield Notice(
                "family", f"{entry['name']} no longer fits this family — reverted"
            )
        return False

    def _harmonize(self, name: str, meta: list, drift: list):
        """One gated mapping for the whole family (P2.5 R4/R5)."""
        yield from self._exec_events(verify.family_baseline_cell(name), quiet=True)
        msgs = [
            {"role": "system", "content": prompts.HARMONIZE_PROMPT},
            {"role": "user", "content": self._drift_context(name, meta, drift)},
        ]
        for attempt in range(1, CLEAN_MAX_ATTEMPTS + 1):
            try:
                resp = yield from self._generate_scoped(msgs)
            except Exception as exc:  # noqa: BLE001 — surface and fall through
                yield Notice("llm_error", f"{type(exc).__name__}: {exc}")
                return False
            self.transcript.append("model", text=resp, kind_note="harmonize")
            msgs.append({"role": "assistant", "content": resp})
            kind, body = parse_tags(resp)
            if kind != "execute" or "def harmonize" not in body:
                msgs.append({"role": "user", "content": prompts.CLEAN_NUDGE_PROMPT})
                continue

            decision = yield GateRequest(
                body,
                attempt,
                title=(
                    f"harmonize {len(meta)} slices of {name} · GATE · "
                    f"{len(drift)} drift finding(s)"
                ),
            )
            if not isinstance(decision, GateDecision):
                decision = GateDecision("run")
            self.transcript.append("gate", action=decision.action, note=decision.note)
            if decision.action == "skip":
                return False
            if decision.action == "reject":
                msgs.append(
                    {
                        "role": "user",
                        "content": f"<observation>rejected: {decision.note}</observation>",
                    }
                )
                continue

            res, stream, paths, ev_id = yield from self._exec_events(body)
            if res.status != "ok":
                yield from self._exec_events(
                    verify.family_revert_cell(name), quiet=True
                )
                msgs.append(
                    {
                        "role": "user",
                        "content": self._observation(ev_id, res, stream, paths, []),
                    }
                )
                continue
            vres, _, _, _v = yield from self._exec_events(
                verify.family_verify_cell(name), quiet=True
            )
            if vres.status == "ok":
                yield Notice("family", f"harmonized {len(meta)} slices to one schema")
                return True
            yield from self._exec_events(verify.family_revert_cell(name), quiet=True)
            verr = (vres.error or {}).get("evalue", vres.status)
            yield Notice("family", f"harmonize rejected: {verr}")
            msgs.append(
                {
                    "role": "user",
                    "content": (
                        f"<observation>verification failed: {verr} — the family "
                        "was reverted; revise the mapping</observation>"
                    ),
                }
            )
        return False

    def _drift_text(self, name: str, meta: list) -> str:
        """Per-slice shape plus union/intersection — never a per-file dump (R2)."""
        sets = [set(m["columns"]) for m in meta]
        shared = set.intersection(*sets) if sets else set()
        union = set.union(*sets) if sets else set()
        rows = sum(m["rows"] for m in meta)
        header = (
            f"\nfamily {name}: {len(meta)} slices, {rows:,} rows, "
            f"{len(shared)} shared columns, {len(union) - len(shared)} that drift\n"
        )
        lines = [header]
        for m in meta:
            missing = sorted(union - set(m["columns"]))
            note = f" · missing {', '.join(missing[:4])}" if missing else ""
            lines.append(
                f"  {m['slice']}: {m['rows']:,} rows × {len(m['columns'])} cols "
                f"(sep {m['sep']!r}){note}\n"
            )
        return "".join(lines)

    def _drift_context(self, name: str, meta: list, drift: list) -> str:
        sets = [set(m["columns"]) for m in meta]
        shared = sorted(set.intersection(*sets)) if sets else []
        union = set.union(*sets) if sets else set()
        per_slice = {m["slice"]: sorted(union - set(m["columns"])) for m in meta}
        return (
            f"Variable: {name} (a dict of {len(meta)} DataFrames)\n\n"
            f"Columns every slice has ({len(shared)}): {json.dumps(shared)}\n\n"
            f"Columns each slice is missing: {json.dumps(per_slice, indent=2)}\n\n"
            f"Drift findings:\n{json.dumps(drift, indent=2)[:4000]}"
        )

    def close(self) -> None:
        try:
            self.transcript.append("session_meta", event="close")
        finally:
            self.client.close()

    # -- CLEAN internals -----------------------------------------------------

    def _snapshot_baseline(self, var: str):
        result, _, _, _ev = yield from self._exec_events(
            verify.baseline_cell(var), quiet=True
        )
        return json.loads(ast.literal_eval(result.value))

    def _fix_mini_turn(
        self, var: str, finding: dict, i: int, n: int, baseline_cols: list[str]
    ):
        t0 = time.monotonic()
        title = (
            f"fix {i}/{n} · {finding['slug']} · {finding['grade']} · "
            f"conf {finding['confidence']:.2f} · {finding['evidence'][:70]}"
        )
        msgs = [
            {"role": "system", "content": prompts.CLEAN_PROMPT},
            {"role": "user", "content": self._finding_context(var, finding)},
        ]
        evs: list[int] = []
        attempts, nudged = 0, False
        status, fix_source, verify_info = "failed", None, {}
        case: dict = {}

        # T1.4 hunk C: one pre-fix content fingerprint per mini turn. Failed
        # attempts revert the frame, so it stays valid across the loop; an
        # empty stream (a harness kernel without crivo) disables the hook.
        fp_cell = (
            "from crivo.fingerprint import frame_fingerprint\n"
            f"print(frame_fingerprint({var}))"
        )
        pre_fp = ""
        if _m1_enabled():
            _, fp_stream, _, _ = yield from self._exec_events(fp_cell, quiet=True)
            if fp_stream.strip():
                pre_fp = fp_stream.strip().splitlines()[-1]

        while attempts < CLEAN_MAX_ATTEMPTS:
            try:
                resp = yield from self._generate_scoped(msgs)
            except Exception as exc:  # noqa: BLE001 — surface, fail the finding
                yield Notice("llm_error", f"{type(exc).__name__}: {exc}")
                break
            evs.append(self.transcript.append("model", text=resp))
            msgs.append({"role": "assistant", "content": resp})
            kind, body = parse_tags(resp)

            if kind != "execute" or "def fix_" not in body:
                if not nudged:
                    nudged = True  # first malformed reply is free, like QUERY
                else:
                    attempts += 1
                msgs.append({"role": "user", "content": prompts.CLEAN_NUDGE_PROMPT})
                continue

            attempts += 1
            pv = yield from self._preview(var, body)
            decision = yield GateRequest(
                body, attempts, title=title, preview=pv, grade=finding["grade"]
            )
            if not isinstance(decision, GateDecision):
                decision = GateDecision("run")
            evs.append(
                self.transcript.append(
                    "gate", action=decision.action, note=decision.note
                )
            )
            if decision.action == "skip":
                status = "skipped"
                break
            if decision.action == "reject":
                msgs.append(
                    {
                        "role": "user",
                        "content": (
                            "<observation>user rejected the fix: "
                            f"{decision.note}</observation>"
                        ),
                    }
                )
                continue

            result, stream, paths, ev_id = yield from self._exec_events(body)
            evs.append(ev_id)
            self._stamp_registry(result.registry, ev_id)
            if result.status != "ok":
                _, _, _, rev_ev = yield from self._exec_events(
                    verify.revert_cell(var), quiet=True
                )
                evs.append(rev_ev)
                msgs.append(
                    {
                        "role": "user",
                        "content": self._observation(ev_id, result, stream, paths, []),
                    }
                )
                continue

            fix_source = body
            if pre_fp:
                _, after_stream, _, _ = yield from self._exec_events(
                    fp_cell, quiet=True
                )
                after_fp = (
                    after_stream.strip().splitlines()[-1]
                    if after_stream.strip()
                    else ""
                )
                if after_fp == pre_fp:
                    # the cell ran and changed nothing: the check's verdict
                    # cannot have moved either, so the verify pass is skipped
                    # and the attempt still counts (research gap 1)
                    verify_info = {"layer1": "skip", "error": "no state change"}
                    msgs.append(
                        {
                            "role": "user",
                            "content": (
                                f"attempt {attempts}/{CLEAN_MAX_ATTEMPTS} not "
                                f"verified: your cell changed nothing in {var} "
                                "(identical content fingerprint). Edit the "
                                "data before finishing, or explain why no "
                                "change is needed."
                            ),
                        }
                    )
                    continue
            vres, _, _, v_ev = yield from self._exec_events(
                verify.verify_cell(var, finding, baseline_cols), quiet=True
            )
            evs.append(v_ev)
            if vres.status == "ok":
                status = "fixed"
                verify_info = {"layer1": "pass"}
                # freeze the case now: the next verified fix moves the baseline
                # forward and the pre-fix frame is gone (P2 R7)
                case, case_ev = yield from self._freeze_case(var, finding)
                if case_ev:
                    evs.append(case_ev)
                break
            _, _, _, rev_ev = yield from self._exec_events(
                verify.revert_cell(var), quiet=True
            )
            evs.append(rev_ev)
            verr = (vres.error or {}).get("evalue", vres.status)
            verify_info = {"layer1": "fail", "error": verr}
            msgs.append(
                {
                    "role": "user",
                    "content": (
                        f"<observation>verification failed: {verr} — the "
                        "dataframe was reverted; revise the fix</observation>"
                    ),
                }
            )

        yield Notice("fix", f"{finding['slug']}: {status} ({attempts} attempts)")
        return {
            "finding": finding,
            "status": status,
            "attempts": attempts,
            "fix_source": fix_source,
            "verify": verify_info,
            "transcript_evs": evs,
            "elapsed_s": round(time.monotonic() - t0, 1),
            "origin": "model",
            "case": case,
        }

    # -- P3: the intent gate --------------------------------------------------

    def _intent_check(self, question: str, cells: list, answer: str):
        """Restate what the code actually computed and diff it against the ask.

        One extra call, deliberately narrow: it never re-solves the problem, it
        only reads the code back. Catches correct code answering the wrong
        question — the failure assertions structurally cannot see, because the
        assertions are about the code that ran, not the question that was asked.
        """
        ran = [c for c in cells if c.get("status") == "ok" and c.get("code")]
        if not ran:
            return {}
        code = "\n\n".join(c["code"] for c in ran[-2:])
        msgs = [
            {"role": "system", "content": prompts.INTENT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Question:\n{question}\n\nCode that executed:\n{code}\n\n"
                    f"Answer written:\n{answer}"
                ),
            },
        ]
        try:
            resp = yield from self._generate_scoped(msgs, stream=False)
        except Exception as exc:  # noqa: BLE001 — an unavailable check is not a verdict
            yield Notice("intent", f"intent check unavailable: {type(exc).__name__}")
            return {}
        self.transcript.append("model", text=resp, kind_note="intent")
        found = {}
        for tag in ("restatement", "verdict", "reason"):
            match = re.search(rf"<{tag}>(.*?)</{tag}>", resp, re.DOTALL)
            found[tag] = match.group(1).strip() if match else ""
        found["verdict"] = "mismatch" if found["verdict"] == "mismatch" else "match"
        if found["verdict"] == "mismatch":
            yield Notice(
                "intent",
                f"the code computed something else: {found['restatement']} "
                f"({found['reason']})",
            )
        return found

    # -- P2: the library ------------------------------------------------------

    def _source_sha(self, var: str) -> str:
        """Which file this variable came from. Promotion counts distinct
        sources, so this is what makes a track record mean anything (R14)."""
        for ds in self.datasets:
            if ds["variable"] == var:
                return ds.get("sha256", "")
        return ""

    def _freeze_case(self, var: str, finding: dict):
        """Save the pre-fix rows this fix was born from, for admission (R7)."""
        slug = finding.get("slug", f"d{finding['disease']}")
        rel = f"skill_cases/{slug}-{finding['disease']}.parquet"
        (self.session_dir / "skill_cases").mkdir(parents=True, exist_ok=True)
        kernel_path = (
            f"/workspace/{rel}" if self.docker else str(self.session_dir / rel)
        )
        result, _, _, ev_id = yield from self._exec_events(
            verify.case_cell(var, finding, kernel_path), quiet=True
        )
        if result.status != "ok":
            return {}, ev_id
        try:
            info = json.loads(ast.literal_eval(result.value))
        except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
            return {}, ev_id
        if not isinstance(info, dict):
            return {}, ev_id
        info["path"] = kernel_path
        return info, ev_id

    def _build_and_approve_plan(self, fixable: list[dict]):
        """Build a plan from the findings and gate it as one coherent unit
        (M2-min, T2.4). Approving arms an in-session ENFORCE policy over the
        plan's AUTO+autoclean disease ids, so those steps then run silently
        through M1's batched path; GATE and HUMAN steps still gate per finding.
        The plan is emitted to stdout and recorded in the transcript, so /why
        can cite it. Returns (Plan, proceed: bool)."""
        built = plan_mod.build_plan(fixable)
        yield StreamText("stdout", "\n" + _plan_table(built) + "\n")
        self.transcript.append("plan", version=built.version, plan=built.to_dict())
        auto_ids = sorted(
            {
                s.disease
                for s in built.steps
                if s.executor == "autoclean" and s.grade == "AUTO"
            }
        )
        if not auto_ids:
            return built, True  # nothing batchable; per-finding gates as today

        decision = yield GateRequest(
            _plan_table(built), 1, title=f"approve {built.summary()}", grade="PLAN"
        )
        if not isinstance(decision, GateDecision):
            decision = GateDecision("run")
        self.transcript.append("gate", action=decision.action, note="plan")
        if decision.action == "run":
            from crivo.detect import SLUGS

            expires = (datetime.now().astimezone().date()).isoformat()
            self.policies = [
                *self.policies,
                policy.PolicyRecord(
                    id=f"plan-v{built.version}",
                    disease_ids=tuple(auto_ids),
                    approver="plan-approval",
                    expires=expires,
                    mode="ENFORCE",
                    valid_disease_ids=set(SLUGS),
                ),
            ]
        return built, decision.action != "skip"

    def _autoclean_attempt(
        self, var: str, finding: dict, i: int, n: int, baseline_cols: list[str]
    ):
        """The taxonomy's own fixer before paying a model call (M1, T1.4).

        Only reachable for AUTO findings whose disease has a registered
        deterministic fixer (router.route pins that). A standing policy may
        batch the gate; otherwise the gate yields exactly like a skill's.
        Verification and revert are the same cells every fix passes. Returns
        None when the fix fails or is rejected, handing the finding to the
        model exactly as a failed skill does."""
        disease = finding["disease"]
        fix_source = f"from crivo.autoclean import FIXERS\nfix = FIXERS[{disease}]"
        code = verify.skill_apply_cell(var, fix_source, finding["columns"])
        verdict = policy.evaluate(finding, self.policies)
        silent = verdict["batched"]
        decision_note = ""
        t0 = time.monotonic()
        evs: list[int] = []
        title = (
            f"autoclean {i}/{n} · d{disease:02d} {finding['slug']} · "
            f"{finding['grade']} · {finding['evidence'][:60]}"
        )

        if silent:
            decision_note = f"policy:{verdict['policy_id']}"
            evs.append(
                self.transcript.append("gate", action="run", note=decision_note)
            )
        else:
            pv = yield from self._preview(var, code)
            decision = yield GateRequest(
                code, 1, title=title, preview=pv, grade=finding["grade"]
            )
            if not isinstance(decision, GateDecision):
                decision = GateDecision("run")
            evs.append(
                self.transcript.append(
                    "gate", action=decision.action, note=decision.note
                )
            )
            if decision.action == "skip":
                return {
                    "finding": finding,
                    "status": "skipped",
                    "attempts": 0,
                    "fix_source": fix_source,
                    "verify": {},
                    "transcript_evs": evs,
                    "elapsed_s": round(time.monotonic() - t0, 1),
                    "origin": f"autoclean:d{disease:02d}",
                    "case": {},
                }
            if decision.action == "reject":
                return None  # to the model, matching skill-reject behavior

        result, _, _, ev_id = yield from self._exec_events(code, quiet=True)
        evs.append(ev_id)
        if result.status == "ok":
            self._stamp_registry(result.registry, ev_id)
            vres, _, _, v_ev = yield from self._exec_events(
                verify.verify_cell(var, finding, baseline_cols), quiet=True
            )
            evs.append(v_ev)
            if vres.status == "ok":
                yield Notice(
                    "autoclean",
                    f"d{disease:02d} {finding['slug']} fixed with no model call"
                    + (f" ({decision_note})" if silent else ""),
                )
                return {
                    "finding": finding,
                    "status": "fixed",
                    "attempts": 0,
                    "fix_source": fix_source,
                    "verify": {"layer1": "pass", "by": f"autoclean:d{disease:02d}"},
                    "transcript_evs": evs,
                    "elapsed_s": round(time.monotonic() - t0, 1),
                    "origin": f"autoclean:d{disease:02d}",
                    "case": {},
                }

        _, _, _, rev_ev = yield from self._exec_events(
            verify.revert_cell(var), quiet=True
        )
        evs.append(rev_ev)
        yield Notice(
            "autoclean",
            f"d{disease:02d} deterministic fix did not verify — "
            "handing to the model",
        )
        return None

    def _skill_attempt(
        self, var: str, finding: dict, i: int, n: int, baseline_cols: list[str]
    ):
        """Try the library before paying a model call (R11/R12).

        Walks ranked candidates best-first. A candidate that fails is reverted
        and scored before the next one is tried, so it never taints the next
        attempt and never escapes without taking its hit (R13). Returns a fix
        record when some candidate worked, or None to hand the finding to the
        model — including when every candidate was tried and failed.
        """
        candidates = self.library.candidates(finding["disease"])[:SKILL_ATTEMPT_CAP]
        sha = self._source_sha(var)
        t0 = time.monotonic()
        evs: list[int] = []

        for entry in candidates:
            try:
                skill = skills.load(self.skills_dir / entry["name"])
            except (OSError, ValueError) as exc:
                yield Notice(
                    "skill", f"{entry['name']} is unreadable ({exc}) — skipping"
                )
                continue

            code = verify.skill_apply_cell(var, skill.fix_source, finding["columns"])
            silent = unattended(entry, finding["grade"])
            title = (
                f"skill {i}/{n} · {entry['name']} · {entry['state']} · "
                f"{finding['grade']} · {finding['evidence'][:60]}"
            )

            if not silent:
                pv = yield from self._preview(var, code)
                decision = yield GateRequest(
                    code, 1, title=title, preview=pv, grade=finding["grade"]
                )
                if not isinstance(decision, GateDecision):
                    decision = GateDecision("run")
                evs.append(
                    self.transcript.append(
                        "gate", action=decision.action, note=decision.note
                    )
                )
                if decision.action == "skip":
                    return {
                        "finding": finding,
                        "status": "skipped",
                        "attempts": 0,
                        "fix_source": skill.fix_source,
                        "verify": {},
                        "transcript_evs": evs,
                        "elapsed_s": round(time.monotonic() - t0, 1),
                        "origin": f"skill:{entry['name']}",
                        "case": {},
                    }
                if decision.action == "reject":
                    self.library.record(
                        entry["name"], success=False, dataset=sha, events=evs
                    )
                    self.library.save()
                    continue

            result, _, _, ev_id = yield from self._exec_events(code, quiet=True)
            evs.append(ev_id)
            uncheckable = ""
            if result.status == "ok":
                self._stamp_registry(result.registry, ev_id)
                vres, _, _, v_ev = yield from self._exec_events(
                    verify.verify_cell(var, finding, baseline_cols), quiet=True
                )
                evs.append(v_ev)
                evalue = (vres.error or {}).get("evalue", "") if vres.error else ""
                if evalue.startswith("uncheckable:"):
                    uncheckable = evalue
                if vres.status == "ok":
                    self.library.record(
                        entry["name"], success=True, dataset=sha, events=evs
                    )
                    self.library.save()
                    yield Notice(
                        "skill",
                        f"{entry['name']} fixed {finding['slug']} with no model call"
                        + (" (unattended)" if silent else ""),
                    )
                    return {
                        "finding": finding,
                        "status": "fixed",
                        "attempts": 0,
                        "fix_source": skill.fix_source,
                        "verify": {"layer1": "pass", "by": entry["name"]},
                        "transcript_evs": evs,
                        "elapsed_s": round(time.monotonic() - t0, 1),
                        "origin": f"skill:{entry['name']}",
                        "case": {},
                    }

            _, _, _, rev_ev = yield from self._exec_events(
                verify.revert_cell(var), quiet=True
            )
            evs.append(rev_ev)
            if uncheckable:
                # the check itself crashed: that is evidence about the
                # detector, not the skill — scoring it retires working skills
                # on someone else's bug. The revert above still ran, because
                # a fix that could not be checked is not verified.
                yield Notice(
                    "skill",
                    f"{entry['name']} not scored — the check crashed "
                    f"({uncheckable.removeprefix('uncheckable: ')}); reverted",
                )
                continue
            self.library.record(entry["name"], success=False, dataset=sha, events=evs)
            self.library.save()
            state = self.library.entries[entry["name"]]["state"]
            yield Notice(
                "skill",
                f"{entry['name']} failed verification — reverted"
                + (" (and retired)" if state == "retired" else ""),
            )

        return None

    def _skill_pass(self, var: str, records: list[dict], admitted: list[str]):
        """After the run, offer each model-authored verified fix as a skill.

        Only `origin == "model"` records qualify: a fix a skill produced never
        spawns another skill. That is the depth-1 recursion cap, and keeping it
        here in the flow means no prompt can talk its way past it (R5).
        """
        candidates = [
            r
            for r in records
            if r["status"] == "fixed"
            and r.get("origin") == "model"
            and r.get("case", {}).get("path")
        ]
        for rec in candidates:
            name = yield from self._propose_skill(var, rec)
            if name:
                # in place and saved immediately, not returned and saved at
                # the end: a kernel death during the NEXT candidate's cells
                # must not orphan a skill a human just approved — folder on
                # disk, invisible to candidates() in every later session
                admitted.append(name)
                self.library.save()

    def _propose_skill(self, var: str, rec: dict):
        """One candidate through generalise -> execute -> execute -> human."""
        finding = rec["finding"]
        source = next((d["path"] for d in self.datasets if d["variable"] == var), var)
        msgs = [
            {"role": "system", "content": prompts.SKILL_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Finding:\n{json.dumps(finding, indent=2)}\n\n"
                    f"The fix that ran and passed verification:\n{rec['fix_source']}"
                ),
            },
        ]
        for _ in range(SKILL_MAX_ATTEMPTS):
            try:
                resp = yield from self._generate_scoped(msgs)
            except Exception as exc:  # noqa: BLE001 — a skill is optional; the fix stands
                yield Notice("llm_error", f"{type(exc).__name__}: {exc}")
                return None
            self.transcript.append("model", text=resp, kind_note="skill_proposal")
            msgs.append({"role": "assistant", "content": resp})

            proposal = skills.parse_proposal(resp)
            if proposal is None:
                msgs.append(
                    {
                        "role": "user",
                        "content": (
                            "<observation>reply must be exactly the four tagged "
                            "blocks: name, description, fix, test</observation>"
                        ),
                    }
                )
                continue

            skill = skills.from_proposal(proposal, finding, source=str(source))
            problems = skills.validate(skill, skill.name)
            if problems:
                yield Notice("skill", f"{skill.name}: {problems[0]}")
                msgs.append(
                    {
                        "role": "user",
                        "content": "<observation>"
                        + "; ".join(problems)
                        + "</observation>",
                    }
                )
                continue

            failure = yield from self._admit(skill, finding, rec["case"])
            if failure:
                yield Notice("skill", f"{skill.name} refused: {failure}")
                msgs.append(
                    {"role": "user", "content": f"<observation>{failure}</observation>"}
                )
                continue

            source_view = f"# {skill.name}\n# {skill.description}\n\n{skill.fix_source}"
            # R4: the approving human sees the skill's effect on the frozen
            # case, not only its source — same renderer as every other gate
            effect = ""
            if self.preview and rec["case"].get("path"):
                bind = (
                    "import pandas as pd\n"
                    f"_case_preview = pd.read_parquet({rec['case']['path']!r})\n"
                    "'bound'"
                )
                bres, _, _, _bev = yield from self._exec_events(
                    bind, quiet=True, tolerate_death=True
                )
                if bres.status == "ok":
                    apply_src = (
                        f"{skill.fix_source}\n"
                        f"_case_preview = fix(_case_preview, "
                        f"{finding.get('columns', [])!r})\n"
                    )
                    effect = yield from self._preview(["_case_preview"], apply_src)
            decision = yield GateRequest(
                source_view,
                1,
                title=(
                    f"admit skill {skill.name} · d{finding['disease']} · "
                    f"reproduces the case it came from"
                ),
                preview=effect,
                # admission is governance, and governance is never unattended
                grade="HUMAN",
            )
            if not isinstance(decision, GateDecision):
                decision = GateDecision("run")
            self.transcript.append(
                "gate",
                action=decision.action,
                note=decision.note,
                kind_note="admission",
            )
            if decision.action == "run":
                skills.save(skill, self.skills_dir)
                self.library.register(skill.name, finding["disease"])
                yield Notice("skill", f"admitted {skill.name} (on probation)")
                return skill.name
            if decision.action == "skip":
                return None
            msgs.append(
                {
                    "role": "user",
                    "content": f"<observation>rejected: {decision.note}</observation>",
                }
            )
        return None

    def _admit(self, skill, finding: dict, case: dict) -> str:
        """Both execution gates. Returns "" when admitted, else why not (R7/R8).

        Runs in the kernel, not the host: this is model-authored code meeting
        real data, and the sandbox is where that belongs.
        """
        adm, _, _, _ev = yield from self._exec_events(
            verify.admission_cell(
                case["path"], skill.fix_source, finding, case.get("healthy", [])
            ),
            quiet=True,
        )
        if adm.status != "ok":
            return (adm.error or {}).get("evalue", adm.status)

        test, _, _, _tev = yield from self._exec_events(
            verify.skill_test_cell(skill.fix_source, skill.test_source), quiet=True
        )
        if test.status != "ok":
            return "its own test fails: " + (test.error or {}).get("evalue", "")
        return ""

    def _diagnosis_text(
        self, var: str, fixable: list, indicators: list, clear: list, broken=None
    ) -> str:
        header = (
            f"\ndiagnosis of {var}: {len(fixable)} fixable finding(s), "
            f"{len(indicators)} indicator(s), {len(clear)} signal(s) clear\n"
        )
        lines = [header]
        for i, f in enumerate(fixable, 1):
            cols = ", ".join(f["columns"]) or "table"
            lines.append(
                f"  {i}. d{f['disease']:02d} {f['slug']} [{cols}] "
                f"{f['grade']} conf {f['confidence']:.2f} — {f['evidence']}\n"
            )
        for f in indicators:
            lines.append(
                f"  ⚠ d{f['disease']:02d} {f['slug']} (indicator, not fixed) "
                f"— {f['evidence']}\n"
            )
        # a signal that crashed is not a signal that found nothing, and saying
        # so is the whole point of `clear` being a claim rather than a silence
        for disease, why in sorted((broken or {}).items()):
            lines.append(f"  ✗ d{int(disease):02d} did not run — {why}\n")
        return "".join(lines)

    def _finding_context(self, var: str, finding: dict) -> str:
        profile = ""
        for ds in self.datasets:
            if ds["variable"] == var:
                for msg in self.history:
                    content = str(msg.get("content", ""))
                    if f"<dataset variable={var!r}>" in content:
                        profile = content
                        break
        return (
            f"Variable: {var}\n\nFinding to fix:\n{json.dumps(finding, indent=2)}"
            + (f"\n\n{profile}" if profile else "")
        )

    def _write_cleaned(self, var: str, records: list[dict]):
        cleaned_dir = self.session_dir / "cleaned"
        cleaned_dir.mkdir(parents=True, exist_ok=True)
        if self.docker:
            kernel_path = f"/workspace/cleaned/{var}.parquet"
        else:
            kernel_path = str(cleaned_dir / f"{var}.parquet")
        code = (
            "from pathlib import Path\n"
            f"Path({json.dumps(kernel_path)}).parent.mkdir("
            "parents=True, exist_ok=True)\n"
            f"{var}.to_parquet({json.dumps(kernel_path)})\n"
            f'f"saved {{len({var})}} rows x {{len({var}.columns)}} cols"'
        )
        result, _, _, ev_id = yield from self._exec_events(code, quiet=True)
        entry = next((e for e in self._registry if e["name"] == var), {})
        stats = {"shape": entry.get("shape"), "saved": result.status == "ok"}
        lineage_path = cleaned_dir / f"{var}.lineage.json"
        source = next((d for d in self.datasets if d["variable"] == var), {})
        lineage_path.write_text(
            json.dumps(
                {
                    "source": source,
                    "session": self.session_id,
                    "fixes": records,
                    "stats": stats,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        outputs = {
            "parquet": str(cleaned_dir / f"{var}.parquet"),
            "lineage": str(lineage_path),
        }
        return outputs, stats, ev_id

    # -- internals -----------------------------------------------------------

    def _generate_streaming(self):
        return (
            yield from self._generate_scoped(
                [
                    {"role": "system", "content": prompts.SYSTEM_PROMPT},
                    *self.history,
                ]
            )
        )

    def _generate_scoped(self, msgs: list[dict], stream: bool = True):
        """Complete an explicit message list + fresh registry.

        `stream=False` for housekeeping calls whose text is not the answer —
        rendering an intent check as if it were the model thinking out loud
        would put the checker's words in the analyst's mouth.
        """
        parts: list[str] = []
        context = [*msgs, {"role": "user", "content": self._registry_block()}]
        beats = 0
        for chunk in llm.generate(context):
            if not chunk:  # a heartbeat from the reasoning phase, not content
                beats += 1
                if stream and beats % HEARTBEAT_EVERY == 0:
                    yield StreamText("model", "·")
                continue
            parts.append(chunk)
            if stream:
                yield StreamText("model", chunk)
        if stream:
            yield StreamText("model", "\n")
        return "".join(parts)

    def _exec_events(self, code: str, quiet: bool = False, tolerate_death=False):
        """Execute a cell; yield render events unless quiet; log the exec.

        Returns (result, stream_text, display_paths, ev_id).

        A dead kernel raises KernelLost by default, because every caller that
        reads `status != "ok"` as "that did not work" would otherwise report an
        infrastructure death as a failed fix — and then do it again for every
        remaining finding. QUERY mode opts out via `tolerate_death`: there a
        restart genuinely recovers, since the loads replay and nothing has been
        mutated yet.
        """
        stream_parts: list[str] = []
        display_paths: list[str] = []
        result = None
        for ev in self.client.execute(code, timeout_s=EXEC_TIMEOUT_S):
            if isinstance(ev, StreamOut):
                stream_parts.append(ev.text)
                if not quiet:
                    yield StreamText(ev.name, ev.text)
            elif isinstance(ev, DisplayItem):
                if ev.mime == "image/png" and not ev.dropped:
                    display_paths.append(ev.payload)
                    if not quiet:
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
        if not tolerate_death and result.status in ("kernel_died", "hung"):
            raise KernelLost(
                f"the {result.status.replace('_', ' ')} — "
                "stopping rather than reporting this as a failed fix"
            )
        return result, "".join(stream_parts), display_paths, ev_id

    def _execute_cell(self, code: str):
        result, stream_text, display_paths, ev_id = yield from self._exec_events(
            code, tolerate_death=True
        )
        delta = self._stamp_registry(result.registry, ev_id)
        self.history.append(
            {
                "role": "user",
                "content": self._observation(
                    ev_id, result, stream_text, display_paths, delta
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
            changed = (
                name not in self._registry_prev or self._registry_prev[name] != key
            )
            if name not in self._registry_prev:
                self.origins[name] = ev_id
                delta.append(f"+ {name} ({key[0]} {key[1]})")
            elif self._registry_prev[name] != key:
                self.origins[name] = ev_id
                delta.append(f"~ {name} ({key[0]} {key[1]})")
            remote = entry.get("remote")
            if changed and remote and remote.get("sha256"):
                # R12: a frame load_url stamped in-kernel grounds like a load,
                # so cards and /why reach it. Same content re-stamped is not a
                # duplicate; new content is a second entry, and both are kept.
                already = any(
                    d.get("variable") == name and d.get("sha256") == remote["sha256"]
                    for d in self.datasets
                )
                if not already:
                    self.datasets.append(
                        {
                            "path": remote.get("uri", ""),
                            "sha256": remote["sha256"],
                            "variable": name,
                            "loaded_event": ev_id,
                            "remote": {
                                "uri": remote.get("uri", ""),
                                "fetched_at": remote.get("fetched_at", ""),
                            },
                        }
                    )
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
        for name, code in self.loads:
            result = None
            for ev in self.client.execute(code, timeout_s=EXEC_TIMEOUT_S):
                result = ev
            # a replayed load must put its registry back, or the survivor's
            # next /clean is greeted with "unknown variable" — restarting is
            # not recovering. Stamped with the variable's original event so
            # provenance keeps pointing at the load the operator saw.
            if getattr(result, "status", None) == "ok" and getattr(
                result, "registry", None
            ):
                self._stamp_registry(result.registry, self.origins.get(name, 0))
        self._restore_snapshot()

    def _restore_snapshot(self) -> None:
        """R8's second half: the loads bring back raw files; the verified
        fixes applied since live only in the snapshot. Restored after the
        loads, so restored state wins over raw state."""
        state_file = self.session_dir / "kernel_state.pkl"
        if not (self.snapshots and state_file.exists()):
            return
        result = None
        for ev in self.client.execute(
            snapshot.restore_cell(str(state_file)), timeout_s=EXEC_TIMEOUT_S
        ):
            result = ev
        if getattr(result, "status", None) == "ok" and getattr(
            result, "registry", None
        ):
            prior = dict(self.origins)
            self._stamp_registry(result.registry, 0)
            # a restored variable keeps the origin it had before the death;
            # 0 marks only names this session never saw
            for name, ev_id in prior.items():
                if self.origins.get(name) == 0:
                    self.origins[name] = ev_id

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
