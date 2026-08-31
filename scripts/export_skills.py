"""Bundle admissible skills into a distributable, scrubbed directory (P4).

Skills as stored carry things that must not travel: metadata.born_from names a
real dataset path from this machine, the library holds skills still on
probation or already retired that haven't earned distribution, and a skill's
body can echo the evidence string — and the path — that spawned it. This
script selects only admissible skills, scrubs what's machine-specific,
re-checks conformance stricter than a live library needs, and writes the
result as a self-contained bundle with a manifest and a README.

Usage: uv run python scripts/export_skills.py [--out DIR] [--zip] [--strict]
    [--include-probation] [--all] [--include-retired] [--dry-run]
"""

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from crivo import library, skills

# A path boundary: start-of-string, whitespace, quote, backtick, or paren —
# never a colon (URL scheme), another slash (mid-URL), or a word character
# (mid-word). Two or more segments, so a slash-command like "/clean" mentioned
# in prose doesn't false-positive as a leaked filesystem path.
_ABS_PATH_RE = re.compile(r"(?<![:\w/])(/[\w.\-]+(?:/[\w.\-]+)+)")


def _absolute_paths(text: str) -> list[str]:
    """Every absolute filesystem path mentioned anywhere in `text`, deduped and
    sorted for a deterministic report."""
    return sorted(set(_ABS_PATH_RE.findall(text)))


MAX_FILE_BYTES = 1_048_576  # 1 MiB; a skill is prose and two small scripts
ALLOWED_TOP_LEVEL = {"SKILL.md", "scripts"}
ALLOWED_SCRIPTS = {"fix.py", "test_fix.py"}


def _shape_problems(folder: Path) -> list[str]:
    """R1's exact layout, enforced on the source folder: nothing beyond
    SKILL.md and scripts/{fix.py,test_fix.py}, and no file over the size cap —
    a stray file would otherwise ride along into the bundle silently."""
    problems = []
    extra_top = {p.name for p in folder.iterdir()} - ALLOWED_TOP_LEVEL
    if extra_top:
        problems.append(
            "extra files beyond SKILL.md and scripts/: " + ", ".join(sorted(extra_top))
        )
    scripts_dir = folder / "scripts"
    if scripts_dir.is_dir():
        extra_scripts = {p.name for p in scripts_dir.iterdir()} - ALLOWED_SCRIPTS
        if extra_scripts:
            problems.append(
                "extra files under scripts/: " + ", ".join(sorted(extra_scripts))
            )
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.stat().st_size > MAX_FILE_BYTES:
            problems.append(
                f"{path.relative_to(folder)} is {path.stat().st_size:,} bytes, "
                f"over the {MAX_FILE_BYTES:,}-byte export limit"
            )
    return problems


def _scrub(skill: skills.Skill, *, strict: bool) -> tuple[skills.Skill, list[str]]:
    """Generalise metadata.born_from to a basename (or drop it under --strict),
    and carry that same generalisation into the body — from_proposal() echoes
    the same path there as quoted provenance. Returns the scrubbed copy and
    exactly what changed, as report-ready sentences; nothing is ever silent."""
    notes = []
    metadata = dict(skill.metadata)
    body = skill.body
    born_from = metadata.get("born_from", "")

    if born_from:
        if strict:
            del metadata["born_from"]
            notes.append(f"metadata.born_from: dropped {born_from!r} (--strict)")
        else:
            basename = Path(born_from).name
            if basename != born_from:
                metadata["born_from"] = basename
                notes.append(f"metadata.born_from: {born_from!r} -> {basename!r}")
                if born_from in body:
                    body = body.replace(born_from, basename)
                    notes.append(f"body: replaced {born_from!r} with {basename!r}")

    return replace(skill, metadata=metadata, body=body), notes


@dataclass
class SkillResult:
    """What happened to one candidate skill folder discovered under skills/."""

    name: str
    state: str
    selected: bool
    ok: bool = False
    problems: list = field(default_factory=list)
    scrubbed: list = field(default_factory=list)
    disease: object = None
    successes: object = None
    failures: object = None
    sha256: str | None = None


