"""Model transfer never includes private source or unsupported claims."""

import copy

import pytest

from app.domain.project_hypothesis import model_summary, summary_digest


def _job():
    return {
        "status": "completed",
        "spec_json": {"repository": "secret-owner/private-repo", "base_sha": "a" * 40},
        "result_json": {
            "kind": "approved_project_search", "changed_conclusion": True,
            "inconclusive": False, "attempted": 4,
            "witness_input": {"private_marker": 123},
            "source_sha256": "b" * 64,
            "witness": {
                "repeatable": True, "input_sha256": "c" * 64,
                "old": {"ratio": 196869.0, "conclusion": "above_threshold"},
                "old_repeat": {"ratio": 196869.00001, "conclusion": "above_threshold"},
                "new": {"ratio": 5.0, "conclusion": "below_threshold"},
                "new_repeat": {"ratio": 5.0, "conclusion": "below_threshold"},
            },
        },
    }


def test_transfer_summary_is_small_and_omits_private_identifiers():
    job = _job()
    summary = model_summary(job)
    encoded = str(summary)
    assert summary["tested_inputs"] == 4
    assert summary["mechanism_status"] == "untested"
    assert summary["old"]["measurement"] == 196869.0
    assert all(marker not in encoded for marker in
               ("private-repo", "private_marker", "source_sha256", "input_sha256", "aaaa"))
    assert summary_digest(summary) == summary_digest(model_summary(job))


@pytest.mark.parametrize("change", [
    {"status": "failed"},
    {"result_json": {"kind": "approved_project_search", "changed_conclusion": False}},
    {"result_json": {"kind": "approved_project_search", "changed_conclusion": True,
                     "inconclusive": True}},
])
def test_no_model_summary_without_repeatable_changed_evidence(change):
    job = _job()
    job.update(change)
    with pytest.raises(ValueError):
        model_summary(job)


def test_invalid_measurement_cannot_be_sent():
    job = copy.deepcopy(_job())
    job["result_json"]["witness"]["new"]["ratio"] = float("nan")
    with pytest.raises(ValueError, match="invalid"):
        model_summary(job)
