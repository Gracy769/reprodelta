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
import contextlib
import hashlib
import json
import os
from datetime import UTC, datetime

from app.domain.contract import ContractBody, hash_contract
from app.domain.evidence import (
    ControlEvidence,
    InvestigationEvidence,
    RunEvidence,
    WitnessEvidence,
)
from app.domain.types import BudgetConfig, VerdictKind
from app.orchestrator.executor import run_control
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
from app.providers.nebius import ModelProposal, NebiusClient
from app.providers.runner import RunnerAdapter, RunResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_input(input_json: dict) -> str:
    return hashlib.sha256(
        json.dumps(input_json, sort_keys=True).encode()
    ).hexdigest()


def _run_result_to_evidence(result: RunResult, environment: str) -> RunEvidence:
    """Convert a RunResult into a RunEvidence summary."""
    outputs = result.outputs
    inner = outputs.get("result", outputs)
    return RunEvidence(
        run_id=result.run_id,
        environment=environment,
        numpy_version=inner.get("numpy_version") if isinstance(inner, dict) else None,
        conclusion=inner.get("conclusion") if isinstance(inner, dict) else None,
        ratio=float(inner["ratio"]) if isinstance(inner, dict) and "ratio" in inner else None,
        intermediate_dtype=inner.get("intermediate_dtype") if isinstance(inner, dict) else None,
        status=result.status,
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
    ev1 = _run_result_to_evidence(r1, env)
    ev2 = _run_result_to_evidence(r2, env)
    # Repeatability: both runs must succeed and agree on conclusion
    repeatability = (
        r1.status == "ok"
        and r2.status == "ok"
        and ev1.conclusion == ev2.conclusion
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
    ) -> None:
        self.contract = contract
        self.budget = budget
        self.runner = runner
        self._nebius = nebius_client or NebiusClient(
            api_key=os.environ.get("NEBIUS_API_KEY"),
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
        contract_hash = hash_contract(self.contract)
        control_sha256 = _hash_input(self._CONTROL_INPUT)

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

        # ------------------------------------------------------------------
        # 2. Search for witness
        # ------------------------------------------------------------------
        searcher = SearchController(self.contract, self.budget, self.runner)
        progress: SearchProgress = searcher.run_search(investigation_id)

        witness_evidence: WitnessEvidence | None = None
        reduction: ReductionResult | None = None
        verdict: VerdictKind
        model_proposals: list[ModelProposal] = []
        intervention_result: InterventionRunResult | None = None
        patch_results: list[VerificationResult] = []

        if progress.witness is not None:
            cand = progress.witness
            pr = cand.pair_result
            assert pr is not None  # guaranteed when witness is set

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
            reduction = reduce_witness(
                cand.input_json, self.contract, self.runner
            )

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
            except Exception:  # noqa: BLE001
                pass  # model call is best-effort; investigation continues

            # ------------------------------------------------------------------
            # 5. Intervention
            # ------------------------------------------------------------------
            explanation_prop = model_proposals[0] if model_proposals else _stub_proposal()
            with contextlib.suppress(Exception):  # intervention is best-effort
                intervention_result = run_intervention(
                    witness_input=cand.input_json,
                    intervention=FIXTURE_INTERVENTION,
                    contract=self.contract,
                    runner=self.runner,
                    proposal=explanation_prop,
                )

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
            except Exception:  # noqa: BLE001
                pass

            # ------------------------------------------------------------------
            # 7. Seal holdouts (frozen before patch generation)
            # ------------------------------------------------------------------
            sealed_holdouts = list(FIXTURE_HOLDOUTS)

            # ------------------------------------------------------------------
            # 8. Verify overfitted patch → should FAIL
            # ------------------------------------------------------------------
            try:
                overfitted_result = verify_patch(
                    OVERFITTED_PATCH, sealed_holdouts, self.contract, self.runner
                )
                patch_results.append(overfitted_result)
            except Exception:  # noqa: BLE001
                pass

            # ------------------------------------------------------------------
            # 9. Verify good patch → should PASS
            # ------------------------------------------------------------------
            try:
                good_result = verify_patch(
                    GOOD_PATCH, sealed_holdouts, self.contract, self.runner
                )
                patch_results.append(good_result)
            except Exception:  # noqa: BLE001
                pass

            verdict = VerdictKind.supported_conclusion_change
        elif progress.status == "exhausted":
            verdict = VerdictKind.no_violation_found
        else:
            verdict = VerdictKind.inconclusive

        # ------------------------------------------------------------------
        # 10. Assemble final InvestigationEvidence
        # ------------------------------------------------------------------
        return InvestigationEvidence(
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
        )


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
