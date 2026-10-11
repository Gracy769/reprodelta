"""
Evaluation fixtures test.

Per docs/testing.md "Deterministic demonstration fixture" requirements:
1. Unchanged controls don't produce a false alarm (no conclusion changes reported).
2. Invalid-domain inputs are all rejected — none can be witnesses.
3. Repeatability within environment — same input gives same output.
4. The real witness is found by the live search.
5. The overfitted patch fails on at least one holdout.
6. The good patch passes all holdouts.
7. After reduction, the witness is the minimal (reduced) form.
8. The generated report contains all required fields per the acceptance criteria.
"""
from __future__ import annotations

import pathlib
import uuid

import pytest

from app.domain.contract import FIXTURE_CONTRACT
from app.domain.evidence import InvestigationEvidence
from app.domain.types import BudgetConfig, VerdictKind
from app.domain.validator import validate_input
from app.orchestrator.executor import PairedRunRequest, run_pair
from app.orchestrator.investigation import InvestigationRunner
from app.orchestrator.repair import (
    FIXTURE_HOLDOUTS,
    GOOD_PATCH,
    OVERFITTED_PATCH,
    verify_patch,
)
from app.providers.runner import LocalRunnerAdapter

# ---------------------------------------------------------------------------
# Fixture directory detection
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
def runner() -> LocalRunnerAdapter:
    return LocalRunnerAdapter(fixture_dir=_FIXTURE_DIR)


@pytest.fixture(scope="module")
def investigation_evidence(runner: LocalRunnerAdapter) -> InvestigationEvidence:
    """Run the full investigation once and share the result across tests."""
    budget = BudgetConfig(max_candidates=100, max_runs=200)
    inv_runner = InvestigationRunner(
        contract=FIXTURE_CONTRACT,
        budget=budget,
        runner=runner,
    )
    return inv_runner.run_full_investigation(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# 1. Unchanged controls produce no false alarm
# ---------------------------------------------------------------------------

class TestUnchangedControlsNoFalseAlarm:
    """
    Inputs that are clearly away from the ULP boundary must not produce
    a conclusion change between old and new environments.
    """

    STABLE_INPUTS = [
        {"signal": [0.5, 0.5, 0.5, 0.5, 1.5, 1.5, 1.5, 1.5]},  # clearly above
        {"signal": [1.5, 1.5, 1.5, 1.5, 0.5, 0.5, 0.5, 0.5]},  # clearly below
        {"signal": [2.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0, 3.0]},  # clearly above
    ]

    def test_stable_inputs_no_conclusion_change(self, runner):
        """Stable inputs must never show a conclusion change."""
        inv_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        for inp in self.STABLE_INPUTS:
            validation = validate_input(FIXTURE_CONTRACT, inp)
            assert validation.valid, f"Test input is invalid: {inp}"

            import hashlib
            import json

            sha = hashlib.sha256(json.dumps(inp, sort_keys=True).encode()).hexdigest()
            req = PairedRunRequest(
                investigation_id=inv_id,
                candidate=inp,
                candidate_sha256=sha,
                contract=FIXTURE_CONTRACT,
            )
            result = run_pair(req, runner)
            assert result.old_conclusion == result.new_conclusion, (
                f"Stable input {inp} caused a conclusion change: "
                f"old={result.old_conclusion}, new={result.new_conclusion}"
            )


# ---------------------------------------------------------------------------
# 2. Invalid domain inputs are all rejected
# ---------------------------------------------------------------------------

class TestInvalidDomainRejected:
    """All out-of-domain inputs must be rejected by validate_input."""

    INVALID_INPUTS = [
        {"signal": [999.0] * 8},                        # out of range
        {"signal": [1.0] * 3},                           # wrong length
        {"signal": [1.0] * 16},                          # too long
        {"signal": []},                                  # empty
        {"signal": "not_a_list"},                        # wrong type
        {"signal": [float("inf")] * 8},                  # infinity
        {"wrong_key": [1.0] * 8},                        # missing 'signal'
    ]

    @pytest.mark.parametrize("bad_input", INVALID_INPUTS)
    def test_invalid_input_rejected(self, bad_input):
        """Each invalid input must be rejected."""
        result = validate_input(FIXTURE_CONTRACT, bad_input)
        assert not result.valid, (
            f"Expected invalid input to be rejected, but it was accepted: {bad_input}"
        )

    def test_no_invalid_input_can_be_witness(self, runner):
        """Invalid inputs must not reach the runner and cannot be witnesses."""
        for bad_input in self.INVALID_INPUTS:
            validation = validate_input(FIXTURE_CONTRACT, bad_input)
            assert not validation.valid, (
                f"Invalid input {bad_input!r} passed validation — cannot guarantee "
                "it won't become a false witness."
            )


# ---------------------------------------------------------------------------
# 3. Repeatability within environment
# ---------------------------------------------------------------------------

class TestRepeatabilityWithinEnvironment:
    """The same input must produce identical outputs when run multiple times."""

    REFERENCE_INPUT = {"signal": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]}

    def _run_once(self, runner: LocalRunnerAdapter, kind: str) -> tuple[str | None, float | None]:
        import hashlib
        import json

        from app.providers.runner import RunManifest, make_run_id

        sha = hashlib.sha256(
            json.dumps(self.REFERENCE_INPUT, sort_keys=True).encode()
        ).hexdigest()
        manifest = RunManifest(
            run_id=make_run_id(),
            kind=kind,  # type: ignore[arg-type]
            candidate_sha256=sha,
            entry_point="run.py",
            input_json=self.REFERENCE_INPUT,
        )
        result = runner.submit(manifest)
        inner = result.outputs.get("result", result.outputs)
        conclusion = inner.get("conclusion") if isinstance(inner, dict) else None
        ratio = float(inner["ratio"]) if isinstance(inner, dict) and "ratio" in inner else None
        return conclusion, ratio

    def test_old_env_repeatable_three_times(self, runner):
        """Old environment must give identical conclusions across 3 runs."""
        results = [self._run_once(runner, "old") for _ in range(3)]
        conclusions = [r[0] for r in results]
        assert len(set(conclusions)) == 1, (
            f"Old environment not repeatable: {conclusions}"
        )

    def test_new_env_repeatable_three_times(self, runner):
        """New environment must give identical conclusions across 3 runs."""
        results = [self._run_once(runner, "new") for _ in range(3)]
        conclusions = [r[0] for r in results]
        assert len(set(conclusions)) == 1, (
            f"New environment not repeatable: {conclusions}"
        )

    def test_old_env_ratios_identical(self, runner):
        """Old environment must give identical ratios across 3 runs."""
        results = [self._run_once(runner, "old") for _ in range(3)]
        ratios = [r[1] for r in results]
        assert len(set(ratios)) == 1, f"Old environment ratios not identical: {ratios}"

    def test_new_env_ratios_identical(self, runner):
        """New environment must give identical ratios across 3 runs."""
        results = [self._run_once(runner, "new") for _ in range(3)]
        ratios = [r[1] for r in results]
        assert len(set(ratios)) == 1, f"New environment ratios not identical: {ratios}"


