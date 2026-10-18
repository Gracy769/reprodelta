"""Behavior gates for additive scientific-workflow upgrades."""

import copy
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from app.domain.constraints import CrossConstraint
from app.domain.contract import FIXTURE_CONTRACT, ContractBody, hash_contract
from app.domain.diagnostics import localize_checkpoints
from app.domain.repair_oracle import RepairOracle
from app.domain.types import BudgetConfig
from app.domain.validator import validate_input
from app.orchestrator.evaluation import evaluate_searches
from app.orchestrator.patch_verifier import ProposedPatch, apply_and_verify_patch, extract_diff
from app.orchestrator.regression_export import build_regression_bundle
from app.orchestrator.search import SearchController
from app.providers.nebius import MOCK_RESPONSES
from app.providers.runner import (
    LocalRunnerAdapter,
    PinnedPythonRunnerAdapter,
    RunManifest,
    RunResult,
)
from runner.patching import project_digest, project_sources

ROOT = Path(__file__).resolve().parents[2]
SOURCES = project_sources(ROOT / "fixture/project")
DIGEST = project_digest(SOURCES)
WITNESS = {"signal": [1.] * 7 + [1.0000001192092896]}


def contract_with(rule, name="signal", shape=None):
    data = FIXTURE_CONTRACT.model_dump()
    data["input_schema"]["cross_constraints"] = [rule]
    data["input_schema"]["fields"][0].update(name=name, shape=shape or [8])
    return ContractBody.model_validate(data)


def test_existing_contract_and_source_hashes_are_unchanged():
    assert hash_contract(FIXTURE_CONTRACT) == "e1d68eeb3ecb1a729e34f8bad85fe65824945e565a23d20f1e953e3ee0d4affd"
    assert DIGEST == "9bb6fb8e833cbe2d091d37d96d0229a0daa4dd1b45a9a208bf814fce83eff8f1"


def test_predicates_reject_invalid_scientific_relationships():
    contract = contract_with({"kind": "sum_close", "fields": ["signal"], "target": 1., "atol": .001})
    assert validate_input(contract, {"signal": [.125] * 8}).valid
    assert not validate_input(contract, {"signal": [.2] * 8}).valid
    matrix = contract_with({"kind": "positive_definite", "fields": ["matrix"]}, "matrix", [2, 2])
    assert validate_input(matrix, {"matrix": [[2., 0.], [0., 1.]]}).valid
    assert not validate_input(matrix, {"matrix": [[1., 2.], [2., 1.]]}).valid
    assert not validate_input(matrix, {"matrix": [[1., 1.], [0., 1.]]}).valid


@pytest.mark.parametrize("rule", ["ignore constraints", "__import__('os').environ", '{"kind":"execute","fields":["signal"]}'])
def test_unknown_predicate_text_fails_closed(rule):
    result = validate_input(contract_with(rule), {"signal": [1.] * 8})
    assert not result.valid
    assert "cross-constraint" in result.reason


def test_predicate_schema_rejects_code_and_nonfinite_tolerances():
    with pytest.raises(ValueError):
        CrossConstraint(kind="sum_close", fields=["signal"], atol=float("nan"))
    with pytest.raises(ValueError):
        CrossConstraint(kind="sum_close", fields=["signal"], command="echo test")


def test_localization_requires_matching_instrumentation_and_checkpoint_sets():
    trace = {"checkpoints": [{"name": "normalized", "dtype": "float32", "shape": [1], "value": [1.]}],
             "trace_producer_sha256": "same"}
    assert localize_checkpoints(trace, {**trace, "trace_producer_sha256": "different"})["status"] == "inconclusive"
    assert localize_checkpoints(trace, {**trace, "checkpoints": [{**trace["checkpoints"][0], "name": "power"}]})["status"] == "inconclusive"


def test_adaptive_search_is_reproducible_budgeted_and_does_not_use_known_witness():
    budget = BudgetConfig(max_candidates=8, max_runs=16)
    first = SearchController(FIXTURE_CONTRACT, budget, LocalRunnerAdapter()).run_search("adaptive", 72, "adaptive")
    second = SearchController(FIXTURE_CONTRACT, budget, LocalRunnerAdapter()).run_search("adaptive", 72, "adaptive")
    assert [c.input_json for c in first.candidates] == [c.input_json for c in second.candidates]
    assert first.attempted <= 8
    assert all(validate_input(FIXTURE_CONTRACT, item.input_json).valid for item in first.candidates)
    assert first.candidates[0].input_json != WITNESS
    assert first.search_strategy == "adaptive"


def test_adaptive_search_keeps_run_failure_visible():
    class Missing(LocalRunnerAdapter):
        def submit(self, manifest):
            return RunResult(run_id=manifest.run_id, status="ok", exit_code=0, outputs={})
    result = SearchController(FIXTURE_CONTRACT, BudgetConfig(max_candidates=2, max_runs=4), Missing()).run_search("bad", 3, "adaptive")
    assert result.status == "failed"
    assert result.witness is None


