"""Owner admission requires exact review approval and keeps lease tokens private."""

import uuid

from fastapi.testclient import TestClient

from app.api import pages, project_runs
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
        self.worker_available = True

    def has_live_worker(self, kind):
        assert kind == "investigation"
        return self.worker_available

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

    async def planned(*_args, **kwargs):
        return {"rate_configured": True}, spec.model_copy(update={
            "candidate_budget": kwargs["candidate_budget"],
            "candidate_strategy": kwargs["candidate_strategy"],
            "candidate_seed": kwargs["candidate_seed"],
        })

    monkeypatch.setattr(project_runs, "_plan", planned)
    try:
        client = TestClient(app)
        body = _body(spec)
        queue.worker_available = False
        assert client.post("/api/project-runs", json=body).status_code == 503
        assert queue.submissions == []
        queue.worker_available = True
        assert client.post("/api/project-runs", json={**body,
               "source_transfer_approved": False}).status_code == 422
        assert client.post("/api/project-runs", json={**body,
               "expected_head_sha": "f" * 40}).status_code == 409
        assert queue.submissions == []
        response = client.post("/api/project-runs", json={
            **body, "candidate_budget": 8, "candidate_strategy": "seeded_random",
            "candidate_seed": 17,
        })
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert "lease_token" not in response.json()
        assert "idempotency_key" not in response.json()
        assert queue.submissions[0][0] == "github:owner"
        submitted = queue.submissions[0][2]
        assert (submitted.candidate_budget, submitted.candidate_strategy,
                submitted.candidate_seed) == (8, "seeded_random", 17)
    finally:
        app.dependency_overrides.clear()


def test_plan_hides_start_when_worker_is_offline(monkeypatch):
    queue = Queue()
    queue.worker_available = False
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_runs.get_profile_store] = lambda: object()
    app.dependency_overrides[project_runs.get_run_queue] = lambda: queue

    async def planned(*_args, **_kwargs):
        return {"execution_enabled": True, "rate_configured": True}, None

    monkeypatch.setattr(project_runs, "_plan", planned)
    try:
        response = TestClient(app).get(
            f"/api/project-runs/profiles/{uuid.uuid4()}/changes/4/plan"
        )
        assert response.status_code == 200
        assert response.json()["feature_enabled"] is True
        assert response.json()["worker_available"] is False
        assert response.json()["execution_enabled"] is False
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


def test_saved_revision_check_is_owner_scoped_and_read_only(monkeypatch):
    spec = _spec()
    job_id = uuid.uuid4()
    calls = []

    class SavedQueue(Queue):
        def get(self, owner, requested):
            calls.append((owner, requested))
            return ({"id": str(job_id), "spec_json": spec.model_dump(mode="json")}
                    if owner == "github:owner" and requested == job_id else None)

    async def refs(_get, provider, token, repository, number):
        assert (provider, token, repository, number) == (
            "github", "host-token", "owner/repo", spec.change_number
        )
        return spec.base_sha, "f" * 40

    monkeypatch.setattr(project_runs, "_saved_token", lambda owner, provider: "host-token")
    monkeypatch.setattr(project_runs, "change_refs", refs)
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_runs.get_run_queue] = lambda: SavedQueue()
    try:
        response = TestClient(app).get(f"/api/project-runs/{job_id}/revision")
        assert response.status_code == 200
        assert response.json()["changed"] is True
        assert response.json()["saved_head_sha"] == spec.head_sha
        assert "host-token" not in response.text
        assert calls == [("github:owner", job_id)]
        app.dependency_overrides[current_user] = lambda: "github:other"
        assert TestClient(app).get(f"/api/project-runs/{job_id}/revision").status_code == 404
        assert calls[-1] == ("github:other", job_id)
    finally:
        app.dependency_overrides.clear()


def test_redacted_share_requires_owner_consent_and_can_be_revoked(monkeypatch):
    job_id, share_id = uuid.uuid4(), uuid.uuid4()
    token = "A" * 43

    class SavedQueue(Queue):
        def get(self, owner, requested):
            if owner != "github:owner" or requested != job_id:
                return None
            return {"id": str(job_id), "status": "completed", "result_json": {
                "kind": "approved_project_sample", "changed_conclusion": True,
                "input_sha256": "private-input-marker", "source_sha256": "private-source-marker",
                "old": {"ratio": 0.125}, "new": {"ratio": 0.875},
            }}

    class Shares:
        copy = None
        revoked = False

        def create(self, owner, requested, copy):
            assert owner == "github:owner" and requested == job_id
            self.copy = copy
            return {"id": str(share_id), "token": token, "created_at": "now"}

        def public(self, value):
            return self.copy if value == token and not self.revoked else None

        def list(self, owner, requested):
            assert owner == "github:owner" and requested == job_id
            return [{"id": str(share_id), "revoked_at": None}]

        def revoke(self, owner, requested, target):
            if (owner, requested, target) != ("github:owner", job_id, share_id):
                return False
            self.revoked = True
            return True

    shares = Shares()
    monkeypatch.setattr(pages, "ProjectShareStore", lambda _dsn: shares)
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_runs.get_run_queue] = lambda: SavedQueue()
    app.dependency_overrides[project_runs.get_share_store] = lambda: shares
    try:
        client = TestClient(app)
        base = f"/api/project-runs/{job_id}"
        assert client.post(base + "/shares", json={
            "acknowledge_public_share": False}).status_code == 422
        preview = client.get(base + "/share-preview")
        assert preview.status_code == 200
        assert "private-input-marker" not in preview.text
        assert "0.125" not in preview.text
        created = client.post(base + "/shares", json={"acknowledge_public_share": True})
        assert created.status_code == 201
        assert shares.copy == preview.json()
        url = created.json()["url"]
        assert url == f"/shared/{token}"
        data_url = f"/api/project-runs/shares/{token}"
        app.dependency_overrides[current_user] = lambda: "github:other"
        assert client.get(base + "/share-preview").status_code == 404
        assert client.get(base + "/shares").status_code == 404
        assert client.delete(base + f"/shares/{share_id}").status_code == 404
        public = client.get(data_url)
        assert public.status_code == 200
        assert public.headers["cache-control"] == "no-store"
        assert public.headers["x-robots-tag"] == "noindex"
        page = client.get(url, headers={"accept": "text/html"})
        assert page.status_code == 200
        assert "A changed conclusion was observed" in page.text
        assert "private-input-marker" not in page.text
        assert page.headers["x-robots-tag"] == "noindex"
        app.dependency_overrides[current_user] = lambda: "github:owner"
        assert client.delete(base + f"/shares/{share_id}").status_code == 204
        assert client.get(data_url).status_code == 404
        assert client.get(url, headers={"accept": "text/html"}).status_code == 404
    finally:
        app.dependency_overrides.clear()
