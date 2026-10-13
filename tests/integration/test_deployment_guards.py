"""Ownership, concurrency, persisted budgets and pinned demonstration evidence."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.types import BudgetConfig
from app.orchestrator.budget import BudgetExceeded, BudgetLedger, reserve
from app.providers.runner import LocalRunnerAdapter
from app.storage.investigation_store import FileSystemInvestigationStore, MemoryInvestigationStore

PROJECT = "00000000-0000-0000-0000-000000000001"
KEY = "test-secret-" * 4


def token(owner, expired=False):
    return jwt.encode(
        {
            "sub": "firebase:" + owner,
            "name": owner,
            "iss": "reprodelta",
            "aud": "reprodelta",
            "exp": datetime.now(UTC) + timedelta(seconds=-1 if expired else 600),
        },
        KEY,
        algorithm="HS256",
    )


@pytest.fixture
def guarded_app(monkeypatch):
    from app.api import investigations as api
    from app.config import get_settings
    from app.main import app

    monkeypatch.setattr(get_settings(), "secret_key", KEY)
    monkeypatch.setattr(get_settings(), "app_url", None)
    monkeypatch.setattr(api, "_store", MemoryInvestigationStore())
    monkeypatch.setattr(api, "_investigations", {})
    monkeypatch.setattr(api, "_investigation_state", {})
    app.dependency_overrides[api.get_runner] = lambda: LocalRunnerAdapter()
    yield app
    app.dependency_overrides.clear()


async def test_all_private_routes_deny_other_owner_and_anonymous(guarded_app):
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + token("alice")},
    ) as alice:
        created = await alice.post("/api/investigations", json={"project_id": PROJECT})
        inv = created.json()["id"]
        assert (await alice.get(f"/api/investigations/{inv}")).status_code == 200
    for headers in ({}, {"Authorization": "Bearer " + token("bob")}):
        async with AsyncClient(
            transport=ASGITransport(app=guarded_app), base_url="http://test", headers=headers
        ) as stranger:
            for suffix in ("", "/progress", "/evidence", "/report", "/report/text", "/stream"):
                assert (await stranger.get(f"/api/investigations/{inv}{suffix}")).status_code == 404
            for suffix, body in (
                ("/start", {}),
                ("/run-pair", {"input_json": {"signal": [1.0] * 8}}),
                ("/verify-patch", {"diff_text": "invalid"}),
            ):
                assert (
                    await stranger.post(f"/api/investigations/{inv}{suffix}", json=body)
                ).status_code == 404


async def test_cookie_auth_and_expired_bearer(guarded_app):
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app),
        base_url="http://test",
        cookies={"rd_session": token("alice")},
    ) as client:
        inv = (await client.post("/api/investigations", json={"project_id": PROJECT})).json()["id"]
        assert (await client.get(f"/api/investigations/{inv}")).status_code == 200
        response = await client.get(
            f"/api/investigations/{inv}",
            headers={"Authorization": "Bearer " + token("alice", True)},
        )
        assert response.status_code == 401


async def test_guest_cookie_isolated_and_csrf_rejected(guarded_app):
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app), base_url="http://test"
    ) as client:
        inv = (await client.post("/api/investigations", json={"project_id": PROJECT})).json()["id"]
        assert "rd_demo" in client.cookies
        assert (await client.get(f"/api/investigations/{inv}")).status_code == 200
        assert (
            await client.post(
                f"/api/investigations/{inv}/run-pair",
                json={"input_json": {"signal": [1.0] * 8}},
                headers={"Origin": "https://attacker.example"},
            )
        ).status_code == 403


async def test_pair_cap_survives_repeated_requests(guarded_app):
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app), base_url="http://test"
    ) as client:
        inv = (
            await client.post(
                "/api/investigations",
                json={"project_id": PROJECT, "max_candidates": 1, "max_runs": 2},
            )
        ).json()["id"]
        body = {"input_json": {"signal": [1.0] * 8}}
        assert (
            await client.post(f"/api/investigations/{inv}/run-pair", json=body)
        ).status_code == 200
        assert (
            await client.post(f"/api/investigations/{inv}/run-pair", json=body)
        ).status_code == 429


@pytest.mark.parametrize("persistent", [False, True])
def test_atomic_budget_caps_and_restart(tmp_path, persistent):
    store = FileSystemInvestigationStore(tmp_path) if persistent else MemoryInvestigationStore()
    inv = store.create_investigation(PROJECT, {"max_runs": 3}, "firebase:alice")

    def attempt(_):
        try:
            store.reserve_budget(inv, runs=1)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(20))) == 3
    restored = FileSystemInvestigationStore(tmp_path) if persistent else store
    assert restored.get_investigation(inv)["owner_id"] == "firebase:alice"
    with pytest.raises(BudgetExceeded):
        restored.reserve_budget(inv, runs=1)


def test_model_cost_tokens_calls_and_wall_limits():
    with pytest.raises(BudgetExceeded, match="model_calls"):
        reserve({"model_calls": 2}, {"max_llm_calls": 2}, model_calls=1)
    with pytest.raises(BudgetExceeded, match="cost"):
        reserve({}, {"max_cost_usd": 0.01}, cost_usd=0.02)
    with pytest.raises(BudgetExceeded, match="tokens"):
        reserve({}, {"max_model_tokens": 2}, tokens=3)
    with pytest.raises(BudgetExceeded, match="wall"):
        reserve(
            {"started_at": (datetime.now(UTC) - timedelta(seconds=10)).isoformat()},
            {"max_wall_seconds": 1},
            runs=1,
        )


def test_mock_model_call_cap_is_enforced():
    from app.providers.nebius import NebiusClient

    client = NebiusClient(api_key=None)
    client.budget_ledger = BudgetLedger(BudgetConfig(max_llm_calls=1))
    asyncio.run(client.propose_explanation("witness", "versions"))
    with pytest.raises(BudgetExceeded):
        asyncio.run(client.propose_explanation("witness", "versions"))


def test_postgres_ownership_budget_restart():
    dsn = os.environ.get("REPRODELTA_TEST_POSTGRES")
    if not dsn:
        pytest.skip("Dedicated CI Postgres not configured")
    from app.storage.postgres_investigation_store import PostgresInvestigationStore

    store = PostgresInvestigationStore(dsn)
    inv = store.create_investigation(PROJECT, {"max_runs": 2}, "firebase:alice")
    store.reserve_budget(inv, runs=2)
    store.transition_status(inv, "running")
    restored = PostgresInvestigationStore(dsn)
    assert restored.get_investigation(inv)["owner_id"] == "firebase:alice"
    assert restored.get_investigation(inv)["status"] == "interrupted"
    with pytest.raises(BudgetExceeded):
        restored.reserve_budget(inv, runs=1)


def test_full_investigation_cannot_bypass_cap_in_later_stages():
    from app.domain.contract import FIXTURE_CONTRACT
    from app.orchestrator.investigation import InvestigationRunner

    class CountingRunner(LocalRunnerAdapter):
        calls = 0

        def submit(self, manifest):
            self.calls += 1
            return super().submit(manifest)

    runner = CountingRunner()
    evidence = InvestigationRunner(
        FIXTURE_CONTRACT, BudgetConfig(max_candidates=1, max_runs=4), runner
    ).run_full_investigation("bounded")
    assert runner.calls == 4
    assert evidence.verdict.value == "inconclusive"
    assert evidence.demonstration_comparison["status"] == "budget_exhausted"
    assert evidence.budget_usage["runs"] == 4


async def test_one_owner_cannot_use_another_owners_approval(guarded_app):
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + token("alice")},
    ) as alice:
        assert (await alice.post(f"/api/projects/{PROJECT}/contract/approve")).status_code == 200
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + token("bob")},
    ) as bob:
        inv = (await bob.post("/api/investigations", json={"project_id": PROJECT})).json()["id"]
        assert (await bob.post(f"/api/investigations/{inv}/start")).status_code == 409


async def test_unbudgeted_alias_is_closed(guarded_app):
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app), base_url="http://test"
    ) as client:
        assert (
            await client.post(
                "/api/investigations/demo/run-pair", json={"input_json": {"signal": [1.0] * 8}}
            )
        ).status_code == 409


def test_live_model_fails_closed_without_cost_configuration(monkeypatch):
    from app.config import get_settings
    from app.providers.nebius import NebiusClient

    monkeypatch.setattr(get_settings(), "nebius_call_cost_ceiling_usd", 0)
    client = NebiusClient(api_key="test-only-never-sent")
    client.budget_ledger = BudgetLedger(BudgetConfig())
    with pytest.raises(BudgetExceeded, match="cost ceiling"):
        asyncio.run(client.propose_explanation("witness", "versions"))
    assert not client.budget_ledger.usage


async def test_guest_demo_uses_mock_even_when_server_has_live_key(guarded_app, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "nebius_api_key", "test-only-never-sent")
    monkeypatch.setattr(get_settings(), "nebius_call_cost_ceiling_usd", 0)
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app), base_url="http://test"
    ) as client:
        assert (await client.post(f"/api/projects/{PROJECT}/contract/approve")).status_code == 200
        inv = (await client.post("/api/investigations", json={"project_id": PROJECT})).json()["id"]
        assert (await client.post(f"/api/investigations/{inv}/start")).status_code == 202
        for _ in range(100):
            progress = (await client.get(f"/api/investigations/{inv}/progress")).json()
            if progress["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.05)
        assert progress["status"] == "completed", progress
        report = (await client.get(f"/api/investigations/{inv}/report")).json()
        assert report["model_proposals"]
        assert all(proposal["is_mock"] for proposal in report["model_proposals"])


async def test_unreviewed_fixture_mode_cannot_enter_public_pair(guarded_app):
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app), base_url="http://test"
    ) as client:
        inv = (await client.post("/api/investigations", json={"project_id": PROJECT})).json()["id"]
        response = await client.post(
            f"/api/investigations/{inv}/run-pair",
            json={"input_json": {"signal": [1.0] * 8, "_mode": "explicit_float32"}},
        )
        assert response.status_code == 422