@dataclass
class ExportReport:
    """The outcome of one export run: every candidate considered, selected or not."""

    skills_root: Path
    out: Path | None
    dry_run: bool
    results: list
    zip_path: Path | None = None

    @property
    def exported(self) -> list[str]:
        return [r.name for r in self.results if r.selected and r.ok]

    @property
    def refused(self) -> list[str]:
        return [r.name for r in self.results if r.selected and not r.ok]

    @property
    def skipped(self) -> list[str]:
        return [r.name for r in self.results if not r.selected]


def _discover(skills_root: Path) -> list[tuple[str, Path, bool]]:
    """(name, folder, is_retired_location) for every candidate skill folder."""
    found = []
    if skills_root.is_dir():
        for child in sorted(skills_root.iterdir()):
            if child.is_dir() and child.name != library.RETIRED_DIR:
                found.append((child.name, child, False))
        retired_root = skills_root / library.RETIRED_DIR
        if retired_root.is_dir():
            for child in sorted(retired_root.iterdir()):
                if child.is_dir():
                    found.append((child.name, child, True))
    return found


def _resolve_state(entries: dict, name: str, *, is_retired_location: bool) -> str:
    """Ledger state for `name`, or "unknown"/"retired" when the ledger has
    nothing to say — location still counts for skills/retired/<name>/."""
    entry = entries.get(name)
    if entry is not None:
        return entry.get("state", "unknown")
    return "retired" if is_retired_location else "unknown"


def _selected(
    state: str, *, include_probation: bool, include_all: bool, include_retired: bool
) -> bool:
    """Which ledger states this run's flags admit. Never guesses: an
    unrecognised state string selects nothing."""
    if state == "retired":
        return include_retired
    if state == "proven":
        return True
    if state == "probation":
        return include_probation or include_all
    if state == "unknown":
        return include_all
    return False


def _build_manifest(results: list) -> dict:
    """name, disease, state at export time, successes/failures, sha256 — one
    row per skill actually written, so the manifest never lists more than
    what's on disk beside it."""
    return {
        "skills": [
            {
                "name": r.name,
                "disease": _as_disease(r.disease),
                "state": r.state,
                "successes": r.successes,
                "failures": r.failures,
                "sha256": r.sha256,
            }
            for r in results
            if r.selected and r.ok
        ]
    }


def _render_readme(results: list) -> str:
    """The bundle's front door: what these skills are, and how to use them in
    any SKILL.md-compatible tool — not just this one."""
    admitted = [r for r in results if r.selected and r.ok]
    lines = [
        "# Exported skills",
        "",
        (
            "These are governed, reusable data-cleaning fixes from an"
            " crivo skill library. Each one was proposed by a model"
            " after a verified fix, then admitted only after passing its"
            " shipped test and a re-run against the real case that produced"
            " it — test-gated, not self-reported. Every use is"
            " outcome-scored: the ledger tracks successes and failures, and"
            " a skill that fails verification twice in a row is retired"
            " automatically. See MANIFEST.json for the state and score each"
            " skill carried at export time."
        ),
        "",
        "## Using these skills",
        "",
        (
            "Each `<name>/` folder is a standard SKILL.md skill: `SKILL.md`"
            " carries the six-field frontmatter plus a description of what"
            " it fixes and when, `scripts/fix.py` exposes"
            " `fix(df, columns)`, and `scripts/test_fix.py` is its"
            " regression test. Drop any folder into a SKILL.md-compatible"
            " tool's skills directory (Claude Code, claude.ai, or any other"
            " Agent Skills-compatible product) and it activates the same"
            " way any other skill does."
        ),
        "",
        "## Contents",
        "",
    ]
    for r in admitted:
        successes = r.successes if r.successes is not None else "?"
        failures = r.failures if r.failures is not None else "?"
        lines.append(
            f"- **{r.name}** (disease {r.disease}, {r.state}): "
            f"{successes} successes, {failures} failures"
        )
    lines.append("")
    return "\n".join(lines)