# ---------------------------------------------------------------------------
# 4. The real witness is found
# ---------------------------------------------------------------------------

class TestRealWitnessIsFound:
    def test_witness_found(self, investigation_evidence):
        """The full investigation must find the known ULP witness."""
        assert investigation_evidence.witness is not None, (
            "No witness found — the search did not discover the known "
            "conclusion-changing input within the budget."
        )

    def test_witness_signal_has_ulp_element(self, investigation_evidence):
        """The known witness must have signal[7] ≈ 1.0000001192."""
        assert investigation_evidence.witness is not None
        sig = investigation_evidence.witness.input_json.get("signal", [])
        assert len(sig) == 8
        assert abs(float(sig[7]) - 1.0000001192092896) < 1e-12, (
            f"Unexpected witness signal[7]: {sig[7]!r}"
        )

    def test_witness_conclusions_differ(self, investigation_evidence):
        """Old and new conclusions must differ for the witness."""
        assert investigation_evidence.witness is not None
        w = investigation_evidence.witness
        assert w.old_run.conclusion != w.new_run.conclusion


# ---------------------------------------------------------------------------
# 5. The overfitted patch fails on at least one holdout
# ---------------------------------------------------------------------------

class TestOverfittedPatchRejected:
    def test_overfitted_patch_fails(self, runner):
        """
        The overfitted patch must fail on at least one holdout, proving
        the repair gate prevents narrowly over-fit fixes from passing.
        """
        result = verify_patch(OVERFITTED_PATCH, FIXTURE_HOLDOUTS, FIXTURE_CONTRACT, runner)
        assert not result.all_passed, (
            "The overfitted patch passed all holdouts — the repair gate is not working."
        )
        assert result.num_failed >= 1, (
            f"Expected at least 1 holdout failure, got {result.num_failed}."
        )

    def test_overfitted_patch_verdict(self, runner):
        """The overfitted patch verdict must be 'repair_fails_stated_checks'."""
        result = verify_patch(OVERFITTED_PATCH, FIXTURE_HOLDOUTS, FIXTURE_CONTRACT, runner)
        assert result.verdict == "repair_fails_stated_checks"