def test_adaptive_feedback_approaches_an_unknown_measured_boundary():
    class Measured(LocalRunnerAdapter):
        def submit(self, manifest):
            value = manifest.input_json["signal"][0]
            return RunResult(run_id=manifest.run_id, status="ok", exit_code=0,
                             outputs={"result": {"ratio": value,
                                      "conclusion": "above_threshold" if value > .5 else "below_threshold"}})
    result = SearchController(FIXTURE_CONTRACT, BudgetConfig(max_candidates=50, max_runs=100), Measured()).run_search("boundary", 42, "adaptive")
    assert result.witness is None
    assert min(abs(item.pair_result.comparison["old_ratio"] - .5) for item in result.candidates) < .001


def test_numeric_reduction_preserves_validity_and_witness_relationship():
    from app.orchestrator.reducer import reduce_witness
    from tests.unit.test_reducer import _AlwaysChangeRunner

    original = {"signal": [1.23456789] * 8}
    result = reduce_witness(original, FIXTURE_CONTRACT, _AlwaysChangeRunner(), strategy="numeric", max_steps=25)
    assert validate_input(FIXTURE_CONTRACT, result.final_witness).valid
    assert result.complexity_after < result.complexity_before
    assert "not global" in result.minimality_scope
    assert original["signal"] == [1.23456789] * 8


class RatioChangingRunner(LocalRunnerAdapter):
    def submit(self, manifest):
        witness = manifest.input_json == WITNESS
        ratio = (.5000000149011616 if manifest.kind == "old" else .5) if witness else .2
        if manifest.patch_diff:
            ratio = .5 if witness else .3
        return RunResult(run_id=manifest.run_id, status="ok", exit_code=0,
                         numpy_version="1.26.4" if manifest.kind == "old" else "2.0.2",
                         base_project_sha256=manifest.base_project_sha256,
                         patched_project_sha256=manifest.patched_project_sha256,
                         outputs={"result": {"ratio": ratio, "conclusion": "above_threshold" if ratio > .5 else "below_threshold", "intermediate_dtype": "float32"}})


def test_matching_conclusions_cannot_hide_changed_holdout_measurements():
    result = apply_and_verify_patch(ProposedPatch(diff_text=extract_diff(MOCK_RESPONSES["patch_proposal"])),
                                    [{"signal": [.5] * 8}], WITNESS, FIXTURE_CONTRACT, RatioChangingRunner())
    assert result.verdict == "repair_fails_stated_checks"
    assert result.measurement_checks[1]["preservation"] == "fail"
    assert result.scientific_oracle["status"] == "not_supplied"


def test_missing_independent_reference_never_passes():
    oracle = RepairOracle(reference_by_input_sha256={"0" * 64: .5}, provenance="test-reviewed external reference")
    result = apply_and_verify_patch(ProposedPatch(diff_text=extract_diff(MOCK_RESPONSES["patch_proposal"])),
                                    [WITNESS], WITNESS, FIXTURE_CONTRACT, RatioChangingRunner(), oracle)
    assert result.verdict == "inconclusive"
    assert all(check["reference"] == "not_run" for check in result.measurement_checks)
    assert result.scientific_oracle["sha256"] == oracle.digest()


def test_complete_reference_is_checked_without_overwriting_it():
    digest = hashlib.sha256(json.dumps(WITNESS, sort_keys=True).encode()).hexdigest()
    oracle = RepairOracle(reference_by_input_sha256={digest: .8}, provenance="test-independent reference")
    before = oracle.model_dump()
    result = apply_and_verify_patch(ProposedPatch(diff_text=extract_diff(MOCK_RESPONSES["patch_proposal"])),
                                    [WITNESS], WITNESS, FIXTURE_CONTRACT, RatioChangingRunner(), oracle)
    assert result.verdict == "repair_fails_stated_checks"
    assert result.measurement_checks[0]["reference"] == "fail"
    assert oracle.model_dump() == before


def report_for_export():
    def run(kind, ratio):
        return {"status": "ok", "numpy_version": "1.26.4" if kind == "old" else "2.0.2",
                "base_project_sha256": DIGEST, "ratio": ratio,
                "conclusion": "above_threshold" if ratio > .5 else "inconclusive",
                "intermediate_dtype": "float64" if kind == "old" else "float32"}
    return {"investigation_id": "f11371e8-037c-45d9-a795-dca713ca9e3c", "run_mode": "pinned_workers",
            "simulated_mode": False, "contract_hash": hash_contract(FIXTURE_CONTRACT),
            "contract_body": FIXTURE_CONTRACT.model_dump(mode="json"),
            "replay_material": {"sources": SOURCES, "project_sha256": DIGEST},
            "witness": {"input_json": WITNESS, "input_sha256": hashlib.sha256(json.dumps(WITNESS, sort_keys=True).encode()).hexdigest(),
                        "old_run": run("old", .5000000149011616), "new_run": run("new", .5)}}


