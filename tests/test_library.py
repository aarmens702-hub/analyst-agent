"""Tests for the skill library and its governance (P2 R10-R14).

The rules under test are the ones that decide whether a skill runs unattended,
so they are asserted at their boundaries: three successes promote, three
successes on one dataset do not, two consecutive failures retire.
"""

from crivo import library


def test_register_starts_a_skill_on_probation() -> None:
    lib = library.Library(root="skills")
    entry = lib.register("fix-sentinel-missing", disease=4)

    assert entry["state"] == "probation"
    assert entry["disease"] == 4
    assert entry["successes"] == entry["failures"] == entry["uses"] == 0
    assert entry["datasets"] == []
    assert lib.entries["fix-sentinel-missing"] is entry


def test_record_counts_outcomes_and_the_sources_they_came_from() -> None:
    lib = library.Library(root="skills")
    lib.register("fix-whitespace", disease=6)

    lib.record("fix-whitespace", success=True, dataset="sha-beers", events=[7, 8])
    entry = lib.record("fix-whitespace", success=False, dataset="sha-beers", events=[9])

    assert entry["uses"] == 2
    assert entry["successes"] == 1
    assert entry["failures"] == 1
    assert entry["datasets"] == ["sha-beers"]  # the same source counts once
    assert entry["recent"] == ["success", "failure"]
    assert [e["events"] for e in entry["events"]] == [[7, 8], [9]]
    assert entry["last_used"]


def test_promotion_needs_successes_on_more_than_one_source() -> None:
    """The rule that guards the Q1 bet: a skill generalised from a single case
    proves nothing by succeeding on that same case again."""
    lib = library.Library(root="skills")
    lib.register("fix-sentinel-missing", disease=4)
    for _ in range(5):
        entry = lib.record("fix-sentinel-missing", success=True, dataset="sha-beers")
    assert entry["state"] == "probation", "five wins on one file is still one file"

    entry = lib.record("fix-sentinel-missing", success=True, dataset="sha-hospital")
    assert entry["state"] == "proven"


def test_a_recent_failure_holds_a_skill_back_until_it_ages_out() -> None:
    lib = library.Library(root="skills")
    lib.register("fix-units", disease=1)
    lib.record("fix-units", success=True, dataset="a")
    lib.record("fix-units", success=False, dataset="a")
    entry = lib.record("fix-units", success=True, dataset="b")
    lib.record("fix-units", success=True, dataset="c")
    assert entry["state"] == "probation", "a failure in the window blocks promotion"

    for _ in range(4):  # push the failure out of the last-5 window
        entry = lib.record("fix-units", success=True, dataset="d")
    assert entry["state"] == "proven"


def test_two_consecutive_failures_retire_a_skill() -> None:
    lib = library.Library(root="skills")
    lib.register("fix-dates", disease=2)
    lib.record("fix-dates", success=False, dataset="a")
    entry = lib.record("fix-dates", success=False, dataset="a")

    assert entry["state"] == "retired"
    assert "twice in a row" in entry["retired_reason"]
    assert entry["events"][-1]["outcome"] == "retired"


def test_candidates_match_on_disease_and_rank_by_record() -> None:
    lib = library.Library(root="skills")
    for name in ("fix-weak", "fix-strong", "fix-other"):
        lib.register(name, disease=6 if name != "fix-other" else 1)
    lib.record("fix-weak", success=True, dataset="a")
    for source in ("a", "b", "c"):
        lib.record("fix-strong", success=True, dataset=source)

    names = [e["name"] for e in lib.candidates(disease=6)]
    assert names == ["fix-strong", "fix-weak"], "a better record goes first"
    assert lib.candidates(disease=99) == []

    lib.retire("fix-strong", reason="superseded")
    assert [e["name"] for e in lib.candidates(disease=6)] == ["fix-weak"]


def test_only_a_proven_skill_on_an_auto_finding_runs_unattended() -> None:
    """R12. Both halves are required: the disease must be safe to fix without
    judgement, and the skill must have earned it."""
    lib = library.Library(root="skills")
    probation = lib.register("fix-new", disease=4)
    proven = lib.register("fix-old", disease=4)
    proven["state"] = "proven"

    assert library.unattended(proven, "AUTO") is True
    assert library.unattended(proven, "GATE") is False
    assert library.unattended(proven, "HUMAN") is False
    assert library.unattended(probation, "AUTO") is False


def test_ledger_round_trips_through_disk(tmp_path) -> None:
    lib = library.Library(root=tmp_path)
    lib.register("fix-sentinel-missing", disease=4)
    lib.record("fix-sentinel-missing", success=True, dataset="sha-beers", events=[3])
    lib.save()

    assert (tmp_path / library.LEDGER_NAME).exists()
    reopened = library.Library.load(tmp_path)
    assert reopened.entries == lib.entries
    assert library.Library.load(tmp_path / "nowhere").entries == {}


def test_retiring_moves_the_folder_aside_rather_than_deleting_it(tmp_path) -> None:
    """The graveyard is evidence for the day-1-vs-day-N story, not garbage."""
    folder = tmp_path / "fix-dates" / "scripts"
    folder.mkdir(parents=True)
    (folder / "fix.py").write_text("def fix(df, columns):\n    return df\n")

    lib = library.Library(root=tmp_path)
    lib.register("fix-dates", disease=2)
    lib.retire("fix-dates", reason="superseded")

    assert not (tmp_path / "fix-dates").exists()
    moved = tmp_path / library.RETIRED_DIR / "fix-dates"
    assert (moved / "scripts" / "fix.py").exists()
    assert lib.entries["fix-dates"]["state"] == "retired"


def test_the_active_cap_evicts_the_worst_performer(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(library, "ACTIVE_CAP", 3)
    lib = library.Library(root=tmp_path)
    for name in ("fix-a", "fix-b", "fix-c"):
        lib.register(name, disease=4)
    lib.record("fix-a", success=True, dataset="x")
    lib.record("fix-b", success=True, dataset="x")
    lib.record("fix-b", success=True, dataset="y")
    # fix-c has done nothing at all, so it is the one to lose

    lib.register("fix-d", disease=4)
    states = {name: entry["state"] for name, entry in lib.entries.items()}
    assert states["fix-c"] == "retired"
    assert "cap" in lib.entries["fix-c"]["retired_reason"]
    assert [s for s in states.values() if s != "retired"] == ["probation"] * 3
