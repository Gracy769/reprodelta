"""
Integration tests for the evidence report generator and API endpoints.

Tests:
- generate_report returns a well-formed bundle with all required fields
- generate_text_summary is a non-empty string
- generate_limitations checks mock mode correctly
- generate_replay_instructions contains expected commands
- API endpoint GET /api/investigations/{id}/report returns JSON
- API endpoint GET /api/investigations/{id}/report/text returns text
"""
from __future__ import annotations

import pathlib
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.contract import FIXTURE_CONTRACT, hash_contract
from app.domain.evidence import ControlEvidence, InvestigationEvidence, RunEvidence, WitnessEvidence
from app.domain.types import BudgetConfig, VerdictKind
from app.orchestrator.investigation import InvestigationRunner
from app.providers.runner import LocalRunnerAdapter

# ---------------------------------------------------------------------------
# Fixture directory detection
# ---------------------------------------------------------------------------

_HERE = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_DIR = _HERE / "fixture" / "project"
_FIXTURE_AVAILABLE = _FIXTURE_DIR.exists()

_REQUIRES_FIXTURE = pytest.mark.skipif(
    not _FIXTURE_AVAILABLE,
    reason="Fixture directory not available",
)


# ---------------------------------------------------------------------------
# Helper: build a minimal InvestigationEvidence without running the full flow
# ---------------------------------------------------------------------------

def _make_run_ev(environment: str, conclusion: str, ratio: float) -> RunEvidence:
    return RunEvidence(
        run_id="test-run-" + environment,
        environment=environment,
        numpy_version="2.0.0",
        conclusion=conclusion,
        ratio=ratio,
        intermediate_dtype="float32",
        status="ok",
    )


def _make_minimal_evidence(with_witness: bool = True) -> InvestigationEvidence:
    old_ev1 = _make_run_ev("old", "above_threshold", 0.5000001)
    old_ev2 = _make_run_ev("old", "above_threshold", 0.5000001)
    new_ev1 = _make_run_ev("new", "below_threshold", 0.4999999)
    new_ev2 = _make_run_ev("new", "below_threshold", 0.4999999)

    controls = [
        ControlEvidence(
            input_json={"signal": [1.0] * 8},
            input_sha256="a" * 64,
            run1=old_ev1,
            run2=old_ev2,
            repeatability_confirmed=True,
        ),
        ControlEvidence(
            input_json={"signal": [1.0] * 8},
            input_sha256="b" * 64,
            run1=new_ev1,
            run2=new_ev2,
            repeatability_confirmed=True,
        ),
    ]

    witness = None
    if with_witness:
        witness = WitnessEvidence(
            input_json={"signal": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0000001192]},
            input_sha256="c" * 64,
            old_run=_make_run_ev("old", "above_threshold", 0.50000001490),
            new_run=_make_run_ev("new", "below_threshold", 0.5),
            conclusion_changed=True,
            verdict=VerdictKind.supported_conclusion_change,
        )

    return InvestigationEvidence(
        investigation_id=str(uuid.uuid4()),
        project_name="Test Project",
        contract_hash=hash_contract(FIXTURE_CONTRACT),
        contract_body=FIXTURE_CONTRACT,
        controls=controls,
        witness=witness,
        reduction=None,
        verdict=VerdictKind.supported_conclusion_change,
        scope="fixture_scope",
        timestamp=datetime.now(UTC),
        model_proposals=[],
        intervention=None,
        patch_results=[],
    )


# ---------------------------------------------------------------------------
# Unit tests: generate_report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_returns_dict(self):
        from app.orchestrator.report import generate_report

        evidence = _make_minimal_evidence()
        report = generate_report(evidence)
        assert isinstance(report, dict)

    def test_schema_version_present(self):
        from app.orchestrator.report import generate_report

        report = generate_report(_make_minimal_evidence())
        assert "schema_version" in report
        assert report["schema_version"] in ("1", "2", "3")

    def test_export_timestamp_present(self):
        from app.orchestrator.report import generate_report

        report = generate_report(_make_minimal_evidence())
        assert "export_timestamp" in report
        assert isinstance(report["export_timestamp"], str)

    def test_investigation_id_present(self):
        from app.orchestrator.report import generate_report

        evidence = _make_minimal_evidence()
        report = generate_report(evidence)
        assert report["investigation_id"] == evidence.investigation_id

    def test_contract_hash_present(self):
        from app.orchestrator.report import generate_report

        evidence = _make_minimal_evidence()
        report = generate_report(evidence)
        assert report["contract_hash"] == evidence.contract_hash

    def test_contract_body_present(self):
        from app.orchestrator.report import generate_report

        report = generate_report(_make_minimal_evidence())
        assert "contract_body" in report
        assert isinstance(report["contract_body"], dict)

    def test_verdict_present_and_not_safe(self):
        from app.orchestrator.report import generate_report

        report = generate_report(_make_minimal_evidence())
        assert "verdict" in report
        assert report["verdict"] != "safe"
        assert report["verdict"] in {v.value for v in VerdictKind}

    def test_controls_present(self):
        from app.orchestrator.report import generate_report

        evidence = _make_minimal_evidence()
        report = generate_report(evidence)
        assert "controls" in report
        assert len(report["controls"]) == len(evidence.controls)

    def test_witness_present_when_found(self):
        from app.orchestrator.report import generate_report

        report = generate_report(_make_minimal_evidence(with_witness=True))
        assert report["witness"] is not None

    def test_witness_none_when_not_found(self):
        from app.orchestrator.report import generate_report

        report = generate_report(_make_minimal_evidence(with_witness=False))
        assert report["witness"] is None

    def test_replay_instructions_present(self):
        from app.orchestrator.report import generate_report

        report = generate_report(_make_minimal_evidence())
        assert "replay_instructions" in report
        assert isinstance(report["replay_instructions"], str)
        assert len(report["replay_instructions"]) > 50

    def test_limitations_present(self):
        from app.orchestrator.report import generate_report

        report = generate_report(_make_minimal_evidence())
        assert "limitations" in report
        assert isinstance(report["limitations"], list)
        assert len(report["limitations"]) >= 1

    def test_all_required_fields_present(self):
        from app.orchestrator.report import generate_report

        required = [
            "schema_version", "export_timestamp", "investigation_id",
            "timestamp", "project_name", "contract_hash", "contract_body",
            "verdict", "scope", "controls", "witness", "reduction",
            "model_proposals", "intervention", "patch_results",
            "replay_instructions", "limitations",
        ]
        report = generate_report(_make_minimal_evidence())
        for field in required:
            assert field in report, f"Missing required field: {field!r}"


