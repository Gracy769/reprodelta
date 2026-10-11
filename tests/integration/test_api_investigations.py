"""Integration tests for the investigations API (Slice 3)."""
from __future__ import annotations

import pathlib

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.contract import FIXTURE_CONTRACT, hash_contract
from app.providers.runner import LocalRunnerAdapter

# ---------------------------------------------------------------------------
# Fixture: override the runner dependency with LocalRunnerAdapter
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend():
    return "asyncio"


def _fixture_dir() -> pathlib.Path:
    here = pathlib.Path(__file__).parent.parent.parent
    return here / "fixture" / "project"


@pytest.fixture
def app_with_local_runner():
    """Return the FastAPI app with the runner overridden to LocalRunnerAdapter."""
    from app.api.investigations import get_runner
    from app.main import app

    runner = LocalRunnerAdapter(fixture_dir=_fixture_dir())
    app.dependency_overrides[get_runner] = lambda: runner
    yield app
    app.dependency_overrides.clear()


DEMO_PROJECT_ID = "00000000-0000-0000-0000-000000000001"
VALID_INPUT = {"signal": [1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5]}


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------

async def test_list_projects_returns_200(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.get("/api/projects")
    assert response.status_code == 200


async def test_list_projects_contains_demo(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.get("/api/projects")
    projects = response.json()
    assert isinstance(projects, list)
    assert len(projects) == 1
    assert projects[0]["id"] == DEMO_PROJECT_ID


# ---------------------------------------------------------------------------
# GET /api/projects/{id}/contract
# ---------------------------------------------------------------------------

async def test_get_contract_returns_200(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/projects/{DEMO_PROJECT_ID}/contract")
    assert response.status_code == 200


async def test_get_contract_body(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/projects/{DEMO_PROJECT_ID}/contract")
    body = response.json()
    assert "contract" in body
    assert "contract_hash" in body
    assert len(body["contract_hash"]) == 64
    assert body["contract"]["schema_version"] == "1"


async def test_get_contract_hash_matches(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/projects/{DEMO_PROJECT_ID}/contract")
    body = response.json()
    assert body["contract_hash"] == hash_contract(FIXTURE_CONTRACT)


async def test_get_contract_unknown_project_returns_404(app_with_local_runner):
    bad_id = "00000000-0000-0000-0000-000000000099"
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/projects/{bad_id}/contract")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/projects/{id}/contract/approve
# ---------------------------------------------------------------------------

async def test_approve_contract_returns_200(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/projects/{DEMO_PROJECT_ID}/contract/approve")
    assert response.status_code == 200


async def test_approve_contract_response_body(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/projects/{DEMO_PROJECT_ID}/contract/approve")
    body = response.json()
    assert body["approved"] is True
    assert body["project_id"] == DEMO_PROJECT_ID
    assert len(body["contract_hash"]) == 64


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/run-pair — valid input
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _fixture_dir().exists(),
    reason="fixture directory not present in this environment",
)
async def test_run_pair_valid_input_returns_200(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/investigations/demo/run-pair",
            json={"input_json": VALID_INPUT},
        )
    assert response.status_code == 200


@pytest.mark.skipif(
    not _fixture_dir().exists(),
    reason="fixture directory not present in this environment",
)
async def test_run_pair_result_structure(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/investigations/demo/run-pair",
            json={"input_json": VALID_INPUT},
        )
    body = response.json()
    assert "old_run" in body
    assert "new_run" in body
    assert "comparison" in body
    assert "candidate_sha256" in body
    assert len(body["candidate_sha256"]) == 64


@pytest.mark.skipif(
    not _fixture_dir().exists(),
    reason="fixture directory not present in this environment",
)
async def test_run_pair_comparison_has_verdict(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/investigations/demo/run-pair",
            json={"input_json": VALID_INPUT},
        )
    body = response.json()
    cmp = body["comparison"]
    assert "verdict_hint" in cmp
    assert "conclusion_changed" in cmp


# ---------------------------------------------------------------------------
# POST /api/investigations/{id}/run-pair — invalid input → 422
# ---------------------------------------------------------------------------

async def test_run_pair_invalid_input_wrong_length_returns_422(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/investigations/demo/run-pair",
            json={"input_json": {"signal": [1.0, 2.0]}},
        )
    assert response.status_code == 422


async def test_run_pair_invalid_input_out_of_range_returns_422(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/investigations/demo/run-pair",
            json={"input_json": {"signal": [100.0] * 8}},
        )
    assert response.status_code == 422


async def test_run_pair_missing_field_returns_422(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/investigations/demo/run-pair",
            json={"input_json": {}},
        )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /investigations/new — HTML page
# ---------------------------------------------------------------------------

async def test_investigations_new_returns_200(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.get("/investigations/new")
    assert response.status_code == 200


async def test_investigations_new_is_html(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.get("/investigations/new")
    assert "text/html" in response.headers["content-type"]


async def test_investigations_new_contains_contract_info(app_with_local_runner):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_local_runner), base_url="http://test"
    ) as client:
        response = await client.get("/investigations/new")
    text = response.text
    assert "contract" in text.lower() or "signal" in text.lower()
