"""Actual proposed-source execution and bounded repair verdicts."""

import copy
import difflib
import os
from pathlib import Path

import pytest

from app.domain.contract import FIXTURE_CONTRACT
from app.orchestrator.patch_verifier import ProposedPatch, apply_and_verify_patch, extract_diff
from app.orchestrator.repair import FIXTURE_HOLDOUTS
from app.providers.nebius import MOCK_RESPONSES
from app.providers.runner import LocalRunnerAdapter, PinnedPythonRunnerAdapter
from runner.patching import apply_diff, project_digest, project_sources

ROOT = Path(__file__).resolve().parents[2]
WITNESS = {"signal": [1.0] * 7 + [1.0000001192092896]}
SOURCE = project_sources(ROOT / "fixture/project")["pipeline.py"]


def diff_for(source):
    return "".join(
        difflib.unified_diff(
            SOURCE.splitlines(keepends=True),
            source.splitlines(keepends=True),
            fromfile="a/fixture/project/pipeline.py",
            tofile="b/fixture/project/pipeline.py",
        )
    )


def verify(diff, runner):
    return apply_and_verify_patch(
        ProposedPatch(diff_text=diff), FIXTURE_HOLDOUTS, WITNESS, FIXTURE_CONTRACT, runner
    )


def test_real_diff_is_not_executed_in_local_api_process():
    result = verify(extract_diff(MOCK_RESPONSES["patch_proposal"]), LocalRunnerAdapter())
    assert result.verdict == "inconclusive"
    assert result.witness_result.check_status == "not_run"
    assert len(result.execution_runs) == 28


@pytest.mark.parametrize(
    "replacement",
    [
        "    normalized = __import__('os').environ",
        "    normalized = np.load('/etc/passwd')",
        "    signal[0] = 0",
        "    normalized = [x for x in range(1000000)]",
        "    normalized = signal ** 1000",
        "    while True: pass",
    ],
)
def test_unsafe_source_is_rejected_before_dispatch(replacement):
    diff = diff_for(
        SOURCE.replace(
            "    normalized = np.array([sample / 2.0 for sample in signal])", replacement, 1
        )
    )
    result = verify(diff, LocalRunnerAdapter())
    assert result.verdict == "invalid_patch"
    assert not result.execution_runs


def test_stale_context_rejected():
    diff = extract_diff(MOCK_RESPONSES["patch_proposal"]).replace("@@ -36 +36 @@", "@@ -35 +35 @@")
    with pytest.raises(ValueError, match="context"):
        apply_diff(SOURCE, diff)


@pytest.fixture
def pinned_runner():
    old = os.environ.get("REPRODELTA_OLD_PYTHON")
    new = os.environ.get("REPRODELTA_NEW_PYTHON")
    if not old or not new:
        pytest.skip("Set REPRODELTA_OLD_PYTHON and REPRODELTA_NEW_PYTHON to pinned interpreters")
    return PinnedPythonRunnerAdapter(Path(old), Path(new))


def test_good_model_diff_executes_actual_pinned_versions(pinned_runner):
    base = project_digest(project_sources(ROOT / "fixture/project"))
    frozen = copy.deepcopy(FIXTURE_HOLDOUTS)
    result = verify(extract_diff(MOCK_RESPONSES["patch_proposal"]), pinned_runner)
    assert result.verdict == "repair_passes_stated_checks"
    assert result.witness_result.check_status == "pass"
    assert len(result.holdout_results) == 6
    assert all(item.check_status == "pass" for item in result.holdout_results)
    assert len(result.execution_runs) == 28
    assert {
        run["result"]["outputs"]["result"]["numpy_version"] for run in result.execution_runs
    } == {"1.26.4", "2.0.2"}
    assert result.patched_project_sha256 != result.base_project_sha256 == base
    assert project_digest(project_sources(ROOT / "fixture/project")) == base
    assert frozen == FIXTURE_HOLDOUTS


def test_overfitted_source_rejected_by_holdout(pinned_runner):
    needle = "    normalized = np.array([sample / 2.0 for sample in signal])"
    replacement = (
        "    if len(signal) == 8 and 1.0 < float(signal[7]) < 1.00001:\n"
        "        return 0.4999999\n" + needle
    )
    result = verify(diff_for(SOURCE.replace(needle, replacement, 1)), pinned_runner)
    assert result.witness_result.check_status == "pass"
    assert result.verdict == "repair_fails_stated_checks"
    assert result.holdout_results[-1].check_status == "fail"


