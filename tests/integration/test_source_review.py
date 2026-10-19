"""Revision identity, incomplete observations, replay gates and admission limits."""
import asyncio
import base64
import os
import uuid
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException, Response

from app.api import connections, source_analysis
from app.config import Settings
from app.domain.dependency_review import build_review, parse_manifest
from app.orchestrator import admission
from app.storage import source_reviews
from app.storage.investigation_store import FileSystemInvestigationStore, MemoryInvestigationStore

BASE, HEAD = "a" * 40, "b" * 40


def encoded(value):
    return {"type": "file", "encoding": "base64", "content": base64.b64encode(value.encode()).decode()}


@pytest.mark.parametrize("path,old,new", [
    ("requirements.txt", "# numpy==1.0\nnumpy==1.26.4 # pin", "# numpy==2.0\nnumpy==1.26.4"),
    ("pyproject.toml", '[project]\ndependencies=["numpy>=1.26"]', '[project]\ndependencies=["numpy>=1.26"]'),
    ("poetry.lock", '[[package]]\nname="numpy"\nversion="1.26.4"', '[[package]]\nname="numpy"\nversion="1.26.4"'),
])
def test_comment_only_or_unchanged_declarations_are_not_upgrades(path, old, new):
    review = build_review("github", "lab/project", 7, BASE, HEAD, [], [{"path": path, "old": old, "new": new}], True)
    assert review["dependency_changes"] == []
    assert review["executed_repository_code"] is False


def test_unavailable_manifest_is_not_a_fabricated_added_dependency():
    review = build_review("github", "lab/project", 7, BASE, HEAD, [],
                          [{"path": "requirements.txt", "old": None, "new": "numpy==2.0.2", "unavailable": True,
                            "limitations": ["Base content missing due to size cap"]}], False)
    assert not review["dependency_changes"]
    assert review["complete"] is False
    assert any("size cap" in limit for limit in review["limitations"])


def test_lock_versions_and_yaml_limits():
    parsed, limits = parse_manifest("uv.lock", '[[package]]\nname="NumPy"\nversion="2.0.2"')
    assert parsed == {"numpy": "==2.0.2"} and not limits
    parsed, limits = parse_manifest("environment.yml", 'dependencies:\n - numpy=2.0.2\n')
    assert parsed == {"numpy": "==2.0.2"} and limits


@pytest.mark.parametrize("path,old,new", [
    ("requirements.txt", 'numpy==1.26.4; python_version < "3"', 'numpy==2.0.2; python_version < "3"'),
    ("pyproject.toml", '[project.optional-dependencies]\nscience=["numpy==1.26.4"]',
     '[project.optional-dependencies]\nscience=["numpy==2.0.2"]'),
    ("Pipfile", '[packages]\nnumpy={version="==1.26.4", markers="python_version < \'3\'"}',
     '[packages]\nnumpy={version="==2.0.2", markers="python_version < \'3\'"}'),
])
@pytest.mark.asyncio
async def test_conditional_or_optional_pin_cannot_authorize_replay(path, old, new):
    source = Path("fixture/project/pipeline.py").read_text(encoding="utf-8")

    async def get(provider, token, endpoint, params=None):
        if endpoint.endswith("/7"):
            return {"base": {"sha": BASE}, "head": {"sha": HEAD}}
        if endpoint.endswith("pipeline.py"):
            return encoded(source)
        return encoded(old if params["ref"] == BASE else new)

    with pytest.raises(HTTPException) as failure:
        await source_analysis.prepare_replay(get, "github", "token", "lab/project", 7, HEAD, path)
    assert failure.value.status_code == 422


@pytest.mark.asyncio
async def test_file_pagination_inspects_dependency_after_first_page(monkeypatch):
    pages = []

    async def fake_get(provider, token, path, params=None):
        pages.append(params["page"])
        return ([{"filename": f"file-{i}.py"} for i in range(100)] if params["page"] == 1
                else [{"filename": "requirements.txt"}])

    monkeypatch.setattr(connections, "_host_get", fake_get)
    files, complete = await connections._host_pages("github", "token", "/repos/lab/project/pulls/7/files")
    assert len(files) == 101 and complete and pages == [1, 2]
    _, complete = await connections._host_pages("github", "token", "/repos/lab/project/pulls/7/files", max_pages=1)
    assert complete is False


@pytest.mark.asyncio
async def test_review_rejects_revision_changed_during_fetch():
    count = 0

    async def get(provider, token, path, params=None):
        nonlocal count
        count += 1
        return {"base": {"sha": BASE}, "head": {"sha": HEAD if count == 1 else "c" * 40}}

    async def pages(*args, **kwargs):
        return [], True

    with pytest.raises(HTTPException) as failure:
        await source_analysis.analyze(get, pages, "github", "token", "lab/project", "lab/project", 7)
    assert failure.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["different_source", "stale", "missing_pin", "wrong_pin"])
