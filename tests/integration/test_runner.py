"""Integration tests for runner/entrypoint.py validate_manifest and run_fixture."""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from app.providers.runner import RunManifest, WorkerHttpRunnerAdapter
from runner.entrypoint import FIXTURE_DIR, run_fixture, validate_manifest
from runner.server import Handler

VALID_INPUT = {"signal": [1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5]}
VALID_SHA = hashlib.sha256(json.dumps(VALID_INPUT, sort_keys=True).encode()).hexdigest()


class TestValidateManifest:
    def _valid(self, **overrides):
        base = {
            "run_id": "test-run-001",
            "kind": "old",
            "candidate_sha256": VALID_SHA,
            "entry_point": "run.py",
            "input_json": VALID_INPUT,
        }
        base.update(overrides)
        return base

    def test_valid_manifest_passes(self):
        validate_manifest(self._valid())  # must not raise

    def test_missing_run_id(self):
        m = self._valid()
        del m["run_id"]
        with pytest.raises(ValueError, match="Missing manifest fields"):
            validate_manifest(m)

    def test_missing_kind(self):
        m = self._valid()
        del m["kind"]
        with pytest.raises(ValueError, match="Missing manifest fields"):
            validate_manifest(m)

    def test_missing_entry_point(self):
        m = self._valid()
        del m["entry_point"]
        with pytest.raises(ValueError, match="Missing manifest fields"):
            validate_manifest(m)

    def test_missing_input_json(self):
        m = self._valid()
        del m["input_json"]
        with pytest.raises(ValueError, match="Missing manifest fields"):
            validate_manifest(m)

    def test_invalid_kind(self):
        with pytest.raises(ValueError, match="Invalid kind"):
            validate_manifest(self._valid(kind="invalid"))

    def test_disallowed_entry_point(self):
        with pytest.raises(ValueError, match="Entry point not allowed"):
            validate_manifest(self._valid(entry_point="evil.py"))

    def test_entry_point_path_traversal(self):
        with pytest.raises(ValueError, match="Entry point not allowed"):
            validate_manifest(self._valid(entry_point="../../../etc/passwd"))

    def test_invalid_sha256_too_short(self):
        with pytest.raises(ValueError, match="Invalid candidate_sha256"):
            validate_manifest(self._valid(candidate_sha256="abc123"))

    def test_invalid_sha256_non_hex(self):
        with pytest.raises(ValueError, match="Invalid candidate_sha256"):
            validate_manifest(self._valid(candidate_sha256="z" * 64))

    def test_input_json_must_be_dict(self):
        with pytest.raises(ValueError, match="input_json must be a dict"):
            validate_manifest(self._valid(input_json=[1, 2, 3]))


class TestRunFixture:
    """Tests that actually call run_fixture; only run when FIXTURE_DIR exists."""

    def _manifest(self):
        return {
            "run_id": "integration-test-001",
            "kind": "old",
            "candidate_sha256": VALID_SHA,
            "entry_point": "run.py",
            "input_json": VALID_INPUT,
            "timeout_seconds": 30,
        }

    @pytest.mark.skipif(
        not FIXTURE_DIR.exists(),
        reason="FIXTURE_DIR not present in this environment",
    )
    def test_valid_manifest_returns_ok(self):
        result = run_fixture(self._manifest())
        assert result["status"] == "ok"
        assert result["run_id"] == "integration-test-001"
        assert "outputs" in result

    @pytest.mark.skipif(
        not FIXTURE_DIR.exists(),
        reason="FIXTURE_DIR not present in this environment",
    )
    def test_result_contains_conclusion(self):
        result = run_fixture(self._manifest())
        assert result["status"] == "ok"
        outputs = result["outputs"]
        assert "result" in outputs
        assert outputs["result"]["conclusion"] in ("above_threshold", "below_threshold")

    def test_mismatched_input_hash_is_rejected(self):
        manifest = self._manifest()
        manifest["candidate_sha256"] = "a" * 64
        with pytest.raises(ValueError, match="does not match"):
            run_fixture(manifest)


def test_worker_http_transport_returns_fixture_result():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/run"
        adapter = WorkerHttpRunnerAdapter(url, url)
        result = adapter.submit(RunManifest(
            run_id="http-test",
            kind="new",
            candidate_sha256=VALID_SHA,
            entry_point="run.py",
            input_json=VALID_INPUT,
        ))
        assert result.status == "ok"
        assert result.outputs["result"]["conclusion"] in ("above_threshold", "below_threshold")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