def _as_disease(value) -> int | None:
    """The ledger holds an int, SKILL.md metadata holds a str (the spec allows
    nothing else there). A manifest reporting whichever it happened to read is
    one a consumer cannot parse without guessing."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def run_export(
    skills_root: Path,
    out: Path,
    *,
    include_probation: bool = False,
    include_all: bool = False,
    include_retired: bool = False,
    strict: bool = False,
    dry_run: bool = False,
    make_zip: bool = False,
) -> ExportReport:
    """Select, scrub, and write admissible skills from `skills_root` into `out`."""
    import hashlib
    import json

    skills_root = Path(skills_root)
    out = Path(out)
    lib = library.Library.load(skills_root)
    results = []
    admitted = []

    for name, folder, is_retired in _discover(skills_root):
        state = _resolve_state(lib.entries, name, is_retired_location=is_retired)
        selected = _selected(
            state,
            include_probation=include_probation,
            include_all=include_all,
            include_retired=include_retired,
        )
        entry = lib.entries.get(name)
        result = SkillResult(name=name, state=state, selected=selected)
        if entry is not None:
            result.disease = entry.get("disease")
            result.successes = entry.get("successes")
            result.failures = entry.get("failures")

        if selected:
            problems = _shape_problems(folder)
            scrubbed = text = None
            try:
                loaded = skills.load(folder)
            except (ValueError, OSError) as exc:
                problems.append(str(exc))
            else:
                scrubbed, notes = _scrub(loaded, strict=strict)
                result.scrubbed = notes
                if result.disease is None:
                    result.disease = scrubbed.metadata.get("disease")
                problems += skills.validate(scrubbed, folder.name)
                text = skills.render(scrubbed)
                leaks = _absolute_paths(text)
                if leaks:
                    problems.append(
                        "absolute path(s) leaked into SKILL.md: " + ", ".join(leaks)
                    )
            if problems:
                result.ok = False
                result.problems = problems
            else:
                result.ok = True
                result.sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
                admitted.append((name, scrubbed))
        results.append(result)

    if dry_run:
        return ExportReport(
            skills_root=skills_root, out=None, dry_run=dry_run, results=results
        )

    report = ExportReport(
        skills_root=skills_root, out=out, dry_run=dry_run, results=results
    )

    out.mkdir(parents=True, exist_ok=True)
    for _, admitted_skill in admitted:
        skills.save(admitted_skill, out)
    manifest = _build_manifest(results)
    (out / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "README.md").write_text(_render_readme(results), encoding="utf-8")

    if make_zip:
        import shutil

        archive = shutil.make_archive(str(out), "zip", root_dir=str(out))
        report.zip_path = Path(archive)
    return report


def _print_report(report: ExportReport) -> None:
    """Human-readable mirror of the ExportReport: nothing about selection,
    scrubbing, or refusal happens silently."""
    for r in report.results:
        if not r.selected:
            print(f"skip     {r.name} ({r.state}; pass a selection flag to include it)")
            continue
        if r.ok:
            verb = "would export" if report.dry_run else "exported"
            print(f"{verb:<12} {r.name} ({r.state})")
            for note in r.scrubbed:
                print(f"             scrubbed: {note}")
        else:
            print(f"refused      {r.name}")
            for problem in r.problems:
                print(f"             {problem}")

    verb = "would be exported" if report.dry_run else "exported"
    tail = "" if report.dry_run else f" to {report.out}"
    print(f"\n{len(report.exported)} skill(s) {verb}{tail}")
    if report.zip_path:
        print(f"zip written to {report.zip_path}")


def main() -> None:
    import argparse

    repo_root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--skills-root",
        type=Path,
        default=repo_root / "skills",
        help="library root to export from (default: repo skills/)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=repo_root / "dist" / "skills-export",
        help="output bundle directory",
    )
    ap.add_argument(
        "--zip", dest="make_zip", action="store_true", help="also write <out>.zip"
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="drop metadata.born_from entirely instead of generalising it",
    )
    ap.add_argument(
        "--include-probation", action="store_true", help="also export probation skills"
    )
    ap.add_argument(
        "--all",
        dest="include_all",
        action="store_true",
        help="also export probation and unknown-state skills (never retired)",
    )
    ap.add_argument(
        "--include-retired", action="store_true", help="also export retired skills"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be exported without writing anything",
    )
    args = ap.parse_args()

    report = run_export(
        args.skills_root,
        args.out,
        include_probation=args.include_probation,
        include_all=args.include_all,
        include_retired=args.include_retired,
        strict=args.strict,
        dry_run=args.dry_run,
        make_zip=args.make_zip,
    )
    _print_report(report)


if __name__ == "__main__":
    main()
