"""The project worker does not manufacture findings on preflight failure."""

import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest

from app.domain.contract import FIXTURE_CONTRACT, InputField, InputSchema, OutputSelector
from app.domain.project_run import ProjectRunSpec
from app.orchestrator import project_worker
from app.providers.e2b_runner import EnvironmentBatchResult


def _job(candidate_budget=1):
    spec = ProjectRunSpec(
        profile_id=uuid.uuid4(),
        provider="github",
        repository="owner/repo",
        change_number=7,
        base_sha="a" * 40,
        head_sha="b" * 40,
        source_sha256="c" * 64,
        profile_digest="d" * 64,
        contract_hash="e" * 64,
        old_environment_lock_sha256="f" * 64,
        new_environment_lock_sha256="0" * 64,
        sandbox_usd_per_second=0.0001,
        max_cost_usd=0.2,
        candidate_budget=candidate_budget,
    )
    return {"id": str(uuid.uuid4()), "lease_token": str(uuid.uuid4()),
            "owner_id": "github:123", "spec_json": spec.model_dump(mode="json")}


class Queue:
    def __init__(self):
        self.final = None
        self.cancelled = False
        self.usage = []
        self.attempts = {}

    def cancellation_requested(self, *_):
        return self.cancelled

    def reserve_sandbox_attempt(self, _job_id, _lease, *, timeout_seconds, usd_per_second):
        attempt = uuid.uuid4()
        self.attempts[attempt] = len(self.usage)
        self.usage.append((timeout_seconds, usd_per_second, True))
        return attempt

    def settle_sandbox_attempt(self, _job_id, _lease, attempt_id, *, actual_seconds):
        index = self.attempts.pop(attempt_id)
        self.usage[index] = (actual_seconds, self.usage[index][1], False)
        return True

    def finish(self, _job_id, _lease, *, status, result=None, error_code=None):
        self.final = (status, result, error_code)
        return True


