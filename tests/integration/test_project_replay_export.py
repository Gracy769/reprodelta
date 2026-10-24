"""Portable owner-only approved-project witness export and replay checks."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api import project_runs
from app.api.connections import current_user
from app.domain.profile_validation import pilot_lock_digest
from app.domain.project_profile import PreparedEnvironment, ProfileApproval
from app.domain.project_run import ProjectRunSpec
from app.main import app
from app.orchestrator import project_replay_script
from app.orchestrator.project_replay_export import build_project_replay_bundle
from app.providers.e2b_runner import validate_snapshot
from tests.integration.test_project_profiles import _draft


def _fixture():
    draft = _draft()
    files = {
        "requirements.txt": b"numpy==1.26.4\n",
        "analysis/reprodelta_adapter.py": (
            b"def run(data):\n"
            b"    import numpy as np\n"
            b"    return {'measurement': 0.4 if np.__version__.startswith('1.') else 0.6}\n"
        ),
    }
    old = PreparedEnvironment(
        template_id="old", python_version="3.12", numpy_version="1.26.4",
        lock_sha256=pilot_lock_digest("1.26.4"),
    )
    new = PreparedEnvironment(
        template_id="new", python_version="3.12", numpy_version="2.0.2",
        lock_sha256=pilot_lock_digest("2.0.2"),
    )
    profile_id = uuid.uuid4()
    approval = ProfileApproval(
        profile_digest=draft.digest, validation_job_id=uuid.uuid4(),
        old=old, new=new, review_note="Operator approved this exact profile",
    )
    profile = {
        "id": str(profile_id), "status": "approved", "provider": draft.provider,
        "repository": draft.repository, "draft_json": draft.model_dump(mode="json"),
        "profile_digest": draft.digest, "contract_hash": draft.contract_hash,
        "approval_json": approval.model_dump(mode="json"),
    }
    spec = ProjectRunSpec(
        profile_id=profile_id, provider=draft.provider, repository=draft.repository,
        change_number=1, base_sha="a" * 40, head_sha="b" * 40,
        source_sha256=validate_snapshot(files), profile_digest=draft.digest,
        contract_hash=draft.contract_hash,
        old_environment_lock_sha256=old.lock_sha256,
        new_environment_lock_sha256=new.lock_sha256,
        sandbox_usd_per_second=0.000046, max_cost_usd=0.1,
    )
    witness = draft.sample_input
    input_sha = hashlib.sha256(json.dumps(witness, sort_keys=True).encode()).hexdigest()
    result = {
        "kind": "approved_project_sample", "scope": "one approved sample input",
        "changed_conclusion": True, "repeatable": True, "inconclusive": False,
        "witness_input": witness, "input_sha256": input_sha,
        "old": {"ratio": 0.4, "conclusion": "below_threshold", "dtype": None},
        "old_repeat": {"ratio": 0.4, "conclusion": "below_threshold", "dtype": None},
        "new": {"ratio": 0.6, "conclusion": "above_threshold", "dtype": None},
        "new_repeat": {"ratio": 0.6, "conclusion": "above_threshold", "dtype": None},
        "source_sha256": spec.source_sha256, "contract_hash": spec.contract_hash,
        "base_sha": spec.base_sha, "head_sha": spec.head_sha,
        "environment_lock_sha256": [old.lock_sha256, new.lock_sha256],
    }
    job = {
        "id": str(uuid.uuid4()), "owner_id": "github:owner", "job_kind": "investigation",
        "status": "completed", "spec_json": spec.model_dump(mode="json"),
        "result_json": result,
    }
    return job, profile, files


def test_private_replay_bundle_is_deterministic_and_script_matches_record(monkeypatch, tmp_path, capsys):
    job, profile, files = _fixture()
    bundle = build_project_replay_bundle(job, profile, files)
    assert bundle == build_project_replay_bundle(job, profile, files)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert "source/analysis/reprodelta_adapter.py" in archive.namelist()
        assert "source/requirements.txt" in archive.namelist()
        assert "replay.py" in archive.namelist()
        assert "PRIVATE" not in archive.read("record.json").decode()
        archive.extractall(tmp_path)
    monkeypatch.setattr(project_replay_script, "ROOT", tmp_path)
    monkeypatch.setattr(project_replay_script, "_runtime", lambda *_: None)
    monkeypatch.setenv("REPRODELTA_OLD_PYTHON", "old-python")
    monkeypatch.setenv("REPRODELTA_NEW_PYTHON", "new-python")

    def child(args, **_kwargs):
        ratio = 0.4 if args[0] == "old-python" else 0.6
        return json.dumps({"result": {"measurement": ratio}}).encode()

    monkeypatch.setattr(project_replay_script, "_run_bounded", child)
    project_replay_script.main()
    assert "Replay matched" in capsys.readouterr().out
    (tmp_path / "input.json").write_text('{"x":0.9}', encoding="utf-8")
    with pytest.raises(AssertionError, match="Bundle file changed"):
        project_replay_script.main()


def test_replay_subprocess_rejects_output_flood_without_retaining_it():
    with pytest.raises(AssertionError, match="output exceeds its limit"):
        project_replay_script._run_bounded(
            [sys.executable, "-I", "-c", "print('x' * 200000)"],
            timeout=5,
        )
    assert project_replay_script._run_bounded(
        [sys.executable, "-I", "-c", "print('small result')"],
        timeout=5,
    ).strip() == b"small result"
    with pytest.raises(TimeoutError, match="time limit"):
        project_replay_script._run_bounded(
            [sys.executable, "-I", "-c", "import time; time.sleep(10)"],
            timeout=1,
        )


@pytest.mark.parametrize("tamper", ["missing_witness", "source", "contract", "repeat"])
def test_replay_export_rejects_missing_or_inconsistent_evidence(tamper):
    job, profile, files = _fixture()
    if tamper == "missing_witness":
        job["result_json"]["witness_input"] = None
    elif tamper == "source":
        files["requirements.txt"] = b"numpy==2.0.2\n"
    elif tamper == "contract":
        job["result_json"]["contract_hash"] = "0" * 64
    else:
        job["result_json"]["old_repeat"]["conclusion"] = "above_threshold"
    with pytest.raises(ValueError):
        build_project_replay_bundle(job, profile, files)


def test_private_replay_endpoint_is_owner_scoped(monkeypatch):
    job, profile, files = _fixture()

    class Queue:
        def get(self, owner, job_id):
            return job if owner == "github:owner" and str(job_id) == job["id"] else None

    class Profiles:
        def get(self, owner, profile_id):
            return profile if owner == "github:owner" and str(profile_id) == profile["id"] else None

    async def snapshot(*_args):
        return files, validate_snapshot(files)

    monkeypatch.setattr(project_runs, "immutable_snapshot", snapshot)
    monkeypatch.setattr(project_runs, "_saved_token", lambda *_: "test-only-token")
    app.dependency_overrides[project_runs.get_run_queue] = lambda: Queue()
    app.dependency_overrides[project_runs.get_profile_store] = lambda: Profiles()
    try:
        app.dependency_overrides[current_user] = lambda: "github:stranger"
        client = TestClient(app)
        assert client.get(f"/api/project-runs/{job['id']}/replay.zip").status_code == 404
        app.dependency_overrides[current_user] = lambda: "github:owner"
        response = client.get(f"/api/project-runs/{job['id']}/replay.zip")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-reprodelta-zip-sha256"] == hashlib.sha256(
            response.content
        ).hexdigest()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.read("source/requirements.txt") == files["requirements.txt"]
        def malformed(*_args):
            raise KeyError("private-profile-field")
        monkeypatch.setattr(project_runs, "build_project_replay_bundle", malformed)
        rejected = client.get(f"/api/project-runs/{job['id']}/replay.zip")
        assert rejected.status_code == 409
        assert rejected.json()["detail"] == "Saved evidence cannot be exported as a replay bundle"
        assert "private-profile-field" not in rejected.text
    finally:
        app.dependency_overrides.clear()
