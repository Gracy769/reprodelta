import json

import pytest

from app.domain.project_share import redacted_project_share


def test_redacted_share_omits_private_source_inputs_and_measurements():
    job = {
        "status": "completed",
        "spec_json": {"repository": "private-owner/secret-project", "base_sha": "a" * 40},
        "result_json": {
            "kind": "approved_project_search", "source_sha256": "secret-source-hash",
            "input_sha256": "secret-input-hash", "changed_conclusion": True,
            "witness_input": {"secret_private_input": 314159},
            "attempted": 2, "inconclusive": False, "sandbox_seconds": 2.5,
            "estimated_sandbox_cost_usd": 0.001,
            "observations": [{"input_sha256": "secret-input-hash", "old": {"ratio": 0.12345},
                              "new": {"ratio": 0.98765}, "repeatable": True,
                              "changed_conclusion": True}],
        },
    }
    copy = redacted_project_share(job)
    encoded = json.dumps(copy)
    assert copy["changed_conclusion"] is True
    assert copy["tested_inputs"] == 1
    for forbidden in ("private-owner", "secret-project", "secret-source-hash",
                      "secret-input-hash", "secret_private_input", "314159",
                      "0.12345", "0.98765"):
        assert forbidden not in encoded


def test_share_requires_completed_supported_result():
    with pytest.raises(ValueError, match="completed"):
        redacted_project_share({"status": "running"})
    with pytest.raises(ValueError, match="shareable"):
        redacted_project_share({"status": "completed", "result_json": {"kind": "other"}})


def test_single_sample_share_distinguishes_new_repeat_controls_from_history():
    current = redacted_project_share({
        "status": "completed", "result_json": {
            "kind": "approved_project_sample", "repeatable": False,
            "inconclusive": True, "changed_conclusion": False,
        },
    })
    historical = redacted_project_share({
        "status": "completed", "result_json": {
            "kind": "approved_project_sample", "changed_conclusion": True,
        },
    })
    assert current["repeatability_tested"] is True
    assert current["inconclusive"] is True
    assert historical["repeatability_tested"] is False
