"""Owner consent and one-call reservation protect the model suggestion path."""

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import project_runs
from app.api.connections import current_user
from app.config import Settings
from app.main import app
from tests.unit.test_project_hypothesis import _job


class Queue:
    def __init__(self, job_id):
        self.job_id = job_id

    def get(self, owner, job_id):
        return _job() if owner == "github:owner" and job_id == self.job_id else None


class Store:
    def __init__(self):
        self.record = None

    def get(self, owner, job_id):
        return self.record if owner == "github:owner" else None

    def reserve(self, owner, job_id, *, summary, model_id, ceiling_usd):
        if self.record is not None:
            return self.record, False
        self.record = {"status": "pending", "summary_json": summary,
                       "model_id": model_id, "reserved_model_cost_usd": ceiling_usd}
        return self.record, True

    def complete(self, owner, job_id, *, request_digest, response_text, usage):
        self.record.update({"status": "completed", "request_digest": request_digest,
                            "response_text": response_text, "usage_json": usage,
                            "prompt_version": "project_hypothesis_v1"})
        return self.record

    def fail(self, owner, job_id):
        self.record["status"] = "failed"
        return self.record


def test_owner_preview_consent_duplicate_and_private_data_boundary(monkeypatch):
    job_id = uuid.uuid4()
    store = Store()
    sent = []
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_runs.get_run_queue] = lambda: Queue(job_id)
    app.dependency_overrides[project_runs.get_hypothesis_store] = lambda: store
    monkeypatch.setattr(project_runs, "get_settings", lambda: Settings(
        project_hypotheses_enabled=True, nebius_api_key="test-key",
        nebius_model_id="nvidia/test", nebius_call_cost_ceiling_usd=0.01,
    ))

    def fake_model(summary, settings):
        sent.append(summary)
        return SimpleNamespace(is_mock=False, model_id=settings.nebius_model_id,
                               request_digest="d" * 64,
                               response_text="Untested: inspect intermediate dtype.",
                               usage={"total_tokens": 40, "unexpected": "secret"})

    monkeypatch.setattr(project_runs, "_request_project_hypothesis", fake_model)
    try:
        client = TestClient(app)
        base = f"/api/project-runs/{job_id}"
        assert client.get(base + "/model-hypothesis").status_code == 404
        preview = client.get(base + "/model-summary")
        assert preview.status_code == 200 and preview.json()["enabled"] is True
        assert "private-repo" not in preview.text and "private_marker" not in preview.text
        assert client.post(base + "/model-hypothesis", json={
            "acknowledge_numeric_summary_transfer": False}).status_code == 422
        assert not sent
        first = client.post(base + "/model-hypothesis", json={
            "acknowledge_numeric_summary_transfer": True})
        assert first.status_code == 202
        assert first.json()["status"] == "completed"
        assert first.json()["usage_json"] == {"total_tokens": 40}
        assert client.post(base + "/model-hypothesis", json={
            "acknowledge_numeric_summary_transfer": True}).status_code == 202
        assert len(sent) == 1
        assert "private-repo" not in str(sent[0])
        app.dependency_overrides[current_user] = lambda: "github:other"
        assert client.get(base + "/model-summary").status_code == 404
        assert client.get(base + "/model-hypothesis").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_model_feature_disabled_does_not_reserve_or_call(monkeypatch):
    job_id = uuid.uuid4()
    store = Store()
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_runs.get_run_queue] = lambda: Queue(job_id)
    app.dependency_overrides[project_runs.get_hypothesis_store] = lambda: store
    monkeypatch.setattr(project_runs, "get_settings", lambda: Settings(
        project_hypotheses_enabled=False, nebius_api_key="test-key",
        nebius_call_cost_ceiling_usd=0.01,
    ))
    try:
        response = TestClient(app).post(f"/api/project-runs/{job_id}/model-hypothesis", json={
            "acknowledge_numeric_summary_transfer": True})
        assert response.status_code == 503
        assert store.record is None
    finally:
        app.dependency_overrides.clear()


def test_failed_model_call_does_not_repeat_or_change_finding(monkeypatch):
    job_id = uuid.uuid4()
    store = Store()
    calls = []
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_runs.get_run_queue] = lambda: Queue(job_id)
    app.dependency_overrides[project_runs.get_hypothesis_store] = lambda: store
    monkeypatch.setattr(project_runs, "get_settings", lambda: Settings(
        project_hypotheses_enabled=True, nebius_api_key="test-key",
        nebius_call_cost_ceiling_usd=0.01,
    ))

    def failed_model(_summary, _settings):
        calls.append(1)
        raise RuntimeError("provider secret and request body must stay private")

    monkeypatch.setattr(project_runs, "_request_project_hypothesis", failed_model)
    try:
        client = TestClient(app)
        path = f"/api/project-runs/{job_id}/model-hypothesis"
        first = client.post(path, json={"acknowledge_numeric_summary_transfer": True})
        assert first.status_code == 503
        assert "provider secret" not in first.text
        second = client.post(path, json={"acknowledge_numeric_summary_transfer": True})
        assert second.status_code == 202 and second.json()["status"] == "failed"
        assert len(calls) == 1
        assert client.get(f"/api/project-runs/{job_id}").json()["result_json"]["changed_conclusion"] is True
    finally:
        app.dependency_overrides.clear()
