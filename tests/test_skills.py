"""Tests for the SKILL.md format layer (P2 R1-R4).

Portability is the whole point of the format, so these tests are mostly about
what the validator *refuses*: a seventh frontmatter field, a name that doesn't
match its directory, metadata that isn't string->string. claude.ai and the
Skills API hard-fail on those, and a skill that can't be uploaded isn't a skill.
"""

import pytest

from analyst_agent import skills


def make(**kw) -> "skills.Skill":
    base = {
        "name": "fix-sentinel-missing",
        "description": "Replace missing-data tokens with NaN. Use when a column "
        "holds 'N/A' or 'unknown' as if they were values.",
        "fix_source": "def fix(df, columns):\n    return df.copy()\n",
        "test_source": "def test_fix():\n    assert True\n",
        "metadata": {"disease": "4"},
    }
    base.update(kw)
    return skills.Skill(**base)


def test_render_emits_the_six_spec_fields_and_nothing_else() -> None:
    text = skills.render(make())
    keys = [
        line.split(":", 1)[0]
        for line in text.splitlines()[1:]
        if line and not line.startswith((" ", "-", "#")) and ":" in line
    ]
    assert keys[: len(skills.SPEC_FIELDS)] == list(skills.SPEC_FIELDS)
    assert "name: fix-sentinel-missing" in text


def test_parse_round_trips_frontmatter_and_body() -> None:
    original = make(
        body="## How it works\n\nNormalise, then replace.\n",
        metadata={"disease": "4", "version": "1", "spawned_by": "beers/ibu"},
    )
    front, body = skills.parse(skills.render(original))
    assert set(front) == set(skills.SPEC_FIELDS)
    assert front["name"] == original.name
    assert front["description"] == original.description  # quoting survives
    assert front["metadata"] == original.metadata
    assert front["allowed-tools"] == []
    assert body.strip() == original.body.strip()


def test_parse_refuses_what_it_cannot_represent() -> None:
    """Guessing at richer YAML would produce a skill that fails at upload time,
    far from the mistake. Refuse instead."""
    deeper = "---\nname: x\nmetadata:\n  nested:\n    deeper: 1\n---\nbody\n"
    with pytest.raises(ValueError, match="subset"):
        skills.parse(deeper)
    with pytest.raises(ValueError, match="frontmatter"):
        skills.parse("# just a heading\n")
    with pytest.raises(ValueError, match="unterminated"):
        skills.parse("---\nname: x\n")


def test_validate_passes_a_well_formed_skill() -> None:
    assert skills.validate(make(), "fix-sentinel-missing") == []


@pytest.mark.parametrize(
    "bad", ["Fix_Upper", "fix sentinel", "", "x" * 65, "fix/slash"]
)
def test_name_charset_and_directory_agreement_are_enforced(bad: str) -> None:
    assert any("name" in p for p in skills.validate(make(name=bad), bad))
    mismatch = skills.validate(make(), "some-other-directory")
    assert any("directory" in p for p in mismatch)


def test_description_metadata_and_body_limits_are_enforced() -> None:
    """The spec's own limits: description 1-1024, metadata string->string,
    body under 500 lines so progressive disclosure stays cheap."""
    here = "fix-sentinel-missing"
    for bad in ("", "x" * 1025):
        assert any(
            "description" in p for p in skills.validate(make(description=bad), here)
        )
    assert any(
        "metadata" in p for p in skills.validate(make(metadata={"disease": 4}), here)
    )
    assert any("500" in p for p in skills.validate(make(body="x\n" * 501), here))


def test_fix_source_must_honour_the_contract() -> None:
    """R3: a top-level `fix(df, columns)`. Checked by parsing, not by string
    search — a docstring mentioning fix(df, columns) is not an implementation."""
    here = "fix-sentinel-missing"
    no_fix = make(
        fix_source='"""calls fix(df, columns)"""\ndef other(df):\n    return df\n'
    )
    assert any("fix(df, columns)" in p for p in skills.validate(no_fix, here))
    wrong_arity = make(fix_source="def fix(df):\n    return df\n")
    assert any("fix(df, columns)" in p for p in skills.validate(wrong_arity, here))
    broken = make(fix_source="def fix(df, columns:\n")
    assert any("syntax" in p.lower() for p in skills.validate(broken, here))


def test_save_writes_the_layout_and_load_round_trips_it(tmp_path) -> None:
    original = make(metadata={"disease": "4", "version": "2"}, body="## Notes\n")
    path = skills.save(original, tmp_path)
    assert path == tmp_path / "fix-sentinel-missing"
    assert (path / "scripts" / "fix.py").read_text().startswith("def fix(df, columns)")
    assert (path / "scripts" / "test_fix.py").exists()

    loaded = skills.load(path)
    assert loaded.name == original.name
    assert loaded.description == original.description
    assert loaded.metadata == original.metadata
    assert loaded.fix_source == original.fix_source
    assert loaded.test_source == original.test_source


def test_save_refuses_an_invalid_skill_without_writing_anything(tmp_path) -> None:
    """A half-written folder would be found by retrieval later and treated as
    a real skill, so refusal has to happen before anything is created."""
    with pytest.raises(ValueError, match="name"):
        skills.save(make(name="NOT VALID"), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_load_refuses_a_seventh_frontmatter_field(tmp_path) -> None:
    """The field a human helpfully adds by hand is the one that breaks upload."""
    path = skills.save(make(), tmp_path)
    md = (
        (path / "SKILL.md")
        .read_text()
        .replace("license:", "author: someone\nlicense:", 1)
    )
    (path / "SKILL.md").write_text(md)
    with pytest.raises(ValueError, match="author"):
        skills.load(path)