async def test_connected_replay_fails_closed(bad):
    source = Path("fixture/project/pipeline.py").read_text(encoding="utf-8")

    async def get(provider, token, path, params=None):
        if path.endswith("/7"):
            return {"base": {"sha": BASE}, "head": {"sha": "c" * 40 if bad == "stale" else HEAD}}
        if path.endswith("pipeline.py"):
            return encoded("print('unsafe')" if bad == "different_source" else source)
        if bad == "missing_pin":
            raise HTTPException(404, "Missing")
        return encoded("numpy>=1.26" if bad == "wrong_pin" else ("numpy==1.26.4" if params["ref"] == BASE else "numpy==2.0.2"))

    with pytest.raises(HTTPException) as failure:
        await source_analysis.prepare_replay(get, "github", "token", "lab/project", 7, HEAD, "requirements.txt")
    assert failure.value.status_code in {409, 422}


@pytest.mark.asyncio
async def test_connected_replay_establishes_immutable_supported_scope():
    source = Path("fixture/project/pipeline.py").read_text(encoding="utf-8")

    async def get(provider, token, path, params=None):
        if path.endswith("/7"):
            return {"base": {"sha": BASE}, "head": {"sha": HEAD}}
        if path.endswith("pipeline.py"):
            return encoded(source.replace("\n", "\r\n"))
        return encoded("numpy==1.26.4" if params["ref"] == BASE else "numpy==2.0.2")

    binding = await source_analysis.prepare_replay(get, "github", "token", "lab/project", 7, HEAD, "requirements.txt")
    assert binding["head_sha"] == HEAD
    assert binding["source_sha256"]["base"] == binding["source_sha256"]["head"]
    assert "other repository code" in binding["scope"]


@pytest.mark.asyncio
@pytest.mark.parametrize("supported", [True, False])
async def test_run_eligibility_precedes_contract_for_selected_revision(monkeypatch, supported):
    source = Path("fixture/project/pipeline.py").read_text(encoding="utf-8")

    async def get(provider, token, path, params=None):
        if path.endswith("/7"):
            return {"base": {"sha": BASE}, "head": {"sha": HEAD}}
        if path.endswith("pipeline.py"):
            return encoded(source if supported else "print('different project')")
        return encoded("numpy==1.26.4" if params["ref"] == BASE else "numpy==2.0.2")

    monkeypatch.setattr(connections, "_host_get", get)
    monkeypatch.setattr(connections, "_saved_token", lambda *args: "test-token")
    monkeypatch.setattr(connections, "get_settings", lambda: Settings(runner_mode="worker_http"))
    result = await connections.paired_replay_eligibility("github", connections.PairedReplayEligibilityRequest(
        repository="lab/project", number=7, expected_head_sha=HEAD,
        dependency_path="requirements.txt"), "github:test-user")
    assert result["eligible"] is supported
    if supported:
        assert result["base_sha"] == BASE and result["head_sha"] == HEAD
        assert result["numpy_versions"] == {"base": "1.26.4", "head": "2.0.2"}
    else:
        assert "exact supported numerical kernel" in result["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers,wanted", [(403, {"x-ratelimit-remaining": "0"}, 429), (403, {}, 403), (401, {}, 401)])
async def test_source_errors_distinguish_rate_access_and_expiry(monkeypatch, status, headers, wanted):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return httpx.Response(status, headers=headers, json={})

    monkeypatch.setattr(connections.httpx, "AsyncClient", lambda **kwargs: Client())
    with pytest.raises(HTTPException) as failure:
        await connections._host_get("github", "token", "/user")
    assert failure.value.status_code == wanted


@pytest.mark.parametrize("filesystem", [False, True])
def test_daily_quota_survives_storage_restart(tmp_path, monkeypatch, filesystem):
    monkeypatch.setattr(admission, "get_settings", lambda: Settings(guest_daily_investigations=1))
    store = FileSystemInvestigationStore(tmp_path) if filesystem else MemoryInvestigationStore()
    admission.create(store, "00000000-0000-0000-0000-000000000001", {}, "guest:first")
    if filesystem:
        store = FileSystemInvestigationStore(tmp_path)
    with pytest.raises(HTTPException) as failure:
        admission.create(store, "00000000-0000-0000-0000-000000000001", {}, "guest:first")
    assert failure.value.status_code == 429


def test_active_jobs_are_bounded_and_capacity_returns(monkeypatch):
    monkeypatch.setattr(admission, "get_settings", lambda: Settings(max_active_investigations=1))
    store = MemoryInvestigationStore()
    one = store.create_investigation("project", {}, "owner:one")
    two = store.create_investigation("project", {}, "owner:two")
    admission.start(store, one, "owner:one")
    with pytest.raises(HTTPException):
        admission.start(store, two, "owner:two")
    store.transition_status(one, "completed")
    admission.start(store, two, "owner:two")
    assert store.get_investigation(two)["status"] == "running"


