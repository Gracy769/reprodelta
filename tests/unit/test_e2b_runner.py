"""The E2B boundary is testable without creating billable sandboxes."""

import json

import pytest

from app.providers.e2b_runner import (
    ProjectAdapter,
    execute_environment,
    execute_environment_batch,
    validate_snapshot,
)


class _CommandResult:
    def __init__(self, stdout, exit_code=0):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = exit_code


class _Sandbox:
    sandbox_id = "sandbox-test"

    def __init__(self, *, fail_run=False, lock_digest="a" * 64):
        self.uploads = {}
        self.killed = False
        self.fail_run = fail_run
        self.lock_digest = lock_digest
        self.files = self
        self.commands = self
        self.run_inputs = []

    def write(self, path, content):
        self.uploads[path] = content

    def make_dir(self, path):
        return True

    def run(self, command, timeout):
        if "numpy.__version__" in command:
            return _CommandResult("1.26.4\n")
        if "reprodelta-environment.lock" in command:
            return _CommandResult(self.lock_digest + "\n")
        if self.fail_run:
            return _CommandResult("", exit_code=1)
        payload = json.loads(self.uploads["/home/user/reprodelta-input.json"])
        self.run_inputs.append(payload)
        return _CommandResult(json.dumps({"result": {"ratio": payload.get("x", 0.5)}}))

    def kill(self):
        self.killed = True


class _Factory:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.options = None
        self.create_count = 0

    def create(self, **options):
        self.create_count += 1
        self.options = options
        return self.sandbox


def test_snapshot_rejects_traversal_and_oversize():
    with pytest.raises(ValueError):
        validate_snapshot({"../secret.py": b"pass"})
    with pytest.raises(ValueError):
        validate_snapshot({"a.py": b"x" * 1_000_001})


def test_e2b_execution_disables_network_and_always_kills():
    sandbox = _Sandbox()
    factory = _Factory(sandbox)
    result = execute_environment(
        files={"adapter.py": b"def run(data): return data"},
        input_json={"signal": [1.0]},
        adapter=ProjectAdapter(module="adapter", function="run"),
        template_id="approved-numpy-old",
        expected_numpy="1.26.4",
        sandbox_factory=factory,
    )
    assert factory.options == {
        "template": "approved-numpy-old",
        "timeout": 900,
        "secure": True,
        "allow_internet_access": False,
        "envs": {},
    }
    assert result.version == "1.26.4"
    assert result.output["result"]["ratio"] == 0.5
    assert sandbox.killed
    assert all(b"secret" not in value for value in sandbox.uploads.values())


def test_e2b_failed_adapter_still_kills():
    sandbox = _Sandbox(fail_run=True)
    with pytest.raises(RuntimeError, match="Approved adapter failed"):
        execute_environment(
            files={"adapter.py": b"pass"},
            input_json={},
            adapter=ProjectAdapter(module="adapter", function="run"),
            template_id="approved-numpy-old",
            expected_numpy="1.26.4",
            sandbox_factory=_Factory(sandbox),
        )
    assert sandbox.killed


def test_cancellation_after_sandbox_creation_always_kills():
    sandbox = _Sandbox()
    with pytest.raises(RuntimeError, match="cancelled or its worker lease was lost"):
        execute_environment(
            files={"adapter.py": b"pass"},
            input_json={},
            adapter=ProjectAdapter(module="adapter", function="run"),
            template_id="approved-numpy-old",
            expected_numpy="1.26.4",
            sandbox_factory=_Factory(sandbox),
            should_abort=lambda: True,
        )
    assert sandbox.killed
    assert sandbox.uploads == {}


def test_sandbox_timeout_uses_remaining_budget():
    sandbox = _Sandbox()
    factory = _Factory(sandbox)
    execute_environment(
        files={"adapter.py": b"pass"}, input_json={},
        adapter=ProjectAdapter(module="adapter", function="run"),
        template_id="approved-numpy-old", expected_numpy="1.26.4",
        sandbox_factory=factory, timeout_seconds=7,
    )
    assert factory.options["timeout"] == 7
    assert sandbox.killed


def test_unapproved_template_never_receives_source_snapshot():
    sandbox = _Sandbox(lock_digest="b" * 64)
    with pytest.raises(RuntimeError, match="lock digest"):
        execute_environment(
            files={"private/adapter.py": b"private source"}, input_json={},
            adapter=ProjectAdapter(module="private.adapter", function="run"),
            template_id="approved-numpy-old", expected_numpy="1.26.4",
            expected_lock_sha256="a" * 64, sandbox_factory=_Factory(sandbox),
        )
    assert sandbox.uploads == {}
    assert sandbox.killed


def test_batch_reuses_one_offline_sandbox_for_bounded_inputs():
    sandbox = _Sandbox()
    factory = _Factory(sandbox)
    result = execute_environment_batch(
        files={"adapter.py": b"def run(data): return data"},
        inputs_json=[{"x": 0.25}, {"x": 0.75}],
        adapter=ProjectAdapter(module="adapter", function="run"),
        template_id="approved-numpy-old", expected_numpy="1.26.4",
        expected_lock_sha256="a" * 64, sandbox_factory=factory,
    )
    assert factory.create_count == 1
    assert factory.options["allow_internet_access"] is False
    assert factory.options["envs"] == {}
    assert sandbox.run_inputs == [{"x": 0.25}, {"x": 0.75}]
    assert [item["result"]["ratio"] for item in result.outputs] == [0.25, 0.75]
    assert len(result.input_sha256) == 2
    assert sandbox.killed


def test_batch_rejects_too_many_inputs_before_sandbox_creation():
    factory = _Factory(_Sandbox())
    with pytest.raises(ValueError, match="1–32"):
        execute_environment_batch(
            files={"adapter.py": b"pass"}, inputs_json=[{}] * 33,
            adapter=ProjectAdapter(module="adapter", function="run"),
            template_id="approved-numpy-old", expected_numpy="1.26.4",
            sandbox_factory=factory,
        )
    assert factory.create_count == 0


def test_batch_cancellation_stops_before_next_input_and_kills_sandbox():
    sandbox = _Sandbox()
    factory = _Factory(sandbox)
    with pytest.raises(RuntimeError, match="cancelled"):
        execute_environment_batch(
            files={"adapter.py": b"def run(data): return data"},
            inputs_json=[{"x": 0.25}, {"x": 0.75}],
            adapter=ProjectAdapter(module="adapter", function="run"),
            template_id="approved-numpy-old", expected_numpy="1.26.4",
            expected_lock_sha256="a" * 64, sandbox_factory=factory,
            should_abort=lambda: len(sandbox.run_inputs) == 1,
        )
    assert sandbox.run_inputs == [{"x": 0.25}]
    assert sandbox.killed
