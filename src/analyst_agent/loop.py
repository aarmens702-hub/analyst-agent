"""The turn loop: Session implements SessionLike (spec §1, R1-R8).

run_turn() is a UI-agnostic generator. It yields typed events; drivers render
them and answer GateRequest via gen.send(GateDecision). Repair is not a
mechanism here — a traceback is an ordinary observation and the loop continues.
"""

import ast
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from analyst_agent import llm, prompts, skills, verify
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
from analyst_agent.library import Library, unattended
from analyst_agent.report import CleanReport
from analyst_agent.transcript import Transcript

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
        skills_dir: str = "skills",
    ) -> None:
        self.workspace_root = Path(workspace)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.session_id = self._next_session_id()
        self.session_dir = self.workspace_root / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir)
        self.docker = docker
        self.skills_dir = Path(skills_dir)
        self.library = Library.load(self.skills_dir)

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
        self.report_seq = 0

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
            return
        start_ev = self.transcript.append("user", text=f"/clean {var}")

        code = (
            "from analyst_agent.detect import detect_all\n"
            "import json\n"
            f"print(json.dumps(detect_all({var}, {var!r})))"
        )
        result, stream, _, diag_ev = yield from self._exec_events(code, quiet=True)
        if result.status != "ok":
            err = (result.error or {}).get("evalue", result.status)
            yield Notice("error", f"diagnosis failed: {err}")
            return
        diagnosis = json.loads(stream.strip().splitlines()[-1])
        fixable = [f for f in diagnosis["findings"] if not f["indicator"]]
        indicators = [f for f in diagnosis["findings"] if f["indicator"]]
        clear = diagnosis["clear"]
        yield StreamText(
            "stdout", self._diagnosis_text(var, fixable, indicators, clear)
        )

        baseline_cols = yield from self._snapshot_baseline(var)
        records: list[dict] = []
        evs_chain = [start_ev, diag_ev]
        for i, finding in enumerate(fixable, 1):
            # the library first: a proven skill costs no model call at all
            rec = yield from self._skill_attempt(
                var, finding, i, len(fixable), baseline_cols
            )
            if rec is None:
                rec = yield from self._fix_mini_turn(
                    var, finding, i, len(fixable), baseline_cols
                )
            records.append(rec)
            evs_chain.extend(rec["transcript_evs"])
            if rec["status"] == "fixed":
                baseline_cols = yield from self._snapshot_baseline(var)

        admitted = yield from self._skill_pass(var, records)

        outputs: dict = {}
        stats: dict = {}
        if any(r["status"] == "fixed" for r in records):
            outputs, stats, out_ev = yield from self._write_cleaned(var, records)
            evs_chain.append(out_ev)

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
            fixes=records,
            indicators=indicators,
            clear=clear,
            outputs=outputs,
            stats=stats,
            skills_admitted=admitted,
            event_chain=[*evs_chain, rep_ev],
            created=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        report.save(self.session_dir / "clean_reports")
        yield StreamText("stdout", "\n" + report.to_markdown() + "\n")

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
                        "variable": f"{name}[{entry['slice']!r}]",
                        "loaded_event": ev_id,
                    }
                )
        yield StreamText("stdout", self._drift_text(name, meta))
        return meta

    def clean_family(self, pattern: str, name: str):
        """P2.5: harmonize a family once, then clean each slice with the library.

        The second half is deliberately the ordinary CLEAN flow. Harmonizing is
        what makes the slices comparable; the compounding is what happens next,
        when one skill fixes the same disease in twenty-one files.
        """
        meta = yield from self.load_family(pattern, name)
        if not meta:
            return

        code = (
            "from analyst_agent.detect import detect_family\n"
            "import json\n"
            f"print(json.dumps(detect_family({name})))"
        )
        result, stream, _, _ev = yield from self._exec_events(code, quiet=True)
        drift = []
        if result.status == "ok":
            try:
                drift = json.loads(stream.strip().splitlines()[-1])
            except (ValueError, IndexError):
                drift = []

        harmonized = False
        if drift:
            # a mapping this family already confirmed replays for free (R6)
            harmonized = yield from self._replay_mapping(name)
            if not harmonized:
                harmonized = yield from self._harmonize(name, meta, drift)
            if not harmonized:
                yield Notice(
                    "family", "harmonizing failed — cleaning slices as they are"
                )
        else:
            harmonized = True  # nothing to reconcile is a kind of harmonized
            yield Notice("family", "slices already share one schema")

        hits: dict = {}
        for entry in meta:
            var = re.sub(r"\W+", "_", f"{name}_{entry['slice']}").strip("_")
            res, _, _, ev_id = yield from self._exec_events(
                f"{var} = {name}[{entry['slice']!r}]\n{var}.shape", quiet=True
            )
            if res.status != "ok":
                continue
            self._stamp_registry(res.registry, ev_id)
            yield StreamText("stdout", f"\n── slice {entry['slice']} ──\n")
            before = dict(self._skill_uses())
            yield from self.clean(var)
            for skill_name, count in self._skill_uses().items():
                gained = count - before.get(skill_name, 0)
                if gained:
                    hits[skill_name] = hits.get(skill_name, 0) + gained

        summary = {
            "family": name,
            "pattern": pattern,
            "slices": [m["slice"] for m in meta],
            "rows": sum(m["rows"] for m in meta),
            "harmonized": harmonized,
            "drift_findings": len(drift),
            # the number the whole phase exists to produce: one skill, many files
            "skill_hits": hits,
        }
        path = self.session_dir / f"family_{name}.json"
        path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        replayed = sum(hits.values())
        yield StreamText(
            "stdout",
            f"\nfamily {name}: {len(meta)} slices, {summary['rows']:,} rows · "
            f"{replayed} fix(es) served by {len(hits)} skill(s) · {path}\n",
        )

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
            decision = yield GateRequest(body, attempts, title=title)
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
                decision = yield GateRequest(code, 1, title=title)
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
            if result.status == "ok":
                self._stamp_registry(result.registry, ev_id)
                vres, _, _, v_ev = yield from self._exec_events(
                    verify.verify_cell(var, finding, baseline_cols), quiet=True
                )
                evs.append(v_ev)
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
            self.library.record(entry["name"], success=False, dataset=sha, events=evs)
            self.library.save()
            state = self.library.entries[entry["name"]]["state"]
            yield Notice(
                "skill",
                f"{entry['name']} failed verification — reverted"
                + (" (and retired)" if state == "retired" else ""),
            )

        return None

    def _skill_pass(self, var: str, records: list[dict]):
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
        admitted: list[str] = []
        for rec in candidates:
            name = yield from self._propose_skill(var, rec)
            if name:
                admitted.append(name)
        if admitted:
            self.library.save()
        return admitted

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

            preview = f"# {skill.name}\n# {skill.description}\n\n{skill.fix_source}"
            decision = yield GateRequest(
                preview,
                1,
                title=(
                    f"admit skill {skill.name} · d{finding['disease']} · "
                    f"reproduces the case it came from"
                ),
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
        self, var: str, fixable: list, indicators: list, clear: list
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

    def _exec_events(self, code: str, quiet: bool = False):
        """Execute a cell; yield render events unless quiet; log the exec.

        Returns (result, stream_text, display_paths, ev_id).
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
        return result, "".join(stream_parts), display_paths, ev_id

    def _execute_cell(self, code: str):
        result, stream_text, display_paths, ev_id = yield from self._exec_events(code)
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