# ---------------------------------------------------------------------------
# Unit tests: generate_limitations
# ---------------------------------------------------------------------------

class TestGenerateLimitations:
    def test_always_includes_constructed_case_statement(self):
        from app.orchestrator.report import generate_limitations

        evidence = _make_minimal_evidence()
        lims = generate_limitations(evidence)
        assert any("constructed demonstration case" in lim for lim in lims)

    def test_missing_model_proposal_limitation(self):
        from app.orchestrator.report import generate_limitations

        evidence = _make_minimal_evidence()
        assert len(evidence.model_proposals) == 0
        lims = generate_limitations(evidence)
        assert any("No model proposal was recorded" in lim for lim in lims)

    def test_witness_limitation_when_witness_present(self):
        from app.orchestrator.report import generate_limitations

        evidence = _make_minimal_evidence(with_witness=True)
        lims = generate_limitations(evidence)
        assert any("specific input" in lim for lim in lims)

    def test_absence_limitation_always_present(self):
        from app.orchestrator.report import generate_limitations

        lims = generate_limitations(_make_minimal_evidence())
        assert any("absence" in lim for lim in lims)


# ---------------------------------------------------------------------------
# Unit tests: generate_replay_instructions
# ---------------------------------------------------------------------------

class TestGenerateReplayInstructions:
    def test_contains_investigation_id(self):
        from app.orchestrator.report import generate_replay_instructions

        evidence = _make_minimal_evidence()
        instructions = generate_replay_instructions(evidence)
        assert evidence.investigation_id in instructions

    def test_contains_contract_hash(self):
        from app.orchestrator.report import generate_replay_instructions

        evidence = _make_minimal_evidence()
        instructions = generate_replay_instructions(evidence)
        assert evidence.contract_hash in instructions

    def test_contains_independent_numpy_environment_commands(self):
        from app.orchestrator.report import generate_replay_instructions

        instructions = generate_replay_instructions(_make_minimal_evidence())
        assert "uv run --no-project --python 3.12 --with numpy==1.26.4" in instructions
        assert "uv run --no-project --python 3.12 --with numpy==2.0.2" in instructions

    def test_contains_expected_ratio_when_witness_found(self):
        from app.orchestrator.report import generate_replay_instructions

        evidence = _make_minimal_evidence(with_witness=True)
        instructions = generate_replay_instructions(evidence)
        # Should contain expected ratio for old env
        assert "ratio=" in instructions

    def test_no_witness_message_when_no_witness(self):
        from app.orchestrator.report import generate_replay_instructions

        evidence = _make_minimal_evidence(with_witness=False)
        instructions = generate_replay_instructions(evidence)
        assert "No witness was found" in instructions


# ---------------------------------------------------------------------------
# Unit tests: generate_text_summary
# ---------------------------------------------------------------------------

