"""SKILL.md packaging: the format a verified fix takes when it becomes reusable
(P2 R1-R4).

The Agent Skills spec allows exactly six frontmatter fields, and claude.ai and
the Skills API hard-fail on a seventh — so this module is deliberately strict in
both directions. It renders only those six, and it refuses to parse anything
outside the small YAML subset they need. A skill that cannot be uploaded is not
a skill, and silence about that would be discovered at export time (P4).
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

SPEC_FIELDS = (
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
)

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
MAX_DESCRIPTION = 1024
MAX_BODY_LINES = 500
DEFAULT_LICENSE = "MIT"
DEFAULT_COMPATIBILITY = ">=0.1.0"


@dataclass
class Skill:
    """One packaged fix. `fix_source` and `test_source` become scripts/."""

    name: str
    description: str
    fix_source: str
    test_source: str
    metadata: dict = field(default_factory=dict)
    license: str = DEFAULT_LICENSE
    compatibility: str = DEFAULT_COMPATIBILITY
    allowed_tools: list = field(default_factory=list)
    body: str = ""


def render(skill: Skill) -> str:
    """The SKILL.md text: six-field frontmatter, then the body."""
    lines = [
        "---",
        f"name: {skill.name}",
        f"description: {_scalar(skill.description)}",
        f"license: {_scalar(skill.license)}",
        f"compatibility: {_scalar(skill.compatibility)}",
        "metadata:",
    ]
    lines += [
        f"  {key}: {_scalar(str(value))}" for key, value in skill.metadata.items()
    ]
    lines.append(f"allowed-tools: [{', '.join(skill.allowed_tools)}]")
    lines += ["---", ""]
    return "\n".join(lines) + skill.body


def _scalar(value: str) -> str:
    """Quote a scalar so punctuation can never be read as YAML structure."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse(text: str) -> tuple[dict, str]:
    """Split SKILL.md into its frontmatter and body.

    A deliberately narrow YAML reader: top-level `key: value`, one level of
    indented string->string map, and a flow-style list. Anything else raises
    rather than guessing — a mis-parsed skill would fail at upload time, far
    from the mistake.
    """
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must open with a --- frontmatter block")
    _, _, rest = text.partition("---\n")
    front_text, sep, body = rest.partition("\n---")
    if not sep:
        raise ValueError("unterminated frontmatter block")

    front: dict = {}
    current: dict | None = None
    for raw in front_text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw.startswith("  ")
        key, colon, value = raw.strip().partition(":")
        if not colon:
            raise ValueError(f"outside the supported YAML subset: {raw!r}")
        key, value = key.strip(), value.strip()
        if indented:
            if current is None:
                raise ValueError(f"outside the supported YAML subset: {raw!r}")
            if not value:
                raise ValueError("nested maps are outside the supported YAML subset")
            current[key] = _unscalar(value)
        elif not value:
            current = front[key] = {}
        else:
            front[key] = _unflow(value) if value.startswith("[") else _unscalar(value)
            current = None
    return front, body.lstrip("-").lstrip("\n")


def _unscalar(value: str) -> str:
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _unflow(value: str) -> list:
    inner = value.strip()[1:-1].strip()
    return [_unscalar(item.strip()) for item in inner.split(",")] if inner else []


def validate(skill: Skill, dirname: str) -> list[str]:
    """Every reason this skill would be refused, as plain sentences. Empty list
    means it is admissible. Reporting all problems at once matters: the author
    is a model given one revision attempt (R6)."""
    problems = []
    if not NAME_RE.match(skill.name or ""):
        problems.append(f"name {skill.name!r} must be 1-64 characters of [a-z0-9-]")
    if skill.name != dirname:
        problems.append(f"name {skill.name!r} must equal its directory {dirname!r}")
    if not 1 <= len(skill.description or "") <= MAX_DESCRIPTION:
        problems.append(
            f"description must be 1-{MAX_DESCRIPTION} characters "
            f"(got {len(skill.description or '')}) and say what it does and when to use it"
        )
    off_spec = [
        key
        for key, value in skill.metadata.items()
        if not isinstance(key, str) or not isinstance(value, str)
    ]
    if off_spec:
        problems.append(
            f"metadata must map strings to strings; {', '.join(map(repr, off_spec))} does not"
        )
    if skill.body.count("\n") >= MAX_BODY_LINES:
        problems.append(
            f"body must stay under {MAX_BODY_LINES} lines so it loads only on activation"
        )
    problems += _fix_contract_problems(skill.fix_source)
    return problems