def test_noop_source_fails_witness(pinned_runner):
    result = verify(
        diff_for(SOURCE.replace("# Compute element-wise squared power", "# Squared power")),
        pinned_runner,
    )
    assert result.witness_result.check_status == "fail"
    assert result.verdict == "repair_fails_stated_checks"


@pytest.mark.parametrize("failure", ["timeout", "hash", "version", "missing"])
def test_incomplete_worker_evidence_cannot_pass(failure):
    from app.providers.runner import RunnerAdapter, RunResult

    class FaultyRunner(RunnerAdapter):
        def submit(self, manifest):
            patched = manifest.patch_diff is not None
            ratio = 0.5 if patched else 0.6 if manifest.kind == "old" else 0.4
            return RunResult(
                run_id=manifest.run_id,
                status="timeout" if failure == "timeout" else "ok",
                exit_code=None if failure == "timeout" else 0,
                outputs={}
                if failure == "missing"
                else {
                    "ratio": ratio,
                    "conclusion": "above_threshold",
                    "intermediate_dtype": "float32",
                },
                base_project_sha256="wrong" if failure == "hash" else manifest.base_project_sha256,
                patched_project_sha256=manifest.patched_project_sha256,
                numpy_version="simulated"
                if failure == "version"
                else {"old": "1.26.4", "new": "2.0.2"}[manifest.kind],
            )

    result = verify(extract_diff(MOCK_RESPONSES["patch_proposal"]), FaultyRunner())
    assert result.verdict == "inconclusive"
    assert result.witness_result.check_status == "not_run"
    assert len(result.execution_runs) == 28


def test_completed_investigation_verifies_model_and_exports_frozen_cases(
    pinned_runner, monkeypatch
):
    from app.domain.types import BudgetConfig
    from app.orchestrator.investigation import InvestigationRunner
    from app.orchestrator.report import generate_report

    monkeypatch.setenv("NEBIUS_API_KEY", "")
    evidence = InvestigationRunner(
        FIXTURE_CONTRACT, BudgetConfig(max_candidates=10, max_runs=100), pinned_runner
    ).run_full_investigation("executed-repair")
    assert evidence.model_patch_result.verdict == "repair_passes_stated_checks"
    assert evidence.frozen_holdouts == FIXTURE_HOLDOUTS
    report = generate_report(evidence)
    assert report["schema_version"] == "3"
    assert report["model_patch_result"]["verdict"] == "repair_passes_stated_checks"
    assert report["frozen_holdouts"] == FIXTURE_HOLDOUTS


async def test_api_uses_persisted_cases_and_keeps_final_evidence_immutable(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.api import investigations as api
    from app.domain.contract import hash_contract
    from app.main import app
    from app.storage.investigation_store import MemoryInvestigationStore

    store = MemoryInvestigationStore()
    inv_id = store.create_investigation(str(api.DEMO_PROJECT_ID), {}, "user")
    sealed = {
        "witness": {"input_json": WITNESS},
        "contract_body": FIXTURE_CONTRACT.model_dump(),
        "contract_hash": hash_contract(FIXTURE_CONTRACT),
        "frozen_holdouts": copy.deepcopy(FIXTURE_HOLDOUTS[:1]),
    }
    store.store_evidence(inv_id, sealed)
    monkeypatch.setattr(api, "_store", store)
    monkeypatch.setattr(api, "_investigations", {})
    app.dependency_overrides[api.get_runner] = lambda: LocalRunnerAdapter()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/investigations/{inv_id}/verify-patch",
                json={"diff_text": extract_diff(MOCK_RESPONSES["patch_proposal"])},
            )
        assert response.status_code == 200
        result = response.json()
        assert result["verdict"] == "inconclusive"
        assert len(result["holdout_results"]) == 1
        assert len(result["execution_runs"]) == 8
        assert store.get_evidence(inv_id) == sealed
        assert store.get_events(inv_id)[-1]["payload"]["verification"] == result
    finally:
        app.dependency_overrides.clear()
