"""
Investigation runner — orchestrates the full investigation sequence.

Steps:
1. Run controls
2. Run search → find witness
3. Run reduction → confirm minimal witness
4. Get model explanation (Nemotron call via NebiusClient)
5. Run intervention
6. Get model patch proposal (Nemotron call)
7. Seal holdouts
8. Verify overfitted patch → should FAIL
9. Verify good patch → should PASS
10. Assemble final InvestigationEvidence
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import UTC, datetime

from app.config import get_settings
from app.domain.comparison import evaluate_output
from app.domain.contract import ContractBody, hash_contract
from app.domain.evidence import (
    ControlEvidence,
    InvestigationEvidence,
    RunEvidence,
    WitnessEvidence,
)
from app.domain.types import BudgetConfig, VerdictKind
from app.orchestrator.budget import BudgetLedger, BudgetRunner
from app.orchestrator.executor import compare_outputs, run_control
from app.orchestrator.intervention import (
    FIXTURE_INTERVENTION,
    InterventionRunResult,
    run_intervention,
)
from app.orchestrator.reducer import ReductionResult, reduce_witness
from app.orchestrator.repair import (
    FIXTURE_HOLDOUTS,
    GOOD_PATCH,
    OVERFITTED_PATCH,
    VerificationResult,
    verify_patch,
)
from app.orchestrator.search import SearchController, SearchProgress
from app.providers.nebius import ModelProposal, NebiusClient, NebiusError
from app.providers.runner import RunnerAdapter, RunResult
from app.storage.investigation_store import InvestigationStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_input(input_json: dict) -> str:
    return hashlib.sha256(
        json.dumps(input_json, sort_keys=True).encode()
    ).hexdigest()


def _run_result_to_evidence(result: RunResult, environment: str, contract: ContractBody) -> RunEvidence:
    """Convert a RunResult into a RunEvidence summary."""
    outputs = result.outputs
    inner = outputs.get("result", outputs)
    try:
        evaluated = evaluate_output(outputs, contract) if result.status == "ok" else None
    except (ValueError, TypeError, OverflowError):
        evaluated = None
    return RunEvidence(
        run_id=result.run_id,
        environment=environment,
        numpy_version=result.numpy_version or (inner.get("numpy_version") if isinstance(inner, dict) else None),
        conclusion=evaluated["conclusion"] if evaluated else None,
        ratio=evaluated["ratio"] if evaluated else None,
        intermediate_dtype=inner.get("intermediate_dtype") if isinstance(inner, dict) else None,
        status=result.status if evaluated else "error",
    )


def _build_control_evidence(
    input_json: dict,
    env: str,
    contract: ContractBody,
    runner: RunnerAdapter,
    input_sha256: str,
) -> ControlEvidence:
    """Run the same input twice in *env* and build a ControlEvidence."""
    results = run_control(input_json, env, contract, runner, input_sha256, n=2)
    r1, r2 = results[0], results[1]
    ev1 = _run_result_to_evidence(r1, env, contract)
    ev2 = _run_result_to_evidence(r2, env, contract)
    comparison = compare_outputs(
        {"status": r1.status, "outputs": r1.outputs},
        {"status": r2.status, "outputs": r2.outputs}, contract,
    )
    repeatability = (
        comparison["conclusion_changed"] is False
        and comparison.get("numeric_equal") is True
        and (not contract.comparison.dtype_must_match or comparison.get("dtype_equal") is True)
    )
    return ControlEvidence(
        input_json=input_json,
        input_sha256=input_sha256,
        run1=ev1,
        run2=ev2,
        repeatability_confirmed=repeatability,
    )


# ---------------------------------------------------------------------------
# InvestigationRunner
# ---------------------------------------------------------------------------


class InvestigationRunner:
    """
    Runs the full investigation sequence for a single investigation ID.

    Parameters
    ----------
    contract:
        The approved contract governing input validity and the conclusion rule.
    budget:
        Execution budget (max_candidates etc.).
    runner:
        The RunnerAdapter to use for dispatching runs.
    nebius_client:
        Optional Nebius client for model proposals.  If ``None`` a new client
        is created using ``NEBIUS_API_KEY`` from the environment (which may be
        ``None``, putting the client in mock mode).
    store:
        Optional InvestigationStore for persisting events.  If ``None``,
        events are not persisted.
    """

    PROJECT_NAME = "Signal Classifier (NumPy 1.26 → 2.0 demo)"
    SCOPE = "fixture/project/pipeline.py — NumPy NEP-50 scalar promotion"

    # The "canonical" control input — equal halves, should be repeatable.
    _CONTROL_INPUT = {"signal": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]}

    def __init__(
        self,
        contract: ContractBody,
        budget: BudgetConfig,
        runner: RunnerAdapter,
        nebius_client: NebiusClient | None = None,
        store: InvestigationStore | None = None,
    ) -> None:
        self.contract = contract
        self.budget = budget
        self.runner = runner
        self._store = store
        settings = get_settings()
        self._nebius = nebius_client or NebiusClient(
            api_key=settings.nebius_api_key,
            model_id=settings.nebius_model_id,
            api_base=settings.nebius_base_url,
        )

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    def _emit(
        self,
        investigation_id: str,
        event_type: str,
        payload: dict | None = None,
    ) -> None:
        """Append an event to the store if one is configured."""
        if self._store is None:
            return
        self._store.append_event(
            investigation_id, actor="runner", event_type=event_type, payload=payload or {},
        )

    # ------------------------------------------------------------------
    # Async model calls
    # ------------------------------------------------------------------

    def _call_model_explanation(
        self,
        witness_summary: str,
        env_diff: str,
    ) -> ModelProposal:
        """Synchronously run the async propose_explanation call."""
        return asyncio.run(
            self._nebius.propose_explanation(witness_summary, env_diff)
        )

    def _call_model_patch(
        self,
        witness_summary: str,
        intervention_summary: str,
        allowed_paths: list[str],
    ) -> ModelProposal:
        """Synchronously run the async propose_patch call."""
        return asyncio.run(
            self._nebius.propose_patch(witness_summary, intervention_summary, allowed_paths)
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run_full_investigation(self, investigation_id: str) -> InvestigationEvidence:
        """
        Synchronously run the full investigation and return InvestigationEvidence.

        The method is not async — it is CPU/IO-bound (subprocess calls) and
        designed to be executed in a thread or wrapped with asyncio.to_thread()
        at the API layer.
        """
        ledger = BudgetLedger(self.budget, self._store, investigation_id)
        self.runner = BudgetRunner(self.runner, ledger)
        self._nebius.budget_ledger = ledger
        contract_hash = hash_contract(self.contract)
        control_sha256 = _hash_input(self._CONTROL_INPUT)

        self._emit(investigation_id, "investigation_started", {
            "contract_hash": contract_hash,
        })

        # ------------------------------------------------------------------
        # 1. Repeatability controls
        # ------------------------------------------------------------------
        old_control = _build_control_evidence(
            self._CONTROL_INPUT, "old", self.contract, self.runner, control_sha256
        )
        new_control = _build_control_evidence(
            self._CONTROL_INPUT, "new", self.contract, self.runner, control_sha256
        )
        controls = [old_control, new_control]
        if not all(control.repeatability_confirmed for control in controls):
            failed_envs = [
                c.run1.environment for c in controls if not c.repeatability_confirmed
            ]
            self._emit(investigation_id, "control_failed", {
                "reason": "repeatability_not_confirmed",
                "failed_envs": failed_envs,
            })
            control_failure = {"stage": "preflight", "error": "Repeatability control failed or returned invalid outputs"}
            self._emit(investigation_id, "stage_failed", control_failure)
            return InvestigationEvidence(
                investigation_id=investigation_id,
                project_name=self.PROJECT_NAME,
                contract_hash=contract_hash,
                contract_body=self.contract,
                controls=controls,
                witness=None,
                reduction=None,
                verdict=VerdictKind.setup_failure,
                scope=self.SCOPE,
                timestamp=datetime.now(UTC),
                model_proposals=[],
                intervention=None,
                patch_results=[],
                stage_failures=[control_failure],
            )

        self._emit(investigation_id, "control_completed", {
            "old_repeatability": old_control.repeatability_confirmed,
            "new_repeatability": new_control.repeatability_confirmed,
        })

        # The fixture holdouts are fixed before any model proposal is requested.
        sealed_holdouts = copy.deepcopy(FIXTURE_HOLDOUTS)
        self._emit(investigation_id, "holdouts_sealed", {
            "count": len(sealed_holdouts),
            "sha256": _hash_input({"holdouts": sealed_holdouts}),
        })

        # ------------------------------------------------------------------
        # 2. Search for witness
        # ------------------------------------------------------------------
        self._emit(investigation_id, "preflight_ok", {})

        searcher = SearchController(
            self.contract,
            self.budget,
            self.runner,
            on_progress=lambda event_type, payload: self._emit(investigation_id, event_type, payload),
        )
        search_settings = self._store.get_investigation(investigation_id).get("budget_json", {}) if self._store else {}
        progress: SearchProgress = searcher.run_search(
            investigation_id, seed=search_settings.get("search_seed", 42),
            search_strategy=search_settings.get("search_strategy", "sweep"),
        )

        witness_evidence: WitnessEvidence | None = None
        reduction: ReductionResult | None = None
        verdict: VerdictKind
        stage_failures: list[dict[str, str]] = []

        def record_failure(stage: str, error: Exception) -> None:
            failure = {"stage": stage, "error": str(error)}
            stage_failures.append(failure)
            self._emit(investigation_id, "stage_failed", failure)

        model_proposals: list[ModelProposal] = []
        intervention_result: InterventionRunResult | None = None
        patch_results: list[VerificationResult] = []
        model_patch_result = None

        if progress.witness is not None:
            cand = progress.witness
            pr = cand.pair_result
            assert pr is not None  # guaranteed when witness is set

            self._emit(investigation_id, "witness_found", {
                "input_sha256": cand.input_sha256,
                "old_conclusion": pr.comparison.get("old_conclusion"),
                "new_conclusion": pr.comparison.get("new_conclusion"),
            })

            old_run_ev = RunEvidence(
                run_id=pr.old_run.run_id,
                environment="old",
                numpy_version=pr.old_run.numpy_version,
                conclusion=pr.comparison.get("old_conclusion"),
                ratio=pr.comparison.get("old_ratio"),
                intermediate_dtype=_extract_dtype(pr.old_run.outputs),
                status=pr.old_run.status,
            )
            new_run_ev = RunEvidence(
                run_id=pr.new_run.run_id,
                environment="new",
                numpy_version=pr.new_run.numpy_version,
                conclusion=pr.comparison.get("new_conclusion"),
                ratio=pr.comparison.get("new_ratio"),
                intermediate_dtype=_extract_dtype(pr.new_run.outputs),
                status=pr.new_run.status,
            )

            witness_evidence = WitnessEvidence(
                input_json=cand.input_json,
                input_sha256=cand.input_sha256,
                old_run=old_run_ev,
                new_run=new_run_ev,
                conclusion_changed=True,
                verdict=VerdictKind.supported_conclusion_change,
            )

            # ------------------------------------------------------------------
            # 3. Reduction
            # ------------------------------------------------------------------
            try:
                reduction = reduce_witness(cand.input_json, self.contract, self.runner)
            except Exception as exc:  # noqa: BLE001
                record_failure("reduction", exc)
                raise
            self._emit(investigation_id, "reduction_completed", {
                "original_length": len(cand.input_json.get("signal", [])),
            })

            # ------------------------------------------------------------------
            # 4. Model explanation (Nemotron call)
            # ------------------------------------------------------------------
            witness_summary = (
                f"Signal: {cand.input_json}\n"
                f"Old conclusion: {pr.comparison.get('old_conclusion')}, "
                f"ratio={pr.comparison.get('old_ratio')}\n"
                f"New conclusion: {pr.comparison.get('new_conclusion')}, "
                f"ratio={pr.comparison.get('new_ratio')}\n"
                f"Old intermediate dtype: {old_run_ev.intermediate_dtype}\n"
                f"New intermediate dtype: {new_run_ev.intermediate_dtype}"
            )
            env_diff = (
                f"NumPy old: {old_run_ev.numpy_version or 'unknown'}\n"
                f"NumPy new: {new_run_ev.numpy_version or 'unknown'}\n"
                "Key change: NEP 50 scalar promotion (float32 / Python float → float32 in NumPy 2.0)"
            )
            try:
                explanation_proposal = self._call_model_explanation(
                    witness_summary, env_diff
                )
                model_proposals.append(explanation_proposal)
                self._emit(investigation_id, "model_explanation_received", {
                    "is_mock": explanation_proposal.is_mock,
                    "model_id": explanation_proposal.model_id,
                })
            except NebiusError as exc:
                record_failure("explanation", exc)
                self._emit(investigation_id, "model_call_failed", {
                    "call_type": "explanation",
                    "error": str(exc),
                })
            except Exception as exc:  # noqa: BLE001
                record_failure("explanation", exc)

            # ------------------------------------------------------------------
            # 5. Intervention
            # ------------------------------------------------------------------
            explanation_prop = model_proposals[0] if model_proposals else _stub_proposal()
            try:
                intervention_result = run_intervention(
                    witness_input=cand.input_json,
                    intervention=FIXTURE_INTERVENTION,
                    contract=self.contract,
                    runner=self.runner,
                    proposal=explanation_prop,
                )
            except Exception as exc:  # noqa: BLE001
                record_failure("intervention", exc)
            if intervention_result is not None:
                if intervention_result.assessment == "inconclusive":
                    record_failure("intervention", ValueError("Intervention output unavailable or invalid"))
                self._emit(investigation_id, "intervention_completed", {
                    "assessment": getattr(intervention_result, "assessment", None),
                    "prediction_supported": getattr(
                        intervention_result, "prediction_supported", None
                    ),
                })

            # ------------------------------------------------------------------
            # 6. Model patch proposal (Nemotron call)
            # ------------------------------------------------------------------
            intervention_summary = (
                f"Intervention: {FIXTURE_INTERVENTION.description}\n"
                f"Result: {intervention_result.assessment if intervention_result else 'not_run'}"
            )
            try:
                patch_proposal = self._call_model_patch(
                    witness_summary=witness_summary,
                    intervention_summary=intervention_summary,
                    allowed_paths=["fixture/project/pipeline.py"],
                )
                model_proposals.append(patch_proposal)
                self._emit(investigation_id, "model_patch_received", {
                    "is_mock": patch_proposal.is_mock,
                    "model_id": patch_proposal.model_id,
                })
                from app.orchestrator.patch_verifier import (
                    ProposedPatch,
                    apply_and_verify_patch,
                    extract_diff,
                )
                model_patch_result = apply_and_verify_patch(
                    ProposedPatch(diff_text=extract_diff(patch_proposal.response_text)),
                    sealed_holdouts, cand.input_json, self.contract, self.runner,
                )
                self._emit(investigation_id, "model_patch_verified", model_patch_result.model_dump(mode="json"))
                if model_patch_result.verdict in ("inconclusive", "invalid_patch"):
                    record_failure("model_repair", ValueError(model_patch_result.verdict))
            except NebiusError as exc:
                record_failure("patch_proposal", exc)
                self._emit(investigation_id, "model_call_failed", {
                    "call_type": "patch",
                    "error": str(exc),
                })
            except Exception as exc:  # noqa: BLE001
                record_failure("patch_proposal", exc)

            # ------------------------------------------------------------------
            # 7. Verify overfitted patch → should FAIL
            # ------------------------------------------------------------------
            try:
                overfitted_result = verify_patch(
                    OVERFITTED_PATCH, sealed_holdouts, self.contract, self.runner
                )
                if any(r.check_status == "not_run" for r in overfitted_result.holdout_results):
                    record_failure("overfitted_repair", ValueError("Some holdout runs failed or returned invalid outputs"))
                patch_results.append(overfitted_result)
                self._emit(investigation_id, "patch_verified", {
                    "kind": "overfitted",
                    "verdict": overfitted_result.verdict,
                    "num_passed": overfitted_result.num_passed,
                    "num_failed": overfitted_result.num_failed,
                })
            except Exception as exc:  # noqa: BLE001
                record_failure("overfitted_repair", exc)

            # ------------------------------------------------------------------
            # 9. Verify good patch → should PASS
            # ------------------------------------------------------------------
            try:
                good_result = verify_patch(
                    GOOD_PATCH, sealed_holdouts, self.contract, self.runner
                )
                if any(r.check_status == "not_run" for r in good_result.holdout_results):
                    record_failure("good_repair", ValueError("Some holdout runs failed or returned invalid outputs"))
                patch_results.append(good_result)
                self._emit(investigation_id, "patch_verified", {
                    "kind": "good",
                    "verdict": good_result.verdict,
                    "num_passed": good_result.num_passed,
                    "num_failed": good_result.num_failed,
                })
            except Exception as exc:  # noqa: BLE001
                record_failure("good_repair", exc)

            verdict = VerdictKind.supported_conclusion_change
        elif progress.status == "failed":
            verdict = VerdictKind.setup_failure
            record_failure("search", ValueError("A candidate execution or output validation failed"))
        elif progress.status == "exhausted":
            hints = {c.pair_result.comparison["verdict_hint"] for c in progress.candidates if c.pair_result}
            verdict = (VerdictKind.inconclusive if not hints or VerdictKind.inconclusive in hints
                       else VerdictKind.observed_drift if VerdictKind.observed_drift in hints
                       else VerdictKind.no_violation_found)
            self._emit(investigation_id, "witness_not_found", {
                "candidates_tried": progress.attempted,
            })
        else:
            verdict = VerdictKind.inconclusive
            self._emit(investigation_id, "witness_not_found", {
                "candidates_tried": progress.attempted,
                "reason": "budget_exhausted_or_inconclusive",
            })

        # ------------------------------------------------------------------
        # 10. Assemble final InvestigationEvidence
        # ------------------------------------------------------------------
        from app.orchestrator.benchmark import compare_searches
        demonstration_comparison = compare_searches(self.runner)
        self._emit(investigation_id, "demonstration_compared", {"status": demonstration_comparison["status"]})
        if demonstration_comparison["status"] != "completed":
            record_failure("demonstration", ValueError("Comparison stopped at the shared execution budget"))
        evidence = InvestigationEvidence(
            investigation_id=investigation_id,
            project_name=self.PROJECT_NAME,
            contract_hash=contract_hash,
            contract_body=self.contract,
            controls=controls,
            witness=witness_evidence,
            reduction=reduction,
            verdict=verdict,
            scope=self.SCOPE,
            timestamp=datetime.now(UTC),
            model_proposals=model_proposals,
            intervention=intervention_result,
            patch_results=patch_results,
            model_patch_result=model_patch_result,
            frozen_holdouts=sealed_holdouts,
            budget_usage=ledger.usage,
            demonstration_comparison=demonstration_comparison,
            stage_failures=stage_failures,
        )

        self._emit(investigation_id, "report_assembled", {
            "verdict": verdict.value,
        })
        self._emit(investigation_id, "investigation_completed", {
            "verdict": verdict.value,
        })

        return evidence


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_dtype(outputs: dict) -> str | None:
    inner = outputs.get("result", outputs)
    if isinstance(inner, dict):
        return inner.get("intermediate_dtype")
    return None


def _stub_proposal() -> ModelProposal:
    """Return a no-op proposal when no model proposals are available yet."""
    from datetime import datetime

    from app.providers.nebius import ModelProposal
    return ModelProposal(
        model_id="stub",
        prompt_template_version="none",
        request_digest="0" * 64,
        response_text="(not available)",
        is_mock=True,
        created_at=datetime.now(UTC),
    )