def _fix_contract_problems(source: str) -> list[str]:
    """R3, checked against the parse tree: a module-level `fix` taking exactly
    (df, columns). A docstring that mentions the signature is not one."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError as exc:
        return [f"scripts/fix.py has a syntax error: {exc.msg} (line {exc.lineno})"]
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "fix":
            names = [arg.arg for arg in node.args.args]
            if names == ["df", "columns"]:
                return []
            return [
                f"fix(df, columns) is the contract; this takes ({', '.join(names)})"
            ]
    return ["scripts/fix.py must define a top-level fix(df, columns)"]


def save(skill: Skill, root) -> Path:
    """Write `root/<name>/` as SKILL.md + scripts/. Refuses invalid skills
    before creating anything: a half-written skill folder would be found later
    by retrieval and treated as real."""
    path = Path(root) / skill.name
    problems = validate(skill, skill.name)
    if problems:
        raise ValueError(f"refusing to write {skill.name!r}: " + "; ".join(problems))
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(render(skill), encoding="utf-8")
    (path / "scripts" / "fix.py").write_text(skill.fix_source, encoding="utf-8")
    (path / "scripts" / "test_fix.py").write_text(skill.test_source, encoding="utf-8")
    return path


def load(path) -> Skill:
    """Read a skill folder back, applying the same rules as save."""
    path = Path(path)
    front, body = parse((path / "SKILL.md").read_text(encoding="utf-8"))
    extra = set(front) - set(SPEC_FIELDS)
    if extra:
        raise ValueError(
            f"{path.name}/SKILL.md carries fields outside the spec's six: "
            f"{', '.join(sorted(extra))} — upload would be rejected"
        )
    skill = Skill(
        name=front.get("name", ""),
        description=front.get("description", ""),
        fix_source=(path / "scripts" / "fix.py").read_text(encoding="utf-8"),
        test_source=(path / "scripts" / "test_fix.py").read_text(encoding="utf-8"),
        metadata=front.get("metadata", {}),
        license=front.get("license", DEFAULT_LICENSE),
        compatibility=front.get("compatibility", DEFAULT_COMPATIBILITY),
        allowed_tools=front.get("allowed-tools", []),
        body=body,
    )
    problems = validate(skill, path.name)
    if problems:
        raise ValueError(f"{path.name} is not a valid skill: " + "; ".join(problems))
    return skill


PROPOSAL_TAGS = ("name", "description", "fix", "test")


def parse_proposal(text: str) -> dict | None:
    """Pull a skill proposal out of a model reply (R6).

    Returns None when any block is missing, which the caller treats as a
    malformed reply worth one revision — the same discipline the QUERY loop
    applies to a missing <execute> tag.
    """
    found = {}
    for tag in PROPOSAL_TAGS:
        match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
        if not match:
            return None
        found[tag] = match.group(1).strip()
    if not found["name"] or not found["fix"]:
        return None
    return found


def from_proposal(proposal: dict, finding: dict, source: str = "") -> Skill:
    """Build a Skill from a parsed proposal, stamping where it came from.

    Provenance goes in `metadata` as strings because the spec allows nothing
    else there — and a skill that cannot say which disease and which case it
    was born from cannot be audited later.
    """
    return Skill(
        name=proposal["name"],
        description=proposal["description"],
        fix_source=proposal["fix"] + "\n",
        test_source=proposal["test"] + "\n",
        metadata={
            "disease": str(finding["disease"]),
            "slug": str(finding.get("slug", "")),
            "born_from": source,
            "version": "1",
        },
        body=(
            f"## What this fixes\n\n{proposal['description']}\n\n"
            f"## Where it came from\n\n"
            f"Disease {finding['disease']} ({finding.get('slug', '')}) on "
            f"`{source or 'an earlier dataset'}`: {finding.get('evidence', '')}\n"
        ),
    )
