"""Unit tests for app/orchestrator/executor.py."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.contract import FIXTURE_CONTRACT
from app.domain.types import VerdictKind
from app.orchestrator.executor import (
    PairedRunRequest,
    PairedRunResult,
    SingleRunRecord,
    compare_outputs,
    run_pair,
)
from app.providers.runner import RunManifest, RunnerAdapter, RunResult

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _ok_old_out(conclusion: str = "above_threshold", ratio: float | None = None) -> dict:
    return {
        "status": "ok",
        "outputs": {
            "result": {
                "conclusion": conclusion,
                "ratio": (0.6 if conclusion == "above_threshold" else 0.4) if ratio is None else ratio,
                "numpy_version": "1.26.4",
                "intermediate_dtype": "float64",
            }
        },
    }


def _ok_new_out(conclusion: str = "above_threshold", ratio: float | None = None) -> dict:
    return {
        "status": "ok",
        "outputs": {
            "result": {
                "conclusion": conclusion,
                "ratio": (0.6 if conclusion == "above_threshold" else 0.4) if ratio is None else ratio,
                "numpy_version": "2.0.0",
                "intermediate_dtype": "float32",
            }
        },
    }


def _error_out() -> dict:
    return {"status": "error", "outputs": {}}


class StubRunner(RunnerAdapter):
    """Returns pre-configured results for old and new manifests."""

    def __init__(self, old_result: RunResult, new_result: RunResult) -> None:
        self._old = old_result
        self._new = new_result

    def submit(self, manifest: RunManifest) -> RunResult:
        return self._old if manifest.kind == "old" else self._new


def _make_run_result(kind: str, status: str = "ok", conclusion: str = "above_threshold") -> RunResult:
    outputs = {
        "result": {
            "conclusion": conclusion,
            "ratio": 0.6 if conclusion == "above_threshold" else 0.4,
            "numpy_version": "1.26.4" if kind == "old" else "2.0.0",
        }
    } if status == "ok" else {}
    return RunResult(
        run_id=f"test-{kind}-001",
        status=status,  # type: ignore[arg-type]
        exit_code=0 if status == "ok" else 1,
        outputs=outputs,
        stderr="",
    )


# ---------------------------------------------------------------------------
# compare_outputs
# ---------------------------------------------------------------------------

class TestCompareOutputs:
    def test_same_conclusion_no_violation(self):
        old = _ok_old_out("above_threshold")
        new = _ok_new_out("above_threshold")
        cmp = compare_outputs(old, new, FIXTURE_CONTRACT)
        assert cmp["conclusion_changed"] is False
        assert cmp["verdict_hint"] == VerdictKind.no_violation_found

    def test_different_conclusions_supported_change(self):
        old = _ok_old_out("above_threshold")
        new = _ok_new_out("below_threshold")
        cmp = compare_outputs(old, new, FIXTURE_CONTRACT)
        assert cmp["conclusion_changed"] is True
        assert cmp["verdict_hint"] == VerdictKind.supported_conclusion_change

    def test_old_error_setup_failure(self):
        cmp = compare_outputs(_error_out(), _ok_new_out(), FIXTURE_CONTRACT)
        assert cmp["verdict_hint"] == VerdictKind.setup_failure
        assert cmp["conclusion_changed"] is None

    def test_new_error_setup_failure(self):
        cmp = compare_outputs(_ok_old_out(), _error_out(), FIXTURE_CONTRACT)
        assert cmp["verdict_hint"] == VerdictKind.setup_failure

    def test_both_error_setup_failure(self):
        cmp = compare_outputs(_error_out(), _error_out(), FIXTURE_CONTRACT)
        assert cmp["verdict_hint"] == VerdictKind.setup_failure

    def test_ok_conclusions_extracted(self):
        old = _ok_old_out("above_threshold", ratio=0.7)
        new = _ok_new_out("below_threshold", ratio=0.49)
        cmp = compare_outputs(old, new, FIXTURE_CONTRACT)
        assert cmp["old_conclusion"] == "above_threshold"
        assert cmp["new_conclusion"] == "below_threshold"
        assert abs(cmp["old_ratio"] - 0.7) < 1e-9
        assert abs(cmp["new_ratio"] - 0.49) < 1e-9

    def test_error_statuses_recorded(self):
        cmp = compare_outputs(_error_out(), _ok_new_out(), FIXTURE_CONTRACT)
        assert cmp["old_status"] == "error"
        assert cmp["new_status"] == "ok"


# ---------------------------------------------------------------------------
# PairedRunResult structure
# ---------------------------------------------------------------------------

class TestPairedRunResult:
    def _build(self) -> PairedRunResult:
        now = datetime.now(UTC)
        old_rec = SingleRunRecord(
            environment="old",
            status="ok",
            outputs={"result": {"conclusion": "above_threshold", "ratio": 0.6}},
            stderr="",
            run_id="old-001",
            started_at=now,
            ended_at=now,
            numpy_version="1.26.4",
        )
        new_rec = SingleRunRecord(
            environment="new",
            status="ok",
            outputs={"result": {"conclusion": "above_threshold", "ratio": 0.6}},
            stderr="",
            run_id="new-001",
            started_at=now,
            ended_at=now,
            numpy_version="2.0.0",
        )
        return PairedRunResult(
            candidate_sha256="a" * 64,
            old_run=old_rec,
            new_run=new_rec,
            comparison={"conclusion_changed": False, "verdict_hint": VerdictKind.no_violation_found},
        )

    def test_structure(self):
        result = self._build()
        assert result.candidate_sha256 == "a" * 64
        assert result.old_run.environment == "old"
        assert result.new_run.environment == "new"
        assert result.comparison["conclusion_changed"] is False

    def test_frozen(self):
        import pydantic
        result = self._build()
        with pytest.raises((TypeError, AttributeError, pydantic.ValidationError)):
            result.candidate_sha256 = "b" * 64  # type: ignore[misc]


# ---------------------------------------------------------------------------
# run_pair with stub runner
# ---------------------------------------------------------------------------

class TestRunPair:
    def _request(self) -> PairedRunRequest:
        return PairedRunRequest(
            investigation_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
            candidate={"signal": [1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5]},
            candidate_sha256="a" * 64,
            contract=FIXTURE_CONTRACT,
        )

    def test_run_pair_same_conclusion(self):
        old_r = _make_run_result("old", "ok", "above_threshold")
        new_r = _make_run_result("new", "ok", "above_threshold")
        runner = StubRunner(old_r, new_r)
        result = run_pair(self._request(), runner)
        assert result.comparison["conclusion_changed"] is False
        assert result.comparison["verdict_hint"] == VerdictKind.no_violation_found

    def test_run_pair_different_conclusion(self):
        old_r = _make_run_result("old", "ok", "above_threshold")
        new_r = _make_run_result("new", "ok", "below_threshold")
        runner = StubRunner(old_r, new_r)
        result = run_pair(self._request(), runner)
        assert result.comparison["conclusion_changed"] is True
        assert result.comparison["verdict_hint"] == VerdictKind.supported_conclusion_change

    def test_run_pair_error_run(self):
        old_r = _make_run_result("old", "error")
        new_r = _make_run_result("new", "ok", "above_threshold")
        runner = StubRunner(old_r, new_r)
        result = run_pair(self._request(), runner)
        assert result.comparison["verdict_hint"] == VerdictKind.setup_failure

    def test_run_pair_returns_candidate_sha256(self):
        old_r = _make_run_result("old")
        new_r = _make_run_result("new")
        runner = StubRunner(old_r, new_r)
        result = run_pair(self._request(), runner)
        assert result.candidate_sha256 == "a" * 64