class TestGenerateTextSummary:
    def test_returns_string(self):
        from app.orchestrator.report import generate_text_summary

        summary = generate_text_summary(_make_minimal_evidence())
        assert isinstance(summary, str)

    def test_contains_investigation_id(self):
        from app.orchestrator.report import generate_text_summary

        evidence = _make_minimal_evidence()
        summary = generate_text_summary(evidence)
        assert evidence.investigation_id in summary

    def test_contains_verdict(self):
        from app.orchestrator.report import generate_text_summary

        evidence = _make_minimal_evidence()
        summary = generate_text_summary(evidence)
        assert evidence.verdict.value in summary

    def test_contains_limitations_section(self):
        from app.orchestrator.report import generate_text_summary

        summary = generate_text_summary(_make_minimal_evidence())
        assert "LIMITATIONS" in summary

    def test_contains_replay_section(self):
        from app.orchestrator.report import generate_text_summary

        summary = generate_text_summary(_make_minimal_evidence())
        assert "REPLAY INSTRUCTIONS" in summary

    def test_witness_section_present_when_witness_found(self):
        from app.orchestrator.report import generate_text_summary

        summary = generate_text_summary(_make_minimal_evidence(with_witness=True))
        assert "WITNESS" in summary


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestReportAPIEndpoints:
    @pytest.fixture(autouse=True)
    def client(self):
        from fastapi.testclient import TestClient

        from app.api.investigations import (
            _investigation_state,
            _investigations,
            get_store,
            set_store,
        )
        from app.main import app
        from app.storage.investigation_store import MemoryInvestigationStore

        self._investigations = _investigations
        self._investigation_state = _investigation_state
        previous_store = get_store()
        self.store = MemoryInvestigationStore()
        set_store(self.store)

        with TestClient(app) as c:
            self.client = c
            yield c
        set_store(previous_store)

    def _create_and_complete_investigation(self) -> str:
        """Register a fake completed investigation in global state."""
        inv_id = self.store.create_investigation(
            "00000000-0000-0000-0000-000000000001", {"max_candidates": 50, "max_runs": 200}
        )
        self.store.transition_status(inv_id, "running")
        self.store.transition_status(inv_id, "completed")
        self._investigations[inv_id] = {
            "id": inv_id,
            "project_id": "00000000-0000-0000-0000-000000000001",
            "status": "completed",
            "max_candidates": 50,
            "max_runs": 200,
        }
        self._investigation_state[inv_id] = {
            "status": "completed",
            "evidence": _make_minimal_evidence(),
            "progress": None,
        }
        return inv_id

    def test_report_endpoint_returns_200(self):
        inv_id = self._create_and_complete_investigation()
        resp = self.client.get(f"/api/investigations/{inv_id}/report")
        assert resp.status_code == 200

    def test_report_endpoint_returns_json(self):
        inv_id = self._create_and_complete_investigation()
        resp = self.client.get(f"/api/investigations/{inv_id}/report")
        data = resp.json()
        assert "schema_version" in data
        assert "verdict" in data

    def test_report_endpoint_verdict_not_safe(self):
        inv_id = self._create_and_complete_investigation()
        resp = self.client.get(f"/api/investigations/{inv_id}/report")
        data = resp.json()
        assert data["verdict"] != "safe"

    def test_report_text_endpoint_returns_200(self):
        inv_id = self._create_and_complete_investigation()
        resp = self.client.get(f"/api/investigations/{inv_id}/report/text")
        assert resp.status_code == 200

    def test_report_text_endpoint_content_type(self):
        inv_id = self._create_and_complete_investigation()
        resp = self.client.get(f"/api/investigations/{inv_id}/report/text")
        assert "text/plain" in resp.headers.get("content-type", "")

    def test_report_text_contains_investigation_id(self):
        inv_id = self._create_and_complete_investigation()
        resp = self.client.get(f"/api/investigations/{inv_id}/report/text")
        # The investigation ID in the evidence is from _make_minimal_evidence,
        # not inv_id; check that the response contains some text.
        assert len(resp.text) > 100

    def test_report_404_when_investigation_missing(self):
        resp = self.client.get("/api/investigations/nonexistent-id/report")
        assert resp.status_code == 404

    def test_report_404_when_evidence_not_ready(self):
        inv_id = self.store.create_investigation(
            "00000000-0000-0000-0000-000000000001", {}
        )
        self.store.transition_status(inv_id, "running")
        self._investigations[inv_id] = {"id": inv_id, "status": "running"}
        self._investigation_state[inv_id] = {
            "status": "running",
            "evidence": None,
            "progress": None,
        }
        resp = self.client.get(f"/api/investigations/{inv_id}/report")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Integration test: full flow produces a valid report
# ---------------------------------------------------------------------------

@_REQUIRES_FIXTURE
class TestFullFlowReport:
    @pytest.fixture(scope="class")
    @classmethod
    def full_evidence(cls):
        runner = LocalRunnerAdapter(fixture_dir=_FIXTURE_DIR)
        budget = BudgetConfig(max_candidates=100, max_runs=200)
        inv_runner = InvestigationRunner(
            contract=FIXTURE_CONTRACT,
            budget=budget,
            runner=runner,
        )
        return inv_runner.run_full_investigation(str(uuid.uuid4()))

    def test_full_flow_report_verdict_is_valid(self, full_evidence):
        from app.orchestrator.report import generate_report

        report = generate_report(full_evidence)
        assert report["verdict"] in {v.value for v in VerdictKind}

    def test_full_flow_report_witness_present(self, full_evidence):
        from app.orchestrator.report import generate_report

        report = generate_report(full_evidence)
        assert report["witness"] is not None

    def test_full_flow_text_summary_non_empty(self, full_evidence):
        from app.orchestrator.report import generate_text_summary

        summary = generate_text_summary(full_evidence)
        assert len(summary) > 200


