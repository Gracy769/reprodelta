"""Owner admission requires exact review approval and keeps lease tokens private."""

import uuid

from fastapi.testclient import TestClient

from app.api import project_runs
from app.api.connections import current_user
from app.config import Settings
from app.domain.project_run import ProjectRunSpec
from app.main import app


def _spec():
    return ProjectRunSpec(
        profile_id=uuid.uuid4(), provider="github", repository="owner/repo",
        change_number=4, base_sha="a" * 40, head_sha="b" * 40,
        source_sha256="c" * 64, profile_digest="d" * 64,
        contract_hash="e" * 64, old_environment_lock_sha256="f" * 64,
        new_environment_lock_sha256="0" * 64,
        sandbox_usd_per_second=0.0001, max_cost_usd=0.2,
    )


class Queue:
    def __init__(self):
        self.submissions = []

    def submit(self, owner, key, spec):
        self.submissions.append((owner, key, spec))
        return {"id": str(uuid.uuid4()), "status": "queued", "owner_id": owner,
                "idempotency_key": key, "lease_token": "worker-secret",
                "spec_json": spec.model_dump(mode="json")}

    def get(self, owner, job_id):
        return None

    def cancel(self, owner, job_id):
        return None


def _body(spec):
    return {
        "profile_id": str(spec.profile_id), "change_number": spec.change_number,
        "expected_base_sha": spec.base_sha, "expected_head_sha": spec.head_sha,
        "approved_contract_hash": spec.contract_hash,
        "approved_cost_ceiling_usd": spec.max_cost_usd,
        "source_transfer_approved": True, "idempotency_key": "safe-key-123",
    }


def test_start_requires_exact_approval_and_hides_worker_capability(monkeypatch):
    spec = _spec()
    queue = Queue()
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_runs.get_profile_store] = lambda: object()
    app.dependency_overrides[project_runs.get_run_queue] = lambda: queue
    monkeypatch.setattr(
        project_runs, "get_settings", lambda: Settings(
            e2b_project_runs_enabled=True, e2b_sandbox_usd_per_second=0.0001,
            e2b_project_run_cost_ceiling_usd=0.2,
        )
    )

    async def planned(*_args, **_kwargs):
        return {"rate_configured": True}, spec

    monkeypatch.setattr(project_runs, "_plan", planned)
    try:
        client = TestClient(app)
        body = _body(spec)
        assert client.post("/api/project-runs", json={**body,
               "source_transfer_approved": False}).status_code == 422
        assert client.post("/api/project-runs", json={**body,
               "expected_head_sha": "f" * 40}).status_code == 409
        assert queue.submissions == []
        response = client.post("/api/project-runs", json=body)
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert "lease_token" not in response.json()
        assert "idempotency_key" not in response.json()
        assert queue.submissions[0][0] == "github:owner"
    finally:
        app.dependency_overrides.clear()


def test_disabled_start_and_other_owner_status_do_not_read_sources(monkeypatch):
    queue = Queue()
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_runs.get_profile_store] = lambda: object()
    app.dependency_overrides[project_runs.get_run_queue] = lambda: queue
    monkeypatch.setattr(
        project_runs, "get_settings", lambda: Settings(e2b_project_runs_enabled=False)
    )

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Disabled run accessed a source host")

    monkeypatch.setattr(project_runs, "_plan", forbidden)
    try:
        client = TestClient(app)
        assert client.post("/api/project-runs", json=_body(_spec())).status_code == 503
        assert client.get(f"/api/project-runs/{uuid.uuid4()}").status_code == 404
        assert client.post(f"/api/project-runs/{uuid.uuid4()}/cancel").status_code == 404
    finally:
        app.dependency_overrides.clear()