def test_disabled_worker_never_fetches_source_or_executes(monkeypatch):
    queue = Queue()
    monkeypatch.setattr(
        project_worker, "get_settings", lambda: SimpleNamespace(e2b_project_runs_enabled=False)
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Source or sandbox was accessed with execution disabled")

    assert project_worker.run_claimed_job(
        _job(), queue, None, execute_batch=forbidden, fetch_source=forbidden
    )
    assert queue.final == ("failed", None, "execution_disabled")


@pytest.mark.parametrize("tamper", [None, "input", "version", "unstable"])
def test_approved_sample_records_observations_not_a_general_verdict(
    monkeypatch, tamper,
):
    job = _job()
    queue = Queue()
    spec = ProjectRunSpec.model_validate(job["spec_json"])
    contract = FIXTURE_CONTRACT.model_copy(
        update={"output_selector": OutputSelector(field_name="ratio")}
    )
    draft = SimpleNamespace(
        adapter_module="package.adapter", adapter_function="run",
        sample_input={"signal": [1.0] * 8}, contract=contract,
    )
    approval = SimpleNamespace(
        old=SimpleNamespace(template_id="old", numpy_version="1.26.4",
                            lock_sha256=spec.old_environment_lock_sha256),
        new=SimpleNamespace(template_id="new", numpy_version="2.0.2",
                            lock_sha256=spec.new_environment_lock_sha256),
    )
    monkeypatch.setattr(
        project_worker, "get_settings", lambda: SimpleNamespace(e2b_project_runs_enabled=True)
    )
    monkeypatch.setattr(
        project_worker, "_approved_inputs", lambda *_: ({}, draft, approval)
    )
    monkeypatch.setattr(
        project_worker, "check_eligibility", lambda *_: {"eligible": True}
    )

    async def source(*_):
        return {}, {"package/adapter.py": b"trusted test bytes"}

    versions = {"1.26.4": 0.4, "2.0.2": 0.6}

    def execute_batch(**kwargs):
        assert kwargs["should_abort"]() is False
        assert 1 <= kwargs["timeout_seconds"] <= 900
        assert kwargs["expected_lock_sha256"] == (
            spec.old_environment_lock_sha256 if kwargs["expected_numpy"] == "1.26.4"
            else spec.new_environment_lock_sha256
        )
        version = kwargs["expected_numpy"]
        inputs = kwargs["inputs_json"]
        assert inputs == [draft.sample_input, draft.sample_input]
        ratios = [versions[version], versions[version]]
        if tamper == "unstable" and version == "2.0.2":
            ratios[1] = 0.4
        hashes = [hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                  for value in inputs]
        if tamper == "input" and version == "2.0.2":
            hashes[1] = "f" * 64
        return EnvironmentBatchResult(
            version="0.0.0" if tamper == "version" and version == "2.0.2" else version,
            sandbox_id=version.replace(".", ""), duration_seconds=1,
            source_sha256=spec.source_sha256,
            input_sha256=hashes,
            outputs=[{"result": {"ratio": ratio}} for ratio in ratios],
            stderrs=["", ""],
        )

    assert project_worker.run_claimed_job(
        job, queue, None, execute_batch=execute_batch, fetch_source=source,
        store_snapshot=lambda owner, files, digest: files,
    )
    status, result, error = queue.final
    if tamper in {"input", "version"}:
        assert (status, result, error) == (
            "failed", None,
            "environment_result_mismatch" if tamper == "version" else "candidate_result_mismatch",
        )
        assert queue.usage == [(1, 0.0001, False), (1, 0.0001, False)]
        return
    assert (status, error) == ("completed", None)
    assert result["changed_conclusion"] is (tamper is None)
    assert result["witness_input"] == (draft.sample_input if tamper is None else None)
    assert result["repeatable"] is (tamper is None)
    assert result["inconclusive"] is (tamper == "unstable")
    assert result["scope"] == "one approved sample input at one dependency-only PR revision"
    assert result["sandbox_seconds"] == 2
    assert queue.usage == [(1, 0.0001, False), (1, 0.0001, False)]


def test_owner_cancellation_prevents_any_sandbox(monkeypatch):
    queue = Queue()
    queue.cancelled = True
    monkeypatch.setattr(
        project_worker, "get_settings", lambda: SimpleNamespace(e2b_project_runs_enabled=True)
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Cancelled job accessed the source or sandbox")

    assert project_worker.run_claimed_job(
        _job(), queue, None, execute_batch=forbidden, fetch_source=forbidden
    )
    assert queue.final == ("cancelled", None, None)


@pytest.mark.parametrize(("changed", "unstable"), [
    (True, False), (False, False), (True, True),
])
def test_candidate_search_uses_controls_before_claiming_witness(
    monkeypatch, changed, unstable,
):
    job = _job(candidate_budget=3)
    queue = Queue()
    spec = ProjectRunSpec.model_validate(job["spec_json"])
    contract = FIXTURE_CONTRACT.model_copy(
        update={"output_selector": OutputSelector(field_name="ratio")}
    )
    draft = SimpleNamespace(
        adapter_module="package.adapter", adapter_function="run",
        sample_input={"signal": [1.0] * 8}, contract=contract,
    )
    approval = SimpleNamespace(
        old=SimpleNamespace(template_id="old", numpy_version="1.26.4",
                            lock_sha256=spec.old_environment_lock_sha256),
        new=SimpleNamespace(template_id="new", numpy_version="2.0.2",
                            lock_sha256=spec.new_environment_lock_sha256),
    )
    monkeypatch.setattr(project_worker, "get_settings", lambda: SimpleNamespace(
        e2b_project_runs_enabled=True))
    monkeypatch.setattr(project_worker, "_approved_inputs", lambda *_: ({}, draft, approval))
    monkeypatch.setattr(project_worker, "check_eligibility", lambda *_: {"eligible": True})

    async def source(*_):
        return {}, {"package/adapter.py": b"trusted test bytes"}

    calls = []

    def execute_batch(**kwargs):
        calls.append(kwargs)
        inputs = kwargs["inputs_json"]
        assert len(inputs) == 6
        assert inputs[0] == inputs[1] and inputs[2] == inputs[3]
        ratios = ([0.4] * 6 if kwargs["expected_numpy"] == "1.26.4"
                  else [0.4, 0.4, 0.6 if changed else 0.4,
                        0.4 if unstable else (0.6 if changed else 0.4), 0.4, 0.4])
        return EnvironmentBatchResult(
            version=kwargs["expected_numpy"], sandbox_id="sandbox", duration_seconds=2,
            source_sha256=spec.source_sha256,
            input_sha256=[hashlib.sha256(json.dumps(value, sort_keys=True,
                allow_nan=False).encode()).hexdigest() for value in inputs],
            outputs=[{"result": {"ratio": ratio}} for ratio in ratios], stderrs=[""] * 6,
        )

    assert project_worker.run_claimed_job(
        job, queue, None, execute_batch=execute_batch, fetch_source=source,
        store_snapshot=lambda owner, files, digest: files,
    )
    status, result, error = queue.final
    assert (status, error) == ("completed", None)
    assert len(calls) == 2
    assert result["attempted"] == 3
    assert result["changed_conclusion"] is (changed and not unstable)
    assert result["witness"] == (result["observations"][1] if changed and not unstable else None)
    assert result["witness_input"] == (calls[0]["inputs_json"][2]
                                      if changed and not unstable else None)
    assert result["inconclusive"] is unstable
    assert result["estimated_sandbox_cost_usd"] == 0.0004
    assert queue.usage == [(2, 0.0001, False), (2, 0.0001, False)]


def test_failed_sandbox_attempt_records_uncertain_cost_bound():
    queue = Queue()

    def failed(**_kwargs):
        raise RuntimeError("provider failure")

    with pytest.raises(RuntimeError, match="provider failure"):
        project_worker._run_metered(
            queue, uuid.uuid4(), uuid.uuid4(), 0.0001,
            failed, timeout_seconds=13,
        )
    assert queue.usage == [(13, 0.0001, True)]


@pytest.mark.parametrize("unstable", [False, True])
def test_adaptive_project_search_uses_only_repeatable_feedback_and_accounts_sandboxes(
    monkeypatch, unstable,
):
    job = _job(candidate_budget=4)
    job["spec_json"]["candidate_strategy"] = "adaptive"
    spec = ProjectRunSpec.model_validate(job["spec_json"])
    contract = FIXTURE_CONTRACT.model_copy(update={
        "input_schema": InputSchema(fields=[InputField(
            name="x", dtype="float32", shape=[], min_value=0, max_value=1,
        )]),
        "output_selector": OutputSelector(field_name="ratio"),
    })
    draft = SimpleNamespace(adapter_module="probe", adapter_function="measure",
                            sample_input={"x": 0.5}, contract=contract)
    approval = SimpleNamespace(old=SimpleNamespace(template_id="old", numpy_version="1.26.4",
        lock_sha256=spec.old_environment_lock_sha256),
        new=SimpleNamespace(template_id="new", numpy_version="2.0.2",
        lock_sha256=spec.new_environment_lock_sha256))
    monkeypatch.setattr(project_worker, "get_settings", lambda: SimpleNamespace(
        e2b_project_runs_enabled=True))
    monkeypatch.setattr(project_worker, "_approved_inputs", lambda *_: ({}, draft, approval))
    monkeypatch.setattr(project_worker, "check_eligibility", lambda *_: {"eligible": True})

    async def source(*_):
        return {}, {"probe.py": b"trusted"}

    calls = []

    def execute_batch(**kwargs):
        inputs = kwargs["inputs_json"]
        calls.append((kwargs["expected_numpy"], inputs))
        version = kwargs["expected_numpy"]
        outputs = [{"result": {"ratio": value["x"] +
                    (0.2 if unstable and version == "2.0.2" and index == 1
                     else 0.1 if version == "2.0.2" else -0.1)}}
                   for index, value in enumerate(inputs)]
        return EnvironmentBatchResult(
            version=version, sandbox_id=f"sandbox-{len(calls)}", duration_seconds=2,
            source_sha256=spec.source_sha256,
            input_sha256=[project_worker._input_digest(value) for value in inputs],
            outputs=outputs, stderrs=[""] * len(inputs),
        )

    queue = Queue()
    assert project_worker.run_claimed_job(
        job, queue, None, execute_batch=execute_batch, fetch_source=source,
        store_snapshot=lambda _owner, files, _digest: files,
    )
    status, finding, error = queue.final
    assert (status, error) == ("completed", None)
    assert finding["strategy"] == "adaptive"
    assert finding["attempted"] == (2 if unstable else 4)
    assert finding["inconclusive"] is unstable
    assert len(finding["sandbox_ids"]) == (2 if unstable else 4)
    assert len(calls) == (2 if unstable else 4)
    assert calls[0][1] == calls[1][1]
    if not unstable:
        assert calls[2][1] == calls[3][1]
        assert calls[2][1] != calls[0][1]
    assert queue.usage == [(2, 0.0001, False)] * len(calls)