def test_saved_reviews_are_revision_keyed_private_and_persisted():
    dsn = os.getenv("REPRODELTA_TEST_POSTGRES")
    if not dsn:
        pytest.skip("Disposable Postgres not configured")
    owner = "review-test:" + str(uuid.uuid4())
    result = build_review("github", "lab/project", 7, BASE, HEAD, [], [], True)
    review_id = source_reviews.save(dsn, owner, result)
    assert source_reviews.get(dsn, owner, uuid.UUID(review_id))["head_sha"] == HEAD
    assert source_reviews.get(dsn, owner + "other", uuid.UUID(review_id)) is None
    assert source_reviews.save(dsn, owner, result) == review_id
    updated_id = source_reviews.save(dsn, owner, {**result, "head_sha": "c" * 40})
    assert updated_id != review_id
    assert len(source_reviews.recent(dsn, owner)) == 2


def test_random_search_uses_seed_and_budget_without_known_boundary():
    from app.domain.contract import FIXTURE_CONTRACT
    from app.domain.types import BudgetConfig
    from app.orchestrator.search import SearchController
    from app.providers.runner import LocalRunnerAdapter
    def search(seed):
        return SearchController(FIXTURE_CONTRACT, BudgetConfig(max_candidates=4, max_runs=8), LocalRunnerAdapter()).run_search("seed-test", seed=seed, search_strategy="random")
    first, repeat, other = search(17), search(17), search(18)
    assert first.search_strategy == "random" and first.seed == 17
    assert first.attempted == 4
    assert [c.input_json for c in first.candidates] == [c.input_json for c in repeat.candidates]
    assert [c.input_json for c in first.candidates] != [c.input_json for c in other.candidates]


def test_matrix_contract_rejects_ragged_bool_and_out_of_domain():
    from app.domain.validator import validate_input
    from app.orchestrator.benchmark import LSTSQ_CONTRACT
    assert validate_input(LSTSQ_CONTRACT, {"matrix": [[1., 0.]] * 4}).valid
    for matrix in ([[1., 0.]] * 3, [[1.], [1., 0.], [1., 0.], [1., 0.]],
                   [[True, 0.]] * 4, [[11., 0.]] * 4):
        assert not validate_input(LSTSQ_CONTRACT, {"matrix": matrix}).valid


def test_execution_timestamps_enclose_the_actual_dispatch(monkeypatch):
    from app.domain.contract import FIXTURE_CONTRACT
    from app.orchestrator import executor
    from app.providers.runner import LocalRunnerAdapter
    times = [datetime(2026, 10, 14, tzinfo=UTC) + timedelta(seconds=i) for i in range(4)]
    values = iter(times)
    monkeypatch.setattr(executor, "_utcnow", lambda: next(values))
    pair = executor.run_pair(executor.PairedRunRequest(investigation_id=uuid.uuid4(), candidate={"signal": [1.] * 8},
                                                     candidate_sha256="a" * 64, contract=FIXTURE_CONTRACT), LocalRunnerAdapter())
    assert pair.old_run.started_at == times[0] and pair.old_run.ended_at == times[1]
    assert pair.new_run.started_at == times[2] and pair.new_run.ended_at == times[3]


@pytest.mark.asyncio
async def test_connected_replay_starts_engine_and_exports_owner_and_source(monkeypatch):
    from app.api import investigations
    from app.domain.contract import FIXTURE_CONTRACT, hash_contract
    from app.providers.runner import LocalRunnerAdapter
    source = Path("fixture/project/pipeline.py").read_text(encoding="utf-8")
    async def get(provider, token, path, params=None):
        if path.endswith("/7"):
            return {"base": {"sha": BASE}, "head": {"sha": HEAD}}
        if path.endswith("pipeline.py"):
            return encoded(source)
        return encoded("numpy==1.26.4" if params["ref"] == BASE else "numpy==2.0.2")
    monkeypatch.setattr(connections, "_host_get", get)
    monkeypatch.setattr(connections, "_saved_token", lambda *args: "test-token")
    monkeypatch.setattr(connections, "get_settings", lambda: Settings(runner_mode="worker_http"))
    # The route requires workers in production; the deterministic adapter tests
    # actual orchestration here. Dedicated pinned tests verify native execution.
    monkeypatch.setattr(investigations, "get_runner", LocalRunnerAdapter)
    previous = investigations._store
    store = MemoryInvestigationStore()
    investigations.set_store(store)
    try:
        from app.api.investigation_access import owner_context

        owner_token = owner_context.set("github:test-user")
        try:
            with pytest.raises(HTTPException) as unapproved:
                await connections.paired_replay("github", connections.PairedReplayRequest(
                    repository="lab/project", number=7, expected_head_sha=HEAD,
                    dependency_path="requirements.txt", contract_hash=hash_contract(FIXTURE_CONTRACT)), Response(), "github:test-user")
            assert unapproved.value.status_code == 409
            await investigations.approve_contract(investigations.DEMO_PROJECT_ID)
        finally:
            owner_context.reset(owner_token)
        result = await connections.paired_replay("github", connections.PairedReplayRequest(
            repository="lab/project", number=7, expected_head_sha=HEAD,
            dependency_path="requirements.txt", contract_hash=hash_contract(FIXTURE_CONTRACT),
            search_strategy="random"), Response(), "github:test-user")
        job = result["investigation_id"]
        for _ in range(200):
            if store.get_investigation(job)["status"] != "running":
                break
            await asyncio.sleep(.01)
        assert store.get_investigation(job)["owner_id"] == "github:test-user"
        assert store.get_investigation(job)["budget_json"]["search_strategy"] == "random"
        assert store.get_investigation(job)["status"] == "completed"
        report = store.get_report(job)
        assert report["source_binding"]["head_sha"] == HEAD
        assert report["source_binding"]["source_sha256"]["head"]
        assert report["run_mode"] == "local_simulation", (report["controls"], report["verdict"], report.get("stage_failures"))
    finally:
        investigations.set_store(previous)


