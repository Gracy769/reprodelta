"""Independent case comparison must keep denominators and controls honest."""

import json
from pathlib import Path

import pytest

from app.domain.project_profile import ProjectProfileDraft
from app.orchestrator.project_evaluation import evaluate_mask_searches

PROFILE = ProjectProfileDraft.model_validate_json(
    (Path(__file__).resolve().parents[2] / "evaluation" /
     "isce3-mask-project-profile.json").read_text()
)


def fake_batch(version, mode, inputs):
    values = []
    for item in inputs:
        result = 5
        if mode == "repaired" or version == "1.26.4":
            result |= item["reference"] << 16 | item["secondary"] << 8
        values.append([result, result])
    return {"numpy_version": version, "values": values}


def test_equal_budget_report_records_witnesses_and_unchanged_controls():
    report = evaluate_mask_searches(PROFILE, fake_batch, seeds=[13, 42], pair_budget=4)
    assert report["status"] == "completed"
    assert len(report["rows"]) == 16
    assert report["totals"] == {"completed_pairs": 64, "native_executions": 256,
                                "false_alarms": 0}
    assert all(row["completed_pairs"] == 4 and row["native_executions"] == 16
               for row in report["rows"])
    assert all(row["first_witness_pair"] is None for row in report["rows"]
               if row["case"] == "unchanged_cast_control")
    assert all(row["first_witness_pair"] is not None for row in report["rows"]
               if row["case"] == "reported_expression")
    assert [row["first_witness_pair"] for row in report["rows"]
            if row["case"] == "reported_expression" and row["strategy"] == "fixed"] == [3, 3]
    json.dumps(report, allow_nan=False)


def test_version_mismatch_cannot_be_counted_as_a_finding():
    def wrong_version(version, mode, inputs):
        result = fake_batch(version, mode, inputs)
        result["numpy_version"] = "0.0.0"
        return result

    with pytest.raises(RuntimeError, match="version mismatch"):
        evaluate_mask_searches(PROFILE, wrong_version, seeds=[13], pair_budget=1)


def test_unstable_repeats_do_not_become_negative_findings():
    def unstable(version, mode, inputs):
        result = fake_batch(version, mode, inputs)
        result["values"][0][1] += 1
        return result

    with pytest.raises(RuntimeError, match="not repeatable"):
        evaluate_mask_searches(PROFILE, unstable, seeds=[13], pair_budget=1)


def test_duplicate_seeds_and_invalid_budget_rejected():
    with pytest.raises(ValueError, match="distinct"):
        evaluate_mask_searches(PROFILE, fake_batch, seeds=[13, 13])
    with pytest.raises(ValueError, match="Pair budget"):
        evaluate_mask_searches(PROFILE, fake_batch, seeds=[13], pair_budget=5)
