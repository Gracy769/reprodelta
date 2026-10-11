"""
Unit tests for app/domain/evidence.py.

Tests construction of evidence types from mock data without any runner calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.contract import FIXTURE_CONTRACT, hash_contract
from app.domain.evidence import (
    ControlEvidence,
    InvestigationEvidence,
    RunEvidence,
    WitnessEvidence,
)
from app.domain.types import VerdictKind
from app.orchestrator.reducer import ReductionResult, ReductionStep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run_evidence(env: str, conclusion: str = "above_threshold") -> RunEvidence:
    return RunEvidence(
        run_id=f"run-{env}-001",
        environment=env,
        numpy_version="1.26.4" if env == "old" else "2.0.0",
        conclusion=conclusion,
        ratio=0.5001 if conclusion == "above_threshold" else 0.4999,
        intermediate_dtype="float64" if env == "old" else "float32",
        status="ok",
    )


def _make_witness_evidence(old_conclusion: str, new_conclusion: str) -> WitnessEvidence:
    changed = old_conclusion != new_conclusion
    return WitnessEvidence(
        input_json={"signal": [1.0] * 7 + [1.0000001192092896]},
        input_sha256="a" * 64,
        old_run=_make_run_evidence("old", old_conclusion),
        new_run=_make_run_evidence("new", new_conclusion),
        conclusion_changed=changed,
        verdict=VerdictKind.supported_conclusion_change if changed else VerdictKind.no_violation_found,
    )


def _make_control_evidence(env: str, repeatable: bool = True) -> ControlEvidence:
    conclusion = "above_threshold"
    return ControlEvidence(
        input_json={"signal": [1.0] * 8},
        input_sha256="b" * 64,
        run1=_make_run_evidence(env, conclusion),
        run2=_make_run_evidence(env, conclusion if repeatable else "below_threshold"),
        repeatability_confirmed=repeatable,
    )


def _make_reduction_result() -> ReductionResult:
    return ReductionResult(
        final_witness={"signal": [1.0] * 7 + [1.0000001192092896]},
        final_sha256="c" * 64,
        steps=[
            ReductionStep(
                step_id=0,
                original_input={"signal": [1.0] * 8},
                reduced_input={"signal": [1.0] * 8},
                reduction_type="neutral_replace",
                accepted=True,
                reason="already neutral",
            )
        ],
        size_before=8,
        size_after=8,
    )


# ---------------------------------------------------------------------------
# RunEvidence
# ---------------------------------------------------------------------------

class TestRunEvidence:
    def test_construction(self):
        ev = _make_run_evidence("old")
        assert ev.environment == "old"
        assert ev.numpy_version == "1.26.4"
        assert ev.status == "ok"

    def test_frozen(self):
        import pydantic
        ev = _make_run_evidence("old")
        with pytest.raises((TypeError, AttributeError, pydantic.ValidationError)):
            ev.status = "error"  # type: ignore[misc]

    def test_conclusion_values(self):
        above = _make_run_evidence("old", "above_threshold")
        below = _make_run_evidence("new", "below_threshold")
        assert above.conclusion == "above_threshold"
        assert below.conclusion == "below_threshold"


# ---------------------------------------------------------------------------
# WitnessEvidence
# ---------------------------------------------------------------------------

class TestWitnessEvidence:
    def test_construction_with_change(self):
        w = _make_witness_evidence("above_threshold", "below_threshold")
        assert w.conclusion_changed is True
        assert w.verdict == VerdictKind.supported_conclusion_change

    def test_construction_without_change(self):
        w = _make_witness_evidence("above_threshold", "above_threshold")
        assert w.conclusion_changed is False
        assert w.verdict == VerdictKind.no_violation_found

    def test_input_sha256_is_stored(self):
        w = _make_witness_evidence("above_threshold", "below_threshold")
        assert w.input_sha256 == "a" * 64

    def test_frozen(self):
        import pydantic
        w = _make_witness_evidence("above_threshold", "below_threshold")
        with pytest.raises((TypeError, AttributeError, pydantic.ValidationError)):
            w.conclusion_changed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ControlEvidence
# ---------------------------------------------------------------------------

class TestControlEvidence:
    def test_repeatable_control(self):
        ctrl = _make_control_evidence("old", repeatable=True)
        assert ctrl.repeatability_confirmed is True

    def test_non_repeatable_control(self):
        ctrl = _make_control_evidence("new", repeatable=False)
        assert ctrl.repeatability_confirmed is False

    def test_both_runs_present(self):
        ctrl = _make_control_evidence("old")
        assert ctrl.run1.environment == "old"
        assert ctrl.run2.environment == "old"


# ---------------------------------------------------------------------------
# InvestigationEvidence
# ---------------------------------------------------------------------------

class TestInvestigationEvidence:
    def _build(
        self,
        verdict: VerdictKind = VerdictKind.supported_conclusion_change,
        with_witness: bool = True,
        with_reduction: bool = True,
    ) -> InvestigationEvidence:
        return InvestigationEvidence(
            investigation_id="test-inv-001",
            project_name="Test Project",
            contract_hash=hash_contract(FIXTURE_CONTRACT),
            contract_body=FIXTURE_CONTRACT,
            controls=[
                _make_control_evidence("old"),
                _make_control_evidence("new"),
            ],
            witness=_make_witness_evidence("above_threshold", "below_threshold") if with_witness else None,
            reduction=_make_reduction_result() if with_reduction else None,
            verdict=verdict,
            scope="fixture/project/pipeline.py",
            timestamp=datetime.now(UTC),
        )

    def test_construction(self):
        ev = self._build()
        assert ev.investigation_id == "test-inv-001"
        assert ev.verdict == VerdictKind.supported_conclusion_change

    def test_witness_present(self):
        ev = self._build(with_witness=True)
        assert ev.witness is not None
        assert ev.witness.conclusion_changed is True

    def test_witness_absent(self):
        ev = self._build(with_witness=False, with_reduction=False)
        assert ev.witness is None

    def test_controls_list(self):
        ev = self._build()
        assert len(ev.controls) == 2
        envs = {ctrl.run1.environment for ctrl in ev.controls}
        assert "old" in envs
        assert "new" in envs

    def test_verdict_no_violation(self):
        ev = self._build(
            verdict=VerdictKind.no_violation_found,
            with_witness=False,
            with_reduction=False,
        )
        assert ev.verdict == VerdictKind.no_violation_found

    def test_verdict_set_from_witness(self):
        """Verdict should be supported_conclusion_change when witness is present."""
        ev = self._build(
            verdict=VerdictKind.supported_conclusion_change,
            with_witness=True,
        )
        assert ev.verdict == VerdictKind.supported_conclusion_change

    def test_contract_hash_is_hex(self):
        ev = self._build()
        assert len(ev.contract_hash) == 64
        assert all(c in "0123456789abcdef" for c in ev.contract_hash)

    def test_timestamp_is_datetime(self):
        ev = self._build()
        assert isinstance(ev.timestamp, datetime)

    def test_reduction_result_stored(self):
        ev = self._build(with_reduction=True)
        assert ev.reduction is not None
        assert len(ev.reduction.steps) >= 1

    def test_serialise_to_json(self):
        """model_dump(mode='json') must not raise."""
        ev = self._build()
        data = ev.model_dump(mode="json")
        assert data["investigation_id"] == "test-inv-001"
        assert data["verdict"] == "supported_conclusion_change"
        assert data["witness"] is not None
