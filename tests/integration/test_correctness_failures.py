"""Failures after successful preflight must remain visible in evidence."""
import pytest

from app.domain.contract import FIXTURE_CONTRACT
from app.domain.types import BudgetConfig, VerdictKind
from app.orchestrator.investigation import InvestigationRunner
from app.orchestrator.report import generate_report, generate_text_summary
from app.providers.runner import LocalRunnerAdapter, RunResult
from app.storage.investigation_store import MemoryInvestigationStore


class IncompleteRunner(LocalRunnerAdapter):
    def __init__(self, after_controls=False):
        super().__init__()
        self.calls = 0
        self.after_controls = after_controls

    def submit(self, manifest):
        self.calls += 1
        if self.after_controls and self.calls <= 4:
            return super().submit(manifest)
        return RunResult(run_id=manifest.run_id, status="ok", exit_code=0, outputs={"result": {}})


@pytest.mark.parametrize("after_controls", [False, True])
def test_incomplete_runs_never_produce_no_violation_found(after_controls):
    evidence = InvestigationRunner(FIXTURE_CONTRACT, BudgetConfig(max_candidates=5, max_runs=30), IncompleteRunner(after_controls)).run_full_investigation("audit")
    assert evidence.verdict == VerdictKind.setup_failure
    assert evidence.witness is None
    if after_controls:
        assert evidence.stage_failures[0]["stage"] == "search"


def test_failed_intervention_is_recorded_and_exported(monkeypatch):
    from app.orchestrator import investigation

    def fail(**kwargs):
        raise RuntimeError("test intervention worker unavailable")

    monkeypatch.setattr(investigation, "run_intervention", fail)
    store = MemoryInvestigationStore()
    inv_id = store.create_investigation("fixture", {}, "user")
    evidence = InvestigationRunner(FIXTURE_CONTRACT, BudgetConfig(max_candidates=10, max_runs=100), LocalRunnerAdapter(), store=store).run_full_investigation(inv_id)
    assert evidence.witness is not None
    assert evidence.intervention is None
    assert {"stage": "intervention", "error": "test intervention worker unavailable"} in evidence.stage_failures
    assert any(e["event_type"] == "stage_failed" for e in store.get_events(inv_id))
    report = generate_report(evidence)
    assert report["stage_failures"] == evidence.stage_failures
    assert any("intervention" in limitation for limitation in report["limitations"])
    assert "intervention worker unavailable" in generate_text_summary(evidence)


def test_failed_repair_checks_are_not_silently_discarded(monkeypatch):
    from app.orchestrator import investigation

    def fail(*args):
        raise RuntimeError("test holdout runner unavailable")

    monkeypatch.setattr(investigation, "verify_patch", fail)
    evidence = InvestigationRunner(FIXTURE_CONTRACT, BudgetConfig(max_candidates=10, max_runs=100), LocalRunnerAdapter()).run_full_investigation("audit")
    assert evidence.patch_results == []
    assert {f["stage"] for f in evidence.stage_failures} == {"overfitted_repair", "good_repair"}


def test_missing_intervention_outputs_cannot_support_a_prediction():
    from app.orchestrator.intervention import FIXTURE_INTERVENTION, run_intervention

    result = run_intervention({"signal": [1.] * 8}, FIXTURE_INTERVENTION, FIXTURE_CONTRACT, IncompleteRunner(), None)
    assert result.assessment == "inconclusive"
    assert result.prediction_supported is False


def test_missing_holdout_outputs_cannot_pass_repairs():
    from app.orchestrator.repair import FIXTURE_HOLDOUTS, GOOD_PATCH, verify_patch

    result = verify_patch(GOOD_PATCH, FIXTURE_HOLDOUTS, FIXTURE_CONTRACT, IncompleteRunner())
    assert result.verdict == "inconclusive"
    assert result.all_passed is False
