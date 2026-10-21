"""The E2B boundary is testable without creating billable sandboxes."""

import json

import pytest

from app.providers.e2b_runner import ProjectAdapter, execute_environment, validate_snapshot


class _CommandResult:
    def __init__(self, stdout, exit_code=0):
        self.stdout = stdout
        self.stderr = ""
        self.exit_code = exit_code


class _Sandbox:
    sandbox_id = "sandbox-test"

    def __init__(self, *, fail_run=False):
        self.uploads = {}
        self.killed = False
        self.fail_run = fail_run
        self.files = self
        self.commands = self

    def write(self, path, content):
        self.uploads[path] = content

    def make_dir(self, path):
        return True

    def run(self, command, timeout):
        if "numpy.__version__" in command:
            return _CommandResult("1.26.4\n")
        if self.fail_run:
            return _CommandResult("", exit_code=1)
        return _CommandResult(json.dumps({"result": {"ratio": 0.5}}))

    def kill(self):
        self.killed = True


class _Factory:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.options = None

    def create(self, **options):
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
