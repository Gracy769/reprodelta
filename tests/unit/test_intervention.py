"""
Unit tests for app/orchestrator/intervention.py

Tests cover:
- Intervention with witness input: conclusions match after (prediction supported)
- Intervention is labeled "supported"
- InterventionManifest is correctly structured
"""
from __future__ import annotations

import pathlib

import pytest

from app.orchestrator.intervention import (
    FIXTURE_INTERVENTION,
    InterventionKind,
    InterventionRunResult,
    run_intervention,
)
from app.providers.nebius import NebiusClient

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
# Shared runner fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def local_runner():
    from app.providers.runner import LocalRunnerAdapter
    return LocalRunnerAdapter(fixture_dir=_FIXTURE_DIR)


@pytest.fixture(scope="module")
def contract():
    from app.domain.contract import FIXTURE_CONTRACT
    return FIXTURE_CONTRACT


@pytest.fixture(scope="module")
def stub_proposal():
    import asyncio
    client = NebiusClient(api_key=None)
    return asyncio.run(
        client.propose_explanation("test witness", "test env")
    )


# Known witness input — the ULP boundary input from the fixture
_WITNESS_INPUT = {"signal": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0000001192092896]}


# ---------------------------------------------------------------------------
# InterventionManifest structure
# ---------------------------------------------------------------------------

class TestInterventionManifest:
    def test_fixture_intervention_kind(self):
        assert FIXTURE_INTERVENTION.kind == InterventionKind.EXPLICIT_FLOAT32_DIVISOR

    def test_fixture_intervention_has_description(self):
        assert len(FIXTURE_INTERVENTION.description) > 10

    def test_fixture_intervention_has_predicted_effect(self):
        assert len(FIXTURE_INTERVENTION.predicted_effect) > 10

    def test_fixture_intervention_has_change_description(self):
        assert len(FIXTURE_INTERVENTION.change_description) > 5

    def test_intervention_manifest_frozen(self):
        """InterventionManifest must be immutable."""
        with pytest.raises((TypeError, Exception)):
            FIXTURE_INTERVENTION.description = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# run_intervention — with witness input
# ---------------------------------------------------------------------------

class TestRunIntervention:
    @pytest.fixture(scope="class")
    @classmethod
    def intervention_result(cls, local_runner, contract, stub_proposal):
        return run_intervention(
            witness_input=_WITNESS_INPUT,
            intervention=FIXTURE_INTERVENTION,
            contract=contract,
            runner=local_runner,
            proposal=stub_proposal,
        )

    def test_returns_intervention_run_result(self, intervention_result):
        assert isinstance(intervention_result, InterventionRunResult)

    def test_intervention_assessment_supported(self, intervention_result):
        """After applying explicit_float32 mode, both envs should agree."""
        assert intervention_result.assessment == "supported", (
            f"Expected 'supported', got '{intervention_result.assessment}'. "
            f"old={intervention_result.old_run.conclusion}, "
            f"new={intervention_result.new_run.conclusion}"
        )

    def test_prediction_supported(self, intervention_result):
        assert intervention_result.prediction_supported is True

    def test_conclusion_changed_is_false(self, intervention_result):
        """After intervention, conclusions should match (no longer different)."""
        assert intervention_result.conclusion_changed is False

    def test_both_runs_succeed(self, intervention_result):
        assert intervention_result.old_run.status == "ok"
        assert intervention_result.new_run.status == "ok"

    def test_both_conclusions_are_same(self, intervention_result):
        assert intervention_result.old_run.conclusion == intervention_result.new_run.conclusion

    def test_kind_matches_manifest(self, intervention_result):
        assert intervention_result.kind == InterventionKind.EXPLICIT_FLOAT32_DIVISOR


# ---------------------------------------------------------------------------
# Non-witness input: already consistent → intervention has no effect
# ---------------------------------------------------------------------------

class TestInterventionOnNonWitnessInput:
    """For inputs far from the boundary, both envs already agree. The
    intervention should keep them consistent (prediction still supported)."""

    _SAFE_INPUT = {"signal": [2.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0, 3.0]}

    def test_safe_input_still_consistent(self, local_runner, contract, stub_proposal):
        result = run_intervention(
            witness_input=self._SAFE_INPUT,
            intervention=FIXTURE_INTERVENTION,
            contract=contract,
            runner=local_runner,
            proposal=stub_proposal,
        )
        # Both envs should still agree
        assert result.old_run.conclusion == result.new_run.conclusion
        assert result.conclusion_changed is False
        assert result.assessment in ("supported", "inconclusive")
