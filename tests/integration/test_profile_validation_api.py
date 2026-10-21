"""Validation requests stay operator-scoped and disabled until the gate opens."""

import uuid

from fastapi.testclient import TestClient

from app.api import project_profiles
from app.api.connections import current_user
from app.config import Settings
from app.domain.profile_validation import pilot_lock_digest
from app.main import app
from tests.integration.test_project_profiles import _draft


class Profiles:
    def __init__(self):
        draft = _draft()
        self.row = {
            "id": str(uuid.uuid4()), "owner_id": "github:owner", "status": "draft",
            "provider": draft.provider, "repository": draft.repository,
            "draft_json": draft.model_dump(mode="json"),
            "profile_digest": draft.digest, "contract_hash": draft.contract_hash,
        }

    def get_any(self, profile_id):
        return self.row if self.row["id"] == str(profile_id) else None


class Queue:
    def __init__(self):
        self.submitted = None

    def submit_validation(self, owner, key, spec):
        self.submitted = (owner, key, spec)
        return {"id": str(uuid.uuid4()), "owner_id": owner, "lease_token": None,
                "idempotency_key": key, "job_kind": "profile_validation",
                "status": "queued", "spec_json": spec.model_dump(mode="json")}


def _body():
    return {
        "source_sha": "a" * 40,
        "old": {"template_id": "old", "python_version": "3.12", "numpy_version": "1.26.4",
                "lock_sha256": pilot_lock_digest("1.26.4")},
        "new": {"template_id": "new", "python_version": "3.12", "numpy_version": "2.0.2",
                "lock_sha256": pilot_lock_digest("2.0.2")},
        "idempotency_key": "validation-123",
    }


def test_validation_requires_operator_and_feature_gate(monkeypatch):
    profiles, queue = Profiles(), Queue()
    app.dependency_overrides[project_profiles.get_profile_store] = lambda: profiles
    app.dependency_overrides[project_profiles.get_validation_queue] = lambda: queue
    app.dependency_overrides[current_user] = lambda: "github:owner"
    monkeypatch.setattr(project_profiles, "get_settings", lambda: Settings(
        e2b_profile_validation_enabled=True, profile_approver_subjects="github:approver",
        e2b_sandbox_usd_per_second=0.000046, e2b_project_run_cost_ceiling_usd=0.1,
    ))
    try:
        client = TestClient(app)
        url = f"/api/project-profiles/{profiles.row['id']}/validations"
        assert client.post(url, json=_body()).status_code == 403
        app.dependency_overrides[current_user] = lambda: "github:approver"
        response = client.post(url, json=_body())
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert "idempotency_key" not in response.json()
        assert queue.submitted[0] == "github:owner"
        assert queue.submitted[2].old.lock_sha256 == pilot_lock_digest("1.26.4")
    finally:
        app.dependency_overrides.clear()


def test_validation_rejects_unreviewed_environment_lock(monkeypatch):
    profiles, queue = Profiles(), Queue()
    app.dependency_overrides[project_profiles.get_profile_store] = lambda: profiles
    app.dependency_overrides[project_profiles.get_validation_queue] = lambda: queue
    app.dependency_overrides[current_user] = lambda: "github:approver"
    monkeypatch.setattr(project_profiles, "get_settings", lambda: Settings(
        e2b_profile_validation_enabled=True, profile_approver_subjects="github:approver",
        e2b_sandbox_usd_per_second=0.000046, e2b_project_run_cost_ceiling_usd=0.1,
    ))
    try:
        body = _body()
        body["old"]["lock_sha256"] = "f" * 64
        response = TestClient(app).post(
            f"/api/project-profiles/{profiles.row['id']}/validations", json=body
        )
        assert response.status_code == 409
        assert queue.submitted is None
    finally:
        app.dependency_overrides.clear()