# ---------------------------------------------------------------------------
# 6. The good patch passes all holdouts
# ---------------------------------------------------------------------------

class TestGoodPatchPasses:
    def test_good_patch_passes_all_holdouts(self, runner):
        """The good patch must pass every holdout in the frozen set."""
        result = verify_patch(GOOD_PATCH, FIXTURE_HOLDOUTS, FIXTURE_CONTRACT, runner)
        assert result.all_passed, (
            f"Good patch failed holdouts: {result.details}"
        )

    def test_good_patch_verdict(self, runner):
        """The good patch verdict must be 'repair_passes_stated_checks'."""
        result = verify_patch(GOOD_PATCH, FIXTURE_HOLDOUTS, FIXTURE_CONTRACT, runner)
        assert result.verdict == "repair_passes_stated_checks"


# ---------------------------------------------------------------------------
# 7. Witness is reduced
# ---------------------------------------------------------------------------

class TestWitnessIsReduced:
    def test_reduction_is_present(self, investigation_evidence):
        """The investigation must include a reduction result."""
        assert investigation_evidence.reduction is not None

    def test_reduction_size_unchanged_or_smaller(self, investigation_evidence):
        """The reduced witness must not be larger than the original."""
        assert investigation_evidence.reduction is not None
        assert investigation_evidence.reduction.size_after <= investigation_evidence.reduction.size_before

    def test_reduction_preserves_conclusion_change(self, investigation_evidence):
        """The reduced witness must still be a valid conclusion-changing input."""
        assert investigation_evidence.reduction is not None
        final = investigation_evidence.reduction.final_witness
        v = validate_input(FIXTURE_CONTRACT, final)
        assert v.valid, f"Reduced witness is not valid: {v.reason}"


# ---------------------------------------------------------------------------
# 8. Report has required fields
# ---------------------------------------------------------------------------

class TestReportHasRequiredFields:
    REQUIRED_FIELDS = [
        "schema_version",
        "export_timestamp",
        "investigation_id",
        "timestamp",
        "project_name",
        "contract_hash",
        "contract_body",
        "verdict",
        "scope",
        "controls",
        "witness",
        "reduction",
        "model_proposals",
        "intervention",
        "patch_results",
        "replay_instructions",
        "limitations",
    ]

    def test_report_contains_all_required_fields(self, investigation_evidence):
        """The generated JSON report must contain all required fields."""
        from app.orchestrator.report import generate_report

        report = generate_report(investigation_evidence)
        for field in self.REQUIRED_FIELDS:
            assert field in report, f"Required field missing from report: {field!r}"

    def test_report_verdict_is_not_safe(self, investigation_evidence):
        """The report must not show a green 'SAFE' verdict — only exact verdict strings."""
        from app.orchestrator.report import generate_report

        report = generate_report(investigation_evidence)
        assert report["verdict"] != "safe", (
            "Report shows a 'safe' verdict — this is forbidden. Use exact VerdictKind strings."
        )
        # Must be one of the seven VerdictKind values
        assert report["verdict"] in {v.value for v in VerdictKind}, (
            f"Report verdict {report['verdict']!r} is not a valid VerdictKind."
        )

    def test_report_limitations_are_present(self, investigation_evidence):
        """The limitations list must have at least one entry."""
        from app.orchestrator.report import generate_report

        report = generate_report(investigation_evidence)
        assert len(report["limitations"]) >= 1

    def test_report_replay_instructions_contain_investigation_id(self, investigation_evidence):
        """The replay instructions must reference the investigation ID."""
        from app.orchestrator.report import generate_report

        report = generate_report(investigation_evidence)
        assert investigation_evidence.investigation_id in report["replay_instructions"]

    def test_text_summary_is_non_empty(self, investigation_evidence):
        """The text summary must be a non-empty string."""
        from app.orchestrator.report import generate_text_summary

        summary = generate_text_summary(investigation_evidence)
        assert isinstance(summary, str)
        assert len(summary) > 100
        assert investigation_evidence.investigation_id in summary