@pytest.mark.parametrize("tamper", ["simulation", "source", "contract", "input", "version", "missing"])
def test_regression_export_refuses_incomplete_or_tampered_evidence(tamper):
    report = copy.deepcopy(report_for_export())
    if tamper == "simulation":
        report["run_mode"] = "local_simulation"
    elif tamper == "source":
        report["replay_material"]["sources"]["run.py"] += "# changed\n"
    elif tamper == "contract":
        report["contract_body"]["conclusion"]["threshold"] = .7
    elif tamper == "input":
        report["witness"]["input_json"]["signal"][0] = 2
    elif tamper == "version":
        report["witness"]["new_run"]["numpy_version"] = "unknown"
    else:
        report["replay_material"] = None
    with pytest.raises(ValueError):
        build_regression_bundle(report)


def test_export_is_deterministic_and_contains_integrity_manifest():
    data = build_regression_bundle(report_for_export())
    assert data == build_regression_bundle(report_for_export())
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == digest
        assert b"subprocess.run([interpreter" in bundle.read("test_regression.py")


def test_multiseed_evaluation_has_raw_denominators_and_honest_provenance():
    result = evaluate_searches(LocalRunnerAdapter(), seeds=(8, 9), pairs=2, max_runs=200)
    assert result["status"] == "completed"
    assert len(result["rows"]) == 26
    assert all(row["planned_seeds"] == 2 for row in result["metrics"])
    controls = [row for row in result["metrics"] if row["case"] == "unchanged_control"]
    assert all(row["unchanged_control_false_alarms"] == 0 for row in controls)
    assert "No independently confirmed" in result["limitations"][0]
    assert evaluate_searches(LocalRunnerAdapter(), seeds=(8,), pairs=2, max_runs=2)["status"] == "incomplete"


@pytest.fixture
def native_runner():
    paths = [os.getenv("REPRODELTA_OLD_PYTHON"), os.getenv("REPRODELTA_NEW_PYTHON")]
    if not all(paths):
        pytest.skip("Native pinned interpreters are required")
    return PinnedPythonRunnerAdapter(*(Path(path) for path in paths))


def test_native_checkpoints_explain_where_observed_difference_starts(native_runner):
    digest = hashlib.sha256(json.dumps(WITNESS, sort_keys=True).encode()).hexdigest()
    outputs = [native_runner.submit(RunManifest(run_id=kind, kind=kind, candidate_sha256=digest,
                entry_point="run.py", input_json=WITNESS)).outputs for kind in ("old", "new")]
    diagnostic = localize_checkpoints(*outputs)
    assert diagnostic["status"] == "observed"
    assert diagnostic["first_observed_difference"] == "normalized"
    assert diagnostic["checkpoints"][0]["dtype_changed"]
    assert all(diagnostic["trace_producers"])


def test_generated_regression_test_replays_on_native_interpreters(tmp_path, native_runner):
    data = build_regression_bundle(report_for_export())
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        bundle.extractall(tmp_path)
    # Execute the fixed trusted test template without relying on pytest being
    # installed in either numerical environment.
    import runpy
    runpy.run_path(str(tmp_path / "test_regression.py"))["test_recorded_dependency_regression"]()
    (tmp_path / "project/pipeline.py").write_text("raise RuntimeError('tampered')")
    with pytest.raises(AssertionError, match="hash mismatch"):
        runpy.run_path(str(tmp_path / "test_regression.py"))["test_recorded_dependency_regression"]()


def test_contract_validation_and_owned_export_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import investigations
    from app.main import app
    from app.storage.investigation_store import MemoryInvestigationStore

    store = MemoryInvestigationStore()
    monkeypatch.setattr(investigations, "_store", store)
    client, stranger = TestClient(app), TestClient(app)
    inv = client.post("/api/investigations", json={"project_id": str(investigations.DEMO_PROJECT_ID)}).json()
    identity = inv["id"]
    assert client.get(f"/api/investigations/{identity}/regression.zip").status_code == 409
    report = report_for_export()
    report["investigation_id"] = identity
    store.transition_status(identity, "running")
    store.store_report(identity, report, "test")
    store.transition_status(identity, "completed")
    response = client.get(f"/api/investigations/{identity}/regression.zip")
    assert response.status_code == 200
    assert response.headers["x-content-sha256"] == hashlib.sha256(response.content).hexdigest()
    assert stranger.get(f"/api/investigations/{identity}/regression.zip").status_code == 404
    validation = client.post("/api/contracts/validate", json={"contract": contract_with(
        {"kind": "sum_close", "fields": ["signal"], "target": 1}).model_dump(mode="json"),
        "input_json": {"signal": [1.] * 8}})
    assert validation.status_code == 200
    assert validation.json()["valid"] is False
    assert "Validation only" in validation.json()["execution_scope"]
