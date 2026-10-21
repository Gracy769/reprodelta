import uuid

from fastapi.testclient import TestClient

from app.api import project_profiles
from app.api.connections import current_user
from app.config import Settings
from app.domain.contract import (
    ConclusionRule,
    ContractBody,
    InputField,
    InputSchema,
    OutputSelector,
)
from app.domain.project_profile import ProjectProfileDraft
from app.main import app


def _draft():
    return ProjectProfileDraft(
        provider="github",
        repository="owner/repo",
        name="Numeric decision",
        adapter_module="analysis.reprodelta_adapter",
        adapter_function="run",
        dependency_path="requirements.txt",
        sample_input={"x": 0.5},
        source_transfer_consent=True,
        contract=ContractBody(
            input_schema=InputSchema(
                fields=[InputField(name="x", dtype="float64", shape=[], min_value=0, max_value=1)]
            ),
            output_selector=OutputSelector(field_name="measurement"),
            conclusion=ConclusionRule(operator="gt", threshold=0.5),
        ),
    )


def test_profile_draft_has_stable_digest_and_rejects_unsafe_paths():
    draft = _draft()
    assert len(draft.digest) == 64
    assert draft.digest == ProjectProfileDraft.model_validate(draft.model_dump()).digest
    try:
        ProjectProfileDraft.model_validate(
            {**draft.model_dump(), "dependency_path": "../secret.txt"}
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Traversal path was accepted")
    try:
        ProjectProfileDraft.model_validate(
            {**draft.model_dump(), "dependency_path": "README.md"}
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Unsupported manifest was accepted")


class _Profiles:
    def __init__(self):
        self.row = None

    def create(self, owner_id, draft):
        self.row = {
            "id": str(uuid.uuid4()),
            "owner_id": owner_id,
            "status": "draft",
            "draft_json": draft.model_dump(mode="json"),
            "profile_digest": draft.digest,
        }
        return self.row

    def list(self, owner_id):
        return [self.row] if self.row and self.row["owner_id"] == owner_id else []

    def get(self, owner_id, profile_id):
        return (
            self.row
            if self.row and self.row["owner_id"] == owner_id and self.row["id"] == str(profile_id)
            else None
        )

    def get_any(self, profile_id):
        return self.row if self.row and self.row["id"] == str(profile_id) else None

    def approve(self, owner_id, profile_id, approver, approval):
        if approval.profile_digest != self.row["profile_digest"]:
            raise ValueError("digest changed")
        self.row["status"] = "approved"
        return self.row


def test_profile_owner_access_and_operator_approval(monkeypatch):
    store = _Profiles()
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_profiles.get_profile_store] = lambda: store
    monkeypatch.setattr(
        project_profiles,
        "get_settings",
        lambda: Settings(
            profile_approver_subjects="github:approver",
            secret_key="x" * 40,
        ),
    )
    try:
        client = TestClient(app)
        response = client.post("/api/project-profiles", json=_draft().model_dump(mode="json"))
        assert response.status_code == 201
        profile_id = response.json()["id"]
        assert client.get(f"/api/project-profiles/{profile_id}").status_code == 200
        approval = {
            "profile_digest": response.json()["profile_digest"],
            "old": {
                "template_id": "old",
                "python_version": "3.12",
                "numpy_version": "1.26.4",
                "lock_sha256": "a" * 64,
            },
            "new": {
                "template_id": "new",
                "python_version": "3.12",
                "numpy_version": "2.0.2",
                "lock_sha256": "b" * 64,
            },
            "review_note": "Prepared images and approved adapter reviewed",
        }
        assert (
            client.post(f"/api/project-profiles/{profile_id}/approve", json=approval).status_code
            == 403
        )
        app.dependency_overrides[current_user] = lambda: "github:approver"
        assert (
            client.post(f"/api/project-profiles/{profile_id}/approve", json=approval).status_code
            == 200
        )
    finally:
        app.dependency_overrides.clear()


def test_eligibility_does_not_read_another_owners_connection(monkeypatch):
    store = _Profiles()
    profile_id = uuid.uuid4()
    store.row = {"id": str(profile_id), "owner_id": "github:someone-else",
                 "provider": "github", "repository": "owner/repo", "status": "approved"}
    app.dependency_overrides[current_user] = lambda: "github:owner"
    app.dependency_overrides[project_profiles.get_profile_store] = lambda: store

    def unexpected(*args):
        raise AssertionError("Source connection must not be read for another owner")

    monkeypatch.setattr(project_profiles, "_saved_token", unexpected)
    try:
        response = TestClient(app).get(
            f"/api/project-profiles/{profile_id}/changes/1/eligibility"
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