@pytest.mark.asyncio
async def test_workbench_cookie_allows_navigation_only_for_verified_owner():
    from app.api import investigations
    from app.api.investigation_access import set_workbench_cookie
    from app.main import app
    store = MemoryInvestigationStore()
    previous = investigations._store
    previous_override = app.dependency_overrides.get(connections.current_user)
    app.dependency_overrides[connections.current_user] = lambda: "neon-user-42"
    investigations.set_store(store)
    job = store.create_investigation("00000000-0000-0000-0000-000000000001", {}, "neon-user-42")
    def cookie(owner):
        response = Response()
        set_workbench_cookie(response, owner)
        assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
        values = SimpleCookie(response.headers["set-cookie"])
        return "rd_workbench=" + values["rd_workbench"].value
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Exercise actual route ordering; the one-segment provider connect
            # route must not shadow workbench session issuance.
            session = await client.post("/api/connections/session/investigation")
            assert session.status_code == 200
            assert "rd_workbench=" in session.headers["set-cookie"]
            accepted = await client.get(f"/api/investigations/{job}", headers={"Cookie": cookie("neon-user-42")})
            assert accepted.status_code == 200
            assert accepted.headers["cache-control"] == "no-store"
            forbidden = await client.get(f"/api/investigations/{job}", headers={"Cookie": cookie("other-user")})
            assert forbidden.status_code == 404
            tampered = await client.get(f"/api/investigations/{job}", headers={"Cookie": "rd_workbench=forged"})
            assert tampered.status_code == 401
    finally:
        investigations.set_store(previous)
        if previous_override is None:
            app.dependency_overrides.pop(connections.current_user, None)
        else:
            app.dependency_overrides[connections.current_user] = previous_override


def test_failed_benchmark_executions_are_not_completed_pairs():
    from app.orchestrator.benchmark import compare_searches
    from app.providers.runner import RunnerAdapter, RunResult
    class FailedRunner(RunnerAdapter):
        def submit(self, manifest):
            return RunResult(run_id=manifest.run_id, status="error", exit_code=1, outputs={})
    result = compare_searches(FailedRunner(), 1)
    assert result["status"] == "execution_failed"
    assert all(row["completed_pairs"] == 0 and row["failed_pairs"] == 1 for row in result["rows"])


@pytest.mark.asyncio
async def test_start_fails_readiness_before_any_worker_execution(monkeypatch):
    from fastapi.responses import JSONResponse

    from app.api import health, investigations
    from app.providers.runner import WorkerHttpRunnerAdapter
    store = MemoryInvestigationStore()
    previous = investigations._store
    investigations.set_store(store)
    async def unavailable():
        return JSONResponse({"status": "unavailable"}, status_code=503)
    monkeypatch.setattr(health, "ready", unavailable)
    try:
        await investigations.approve_contract(investigations.DEMO_PROJECT_ID)
        record = await investigations.create_investigation(investigations.CreateInvestigationRequest(project_id=investigations.DEMO_PROJECT_ID))
        with pytest.raises(HTTPException) as error:
            await investigations.start_investigation(record["id"], WorkerHttpRunnerAdapter("http://old/run", "http://new/run"))
        assert error.value.status_code == 503
        assert store.get_investigation(record["id"])["status"] == "draft"
        assert store.get_events(record["id"])[-1]["payload"]["stage"] == "preflight"
    finally:
        investigations.set_store(previous)
