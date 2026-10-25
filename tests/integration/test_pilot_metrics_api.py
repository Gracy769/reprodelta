"""Pilot aggregates are visible only to configured approvers."""

from fastapi.testclient import TestClient

from app.api import project_profiles
from app.api.connections import current_user
from app.config import Settings
from app.main import app


class Metrics:
    def __init__(self):
        self.calls = 0

    def read(self):
        self.calls += 1
        return {"profiles": {"created": 2, "approved": 1},
                "investigations": {"completed": 1}, "failure_codes": []}


def test_operator_metrics_reject_non_approver_before_storage(monkeypatch):
    metrics = Metrics()
    monkeypatch.setattr(project_profiles, "get_settings", lambda: Settings(
        profile_approver_subjects="github:approver",
    ))
    app.dependency_overrides[current_user] = lambda: "github:other"
    app.dependency_overrides[project_profiles.get_pilot_metrics] = lambda: metrics
    try:
        response = TestClient(app).get("/api/project-profiles/operator/pilot-metrics")
        assert response.status_code == 403
        assert metrics.calls == 0
    finally:
        app.dependency_overrides.clear()


def test_operator_metrics_are_aggregate_and_not_cached(monkeypatch):
    metrics = Metrics()
    monkeypatch.setattr(project_profiles, "get_settings", lambda: Settings(
        profile_approver_subjects="github:approver",
    ))
    app.dependency_overrides[current_user] = lambda: "github:approver"
    app.dependency_overrides[project_profiles.get_pilot_metrics] = lambda: metrics
    try:
        response = TestClient(app).get("/api/project-profiles/operator/pilot-metrics")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["investigations"]["completed"] == 1
        assert metrics.calls == 1
    finally:
        app.dependency_overrides.clear()
