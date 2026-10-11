"""
Unit tests for app/orchestrator/patch_verifier.py

Tests:
1. test_valid_patch_allowed_path — diff targeting pipeline.py → valid
2. test_patch_targeting_tests_rejected — diff with tests/ path → rejected
3. test_patch_targeting_holdouts_rejected — diff with holdout in path → rejected
4. test_patch_targeting_runner_rejected — diff with runner/ → rejected
5. test_unparseable_diff_inconclusive — garbage text → invalid_patch
6. test_good_patch_diff_passes_holdouts — explicit_float32 diff → passes
7. test_overfitted_diff_fails_holdouts — overfitted diff → fails
"""
from __future__ import annotations

import pytest

from app.orchestrator.patch_verifier import (
    ConstrainedPatchVerificationResult,
    ProposedPatch,
    apply_and_verify_patch,
    validate_patch_proposal,
)

# ---------------------------------------------------------------------------
# Sample diffs
# ---------------------------------------------------------------------------

_GOOD_DIFF = (
    "--- a/fixture/project/pipeline.py\n"
    "+++ b/fixture/project/pipeline.py\n"
    "@@ -28,7 +28,7 @@\n"
    "-    normalized = np.array([sample / 2.0 for sample in signal])\n"
    "+    normalized = np.array([sample / np.float32(2.0) for sample in signal])"
)

_OVERFITTED_DIFF = (
    "--- a/fixture/project/pipeline.py\n"
    "+++ b/fixture/project/pipeline.py\n"
    "@@ -25 +25 @@\n"
    "+    if len(signal) == 8 and abs(float(signal[7]) - 1.0) < 1e-5 and float(signal[7]) > 1.0:\n"
    "+        return 0.4999999  # hard-coded witness workaround\n"
)

_TESTS_DIFF = (
    "--- a/tests/test_pipeline.py\n"
    "+++ b/tests/test_pipeline.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new"
)

_HOLDOUT_DIFF = (
    "--- a/fixture/project/holdout_cases.py\n"
    "+++ b/fixture/project/holdout_cases.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new"
)

_RUNNER_DIFF = (
    "--- a/runner/worker.py\n"
    "+++ b/runner/worker.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new"
)

_GARBAGE_TEXT = "this is not a diff at all, just random text 12345"

_UNKNOWN_CHANGE_DIFF = (
    "--- a/fixture/project/pipeline.py\n"
    "+++ b/fixture/project/pipeline.py\n"
    "@@ -1 +1 @@\n"
    "-old_function()\n"
    "+new_function_that_is_unknown()"
)


# ---------------------------------------------------------------------------
# Test validate_patch_proposal
# ---------------------------------------------------------------------------

class TestValidatePatchProposal:
    def test_valid_patch_allowed_path(self):
        result = validate_patch_proposal(_GOOD_DIFF)
        assert result.valid is True
        assert result.reason is None
        assert "fixture/project/pipeline.py" in result.allowed_paths

    def test_patch_targeting_tests_rejected(self):
        result = validate_patch_proposal(_TESTS_DIFF)
        assert result.valid is False
        assert "tests/" in result.reason or "rejected_paths" in result.reason

    def test_patch_targeting_holdouts_rejected(self):
        result = validate_patch_proposal(_HOLDOUT_DIFF)
        assert result.valid is False

    def test_patch_targeting_runner_rejected(self):
        result = validate_patch_proposal(_RUNNER_DIFF)
        assert result.valid is False

    def test_unparseable_diff_invalid(self):
        result = validate_patch_proposal(_GARBAGE_TEXT)
        assert result.valid is False
        assert "not_a_diff" in result.reason or "no_allowed_paths" in result.reason

    def test_no_allowed_path_invalid(self):
        result = validate_patch_proposal("--- a/some/other/file.py\n+++ b/some/other/file.py\n@@ -1 +1 @@")
        assert result.valid is False

    def test_rejected_paths_populated(self):
        result = validate_patch_proposal(_TESTS_DIFF)
        assert len(result.rejected_paths) > 0


# ---------------------------------------------------------------------------
# Test apply_and_verify_patch
# ---------------------------------------------------------------------------

class TestApplyAndVerifyPatch:
    def test_unparseable_diff_returns_invalid_patch(self):
        proposal = ProposedPatch(
            diff_text=_GARBAGE_TEXT,
            description="garbage",
            rationale="test",
        )
        result = apply_and_verify_patch(proposal)
        assert result.verdict == "invalid_patch"

    def test_tests_path_rejected(self):
        proposal = ProposedPatch(
            diff_text=_TESTS_DIFF,
            description="bad",
            rationale="test",
        )
        result = apply_and_verify_patch(proposal)
        assert result.verdict == "invalid_patch"

    def test_unknown_change_is_inconclusive(self):
        proposal = ProposedPatch(
            diff_text=_UNKNOWN_CHANGE_DIFF,
            description="unknown",
            rationale="test",
        )
        result = apply_and_verify_patch(proposal)
        assert result.verdict == "inconclusive"

    def test_good_patch_diff_passes_holdouts(self):
        """explicit_float32 diff → should pass all holdouts."""
        proposal = ProposedPatch(
            diff_text=_GOOD_DIFF,
            description="Use np.float32 divisor",
            rationale="Fixes NEP-50 precision issue",
        )
        result = apply_and_verify_patch(proposal)
        assert isinstance(result, ConstrainedPatchVerificationResult)
        assert result.verdict == "repair_passes_stated_checks"
        assert len(result.holdout_results) > 0

    def test_overfitted_diff_fails_holdouts(self):
        """Overfitted diff → should fail some holdouts."""
        proposal = ProposedPatch(
            diff_text=_OVERFITTED_DIFF,
            description="Special-case hack",
            rationale="Overfitted to witness only",
        )
        result = apply_and_verify_patch(proposal)
        assert isinstance(result, ConstrainedPatchVerificationResult)
        assert result.verdict == "repair_fails_stated_checks"
        assert any(h.check_status == "fail" for h in result.holdout_results)

    def test_result_has_limitations(self):
        proposal = ProposedPatch(diff_text=_GOOD_DIFF, description="", rationale="")
        result = apply_and_verify_patch(proposal)
        assert len(result.limitations) >= 1

    def test_parse_result_embedded(self):
        proposal = ProposedPatch(diff_text=_GOOD_DIFF, description="", rationale="")
        result = apply_and_verify_patch(proposal)
        assert result.parse_result.valid is True

    def test_patch_checks_populated(self):
        proposal = ProposedPatch(diff_text=_GOOD_DIFF, description="", rationale="")
        result = apply_and_verify_patch(proposal)
        assert len(result.patch_checks) >= 2
        check_types = [c.check_type for c in result.patch_checks]
        assert "path_allowlist" in check_types
        assert "change_detection" in check_types


# ---------------------------------------------------------------------------
# Test ProposedPatch schema
# ---------------------------------------------------------------------------

class TestProposedPatch:
    def test_construction(self):
        p = ProposedPatch(diff_text="--- a/x\n+++ b/x\n@@ @@", description="d", rationale="r")
        assert p.diff_text.startswith("---")
        assert p.description == "d"
        assert p.rationale == "r"

    def test_immutable(self):
        from pydantic import ValidationError
        p = ProposedPatch(diff_text="d", description="d", rationale="r")
        with pytest.raises((AttributeError, ValidationError, TypeError)):
            p.diff_text = "something else"  # type: ignore[misc]
