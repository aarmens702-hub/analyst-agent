"""T2.5 flat governance config: one schema-validated file into live objects.

specs/2026-09-04-a1-build-plan.md T2.5 closes the policy layer with "one flat
schema-validated governance config"; the mailo governance-config pattern
(docs/research/2026-09-04-mining-synthesis.md, candidate 2) is why: policies,
judge rubric, and routing thresholds live in one file so the whole governance
posture reviews in a single diff. These tests pin the fail-closed contract
against an explicit small taxonomy; exactly one test reaches into crivo.detect
to confirm the lazy default.
"""

import json
import os
import subprocess
import sys

import pytest

from crivo.governance import (
    DEFAULT_GOVERNANCE,
    Governance,
    load_governance,
    save_governance,
)
from crivo.policy import PolicyRecord

TAXONOMY = frozenset({1, 2, 6, 14})


def _config(**over):
    base = {
        "policies": [
            {
                "id": "pol-001",
                "disease_ids": [1, 2],
                "approver": "aarmen",
                "expires": "2026-12-31",
                "mode": "LOG_ONLY",
            }
        ],
        "judge": {"enabled": True, "sample_rate": 0.2, "rubric": "answer-card"},
        "routing": {"escalate_after_fail": True},
    }
    base.update(over)
    return base


def _write(tmp_path, config):
    path = tmp_path / "governance.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_non_object_top_level_fails_closed_with_a_clear_message(tmp_path):
    """A hand-edited file that is a JSON list or scalar must raise a tailored
    ValueError, not a raw AttributeError (integration tightening, T2.5)."""
    path = tmp_path / "governance.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_governance(path, TAXONOMY)


def test_non_numeric_sample_rate_fails_closed(tmp_path):
    """sample_rate must be a number; a string or bool raises ValueError rather
    than passing a bogus comparison (integration tightening, T2.5)."""
    cfg = _config(judge={"enabled": True, "sample_rate": "high", "rubric": ""})
    with pytest.raises(ValueError, match="must be a number"):
        load_governance(_write(tmp_path, cfg), TAXONOMY)
    cfg = _config(judge={"enabled": True, "sample_rate": True, "rubric": ""})
    with pytest.raises(ValueError, match="must be a number"):
        load_governance(_write(tmp_path, cfg), TAXONOMY)


def test_load_reads_the_flat_config_into_live_objects(tmp_path):
    gov = load_governance(_write(tmp_path, _config()), TAXONOMY)
    assert isinstance(gov, Governance)
    (policy,) = gov.policies
    assert isinstance(policy, PolicyRecord)
    assert policy.id == "pol-001"
    assert policy.disease_ids == (1, 2)
    assert policy.mode == "LOG_ONLY"
    assert gov.judge == {"enabled": True, "sample_rate": 0.2, "rubric": "answer-card"}
    assert gov.routing == {"escalate_after_fail": True}


def test_missing_policies_key_is_an_empty_valid_governance(tmp_path):
    gov = load_governance(_write(tmp_path, {"judge": {"enabled": False}}), TAXONOMY)
    assert gov.policies == []
    # judge merges over the safe defaults; routing defaults in whole
    assert gov.judge["sample_rate"] == 0.0
    assert gov.routing == {"escalate_after_fail": True}


def test_empty_file_loads_to_the_default_posture(tmp_path):
    gov = load_governance(_write(tmp_path, {}), TAXONOMY)
    assert gov == DEFAULT_GOVERNANCE


def test_unknown_top_level_key_raises_naming_it(tmp_path):
    path = _write(tmp_path, _config() | {"memory": {"namespaces": []}})
    with pytest.raises(ValueError, match="memory"):
        load_governance(path, TAXONOMY)


def test_unknown_disease_id_raises_with_file_path_context(tmp_path):
    bad = _config()
    bad["policies"][0]["disease_ids"] = [1, 99]
    path = _write(tmp_path, bad)
    with pytest.raises(ValueError) as exc:
        load_governance(path, TAXONOMY)
    message = str(exc.value)
    assert "99" in message  # propagated from PolicyRecord admission
    assert str(path) in message  # file path added for context


@pytest.mark.parametrize("rate", [-0.1, 1.5])
def test_sample_rate_outside_unit_interval_raises(tmp_path, rate):
    cfg = _config()
    cfg["judge"]["sample_rate"] = rate
    with pytest.raises(ValueError, match="sample_rate"):
        load_governance(_write(tmp_path, cfg), TAXONOMY)


def test_load_is_all_or_nothing_on_a_bad_policy(tmp_path):
    # a good policy sits next to a bad one: the whole load fails, no partial
    cfg = _config()
    cfg["policies"].append(
        {
            "id": "pol-002",
            "disease_ids": [99],
            "approver": "aarmen",
            "expires": "2026-12-31",
            "mode": "LOG_ONLY",
        }
    )
    with pytest.raises(ValueError, match="99"):
        load_governance(_write(tmp_path, cfg), TAXONOMY)


def test_save_load_round_trips_the_data(tmp_path):
    original = load_governance(_write(tmp_path, _config()), TAXONOMY)
    out = tmp_path / "roundtrip.json"
    save_governance(original, out)
    assert load_governance(out, TAXONOMY) == original


def test_default_governance_is_a_safe_zero_config(tmp_path):
    assert DEFAULT_GOVERNANCE.policies == []
    assert DEFAULT_GOVERNANCE.judge["enabled"] is False
    assert DEFAULT_GOVERNANCE.routing["escalate_after_fail"] is True
    # the zero-config default survives a round trip like any loaded posture
    out = tmp_path / "default.json"
    save_governance(DEFAULT_GOVERNANCE, out)
    assert load_governance(out, TAXONOMY) == DEFAULT_GOVERNANCE


def test_lazy_default_valid_ids_come_from_detect_slugs(tmp_path, monkeypatch):
    # with no explicit set, load_governance imports crivo.detect.SLUGS lazily;
    # patching it proves the default taxonomy is exactly set(SLUGS)
    monkeypatch.setattr("crivo.detect.SLUGS", {1: "a", 2: "b"})
    ok = _config()
    ok["policies"][0]["disease_ids"] = [1, 2]
    gov = load_governance(_write(tmp_path, ok))
    assert gov.policies[0].disease_ids == (1, 2)

    bad = _config()
    bad["policies"][0]["disease_ids"] = [3]  # not in the patched SLUGS
    with pytest.raises(ValueError, match=r"disease ids \[3\]"):
        load_governance(_write(tmp_path, bad))


def test_module_imports_keyless_in_a_fresh_interpreter():
    # keyless import-safety: the module loads with every *_KEY var stripped,
    # needing no provider credential and no eager detect import for its surface
    env = {k: v for k, v in os.environ.items() if "KEY" not in k.upper()}
    code = (
        "from crivo.governance import "
        "DEFAULT_GOVERNANCE, load_governance, save_governance, Governance; "
        "assert DEFAULT_GOVERNANCE.routing['escalate_after_fail'] is True"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=env)
