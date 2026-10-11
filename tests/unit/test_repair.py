"""
Unit tests for app/orchestrator/repair.py

Tests cover:
- Overfitted patch FAILS verification on holdouts
- Good patch PASSES verification on holdouts
- Holdout inputs cannot be modified by the patch
- VerificationResult verdict is correctly set
"""
from __future__ import annotations

import copy
import pathlib

import pytest

from app.orchestrator.repair import (
    FIXTURE_HOLDOUTS,
    GOOD_PATCH,
    OVERFITTED_PATCH,
    HoldoutResult,
    PatchKind,
    VerificationResult,
    verify_patch,
)

# ---------------------------------------------------------------------------
# Fixture availability check
# ---------------------------------------------------------------------------

_HERE = pathlib.Path(__file__).parent.parent.parent  # repo root
_FIXTURE_DIR = _HERE / "fixture" / "project"
_FIXTURE_AVAILABLE = _FIXTURE_DIR.exists()

pytestmark = pytest.mark.skipif(
    not _FIXTURE_AVAILABLE,
    reason="Fixture directory not available in this environment",
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def local_runner():
    from app.providers.runner import LocalRunnerAdapter
    return LocalRunnerAdapter(fixture_dir=_FIXTURE_DIR)


@pytest.fixture(scope="module")
def contract():
    from app.domain.contract import FIXTURE_CONTRACT
    return FIXTURE_CONTRACT


# Use a fresh sealed copy so tests cannot affect each other
@pytest.fixture(scope="module")
def sealed_holdouts():
    return list(FIXTURE_HOLDOUTS)


# ---------------------------------------------------------------------------
# Good patch verification
# ---------------------------------------------------------------------------

class TestGoodPatch:
    @pytest.fixture(scope="class")
    @classmethod
    def good_result(cls, local_runner, contract, sealed_holdouts):
        return verify_patch(GOOD_PATCH, sealed_holdouts, contract, local_runner)

    def test_returns_verification_result(self, good_result):
        assert isinstance(good_result, VerificationResult)

    def test_patch_kind_is_good(self, good_result):
        assert good_result.patch_kind == PatchKind.GOOD

    def test_all_holdouts_pass(self, good_result):
        assert good_result.all_passed is True, (
            f"Expected all holdouts to pass, got verdict={good_result.verdict}, "
            f"details={good_result.details}, "
            f"num_failed={good_result.num_failed}"
        )

    def test_verdict_passes(self, good_result):
        assert good_result.verdict == "repair_passes_stated_checks"

    def test_num_passed_equals_holdout_count(self, good_result, sealed_holdouts):
        assert good_result.num_passed == len(sealed_holdouts)

    def test_num_failed_is_zero(self, good_result):
        assert good_result.num_failed == 0

    def test_has_holdout_results(self, good_result, sealed_holdouts):
        assert len(good_result.holdout_results) == len(sealed_holdouts)

    def test_each_holdout_result_has_sha256(self, good_result):
        for hr in good_result.holdout_results:
            assert len(hr.input_sha256) == 64
            assert all(c in "0123456789abcdef" for c in hr.input_sha256)


# ---------------------------------------------------------------------------
# Overfitted patch verification
# ---------------------------------------------------------------------------

class TestOverfittedPatch:
    @pytest.fixture(scope="class")
    @classmethod
    def overfitted_result(cls, local_runner, contract, sealed_holdouts):
        return verify_patch(OVERFITTED_PATCH, sealed_holdouts, contract, local_runner)

    def test_returns_verification_result(self, overfitted_result):
        assert isinstance(overfitted_result, VerificationResult)

    def test_patch_kind_is_overfitted(self, overfitted_result):
        assert overfitted_result.patch_kind == PatchKind.OVERFITTED

    def test_verdict_fails(self, overfitted_result):
        """Overfitted patch only handles witness, not holdouts with above_threshold."""
        assert overfitted_result.verdict == "repair_fails_stated_checks", (
            f"Expected 'repair_fails_stated_checks', got '{overfitted_result.verdict}'. "
            f"Details: {overfitted_result.details}"
        )

    def test_at_least_one_holdout_fails(self, overfitted_result):
        assert overfitted_result.num_failed > 0

    def test_not_all_passed(self, overfitted_result):
        assert overfitted_result.all_passed is False


# ---------------------------------------------------------------------------
# Holdout integrity
# ---------------------------------------------------------------------------

class TestHoldoutIntegrity:
    def test_holdout_inputs_not_modified_by_verify(self, local_runner, contract):
        """Calling verify_patch must not mutate the holdout list."""
        original = copy.deepcopy(FIXTURE_HOLDOUTS)
        sealed = list(FIXTURE_HOLDOUTS)
        verify_patch(GOOD_PATCH, sealed, contract, local_runner)
        # The original FIXTURE_HOLDOUTS constant must remain unchanged
        assert original == FIXTURE_HOLDOUTS

    def test_holdout_list_has_expected_entries(self):
        assert len(FIXTURE_HOLDOUTS) == 6

    def test_each_holdout_has_signal_key(self):
        for h in FIXTURE_HOLDOUTS:
            assert "signal" in h
            assert len(h["signal"]) == 8

    def test_holdout_results_have_correct_type(self, local_runner, contract, sealed_holdouts):
        result = verify_patch(GOOD_PATCH, sealed_holdouts, contract, local_runner)
        for hr in result.holdout_results:
            assert isinstance(hr, HoldoutResult)
            assert hr.check_status in ("pass", "fail", "not_run")


# ---------------------------------------------------------------------------
# VerificationResult verdict logic
# ---------------------------------------------------------------------------

class TestVerificationResultVerdict:
    def test_good_patch_verdict_value(self, local_runner, contract, sealed_holdouts):
        result = verify_patch(GOOD_PATCH, sealed_holdouts, contract, local_runner)
        assert result.verdict in (
            "repair_passes_stated_checks",
            "repair_fails_stated_checks",
            "inconclusive",
        )

    def test_overfitted_patch_verdict_value(self, local_runner, contract, sealed_holdouts):
        result = verify_patch(OVERFITTED_PATCH, sealed_holdouts, contract, local_runner)
        assert result.verdict in (
            "repair_passes_stated_checks",
            "repair_fails_stated_checks",
            "inconclusive",
        )
