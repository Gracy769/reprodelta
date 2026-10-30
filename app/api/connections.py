"""Read-only source-host connections and bounded PR/MR advice.

The connection token is supplied by the user, encrypted at rest, and never
returned to the browser. Repository source is never executed by this module.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
import jwt
import psycopg
from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from jwt import PyJWKClient
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.storage import source_reviews

router = APIRouter(prefix="/api/connections", tags=["connections"])

HOSTS = {
    "github": "https://api.github.com",
    "gitlab": "https://gitlab.com/api/v4",
}


class ConnectRequest(BaseModel):
    token: str = Field(min_length=12, max_length=4096)


def _database_dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _cipher() -> MultiFernet:
    secret = get_settings().secret_key
    if secret == "change-me-in-production":
        raise HTTPException(503, "Token storage requires a configured SECRET_KEY")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"reprodelta-source-token-v1",
        info=b"fernet-encryption-key",
    )
    primary_key = base64.urlsafe_b64encode(hkdf.derive(secret.encode()))
    legacy_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return MultiFernet([Fernet(primary_key), Fernet(legacy_key)])



_table_initialized: bool = False


def _ensure_table(conn: psycopg.Connection) -> None:
    global _table_initialized
    if _table_initialized:
        return
    _table_initialized = True
    conn.execute(
        """CREATE TABLE IF NOT EXISTS connected_source_accounts (
            owner_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            account_name TEXT NOT NULL,
            encrypted_token TEXT NOT NULL,
            encrypted_refresh_token TEXT,
            token_expires_at TIMESTAMPTZ,
            connected_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (owner_id, provider)
        )"""
    )
    conn.execute("ALTER TABLE connected_source_accounts ADD COLUMN IF NOT EXISTS encrypted_refresh_token TEXT")
    conn.execute("ALTER TABLE connected_source_accounts ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMPTZ")


@lru_cache(maxsize=1)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True)


def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
    settings = get_settings()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to continue")
    token = authorization[7:]
    if getattr(settings, "secret_key", None) not in {None, "change-me-in-production"}:
        for issuer, audience in (("reprodelta-workspace", "reprodelta-api"),
                                 ("reprodelta", "reprodelta")):
            try:
                claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"],
                                    issuer=issuer, audience=audience,
                                    options={"require": ["exp", "sub", "iss", "aud"]})
                subject = claims.get("sub")
                if isinstance(subject, str) and subject.startswith(("github:", "gitlab:", "firebase:")):
                    return subject
            except jwt.PyJWTError:
                continue
    if not settings.neon_auth_base_url or not settings.neon_auth_jwks_url:
        raise HTTPException(401, "Session expired; sign in again")
    try:
        key = _jwks_client(settings.neon_auth_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["EdDSA", "ES256", "RS256"],
            issuer=settings.neon_auth_base_url.rstrip("/"),
            audience=settings.neon_auth_base_url.rstrip("/"),
            leeway=30,
            options={"require": ["exp", "sub", "iss", "aud"]},
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise ValueError("missing subject")
        return subject
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(401, "Session expired; sign in again") from exc


def _provider(provider: str) -> str:
    if provider not in HOSTS:
        raise HTTPException(404, "Source host not supported")
    return HOSTS[provider]


async def _host_get(provider: str, token: str, path: str, params: dict | None = None):
    base = _provider(provider)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if provider == "github":
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(base + path, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"{provider.title()} is unavailable") from exc
    if response.status_code == 429 or (response.status_code == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0" or response.headers.get("retry-after"))):
        retry = response.headers.get("retry-after", "60")
        raise HTTPException(429, f"{provider.title()} rate limit reached; retry later", headers={"Retry-After": retry})
    if response.status_code == 401:
        raise HTTPException(401, f"{provider.title()} connection expired; reconnect your source account")
    if response.status_code == 403:
        raise HTTPException(403, f"{provider.title()} denied access; check repository permissions and organization authorization")
    if response.status_code == 404:
        raise HTTPException(404, "Repository or change not found")
    if response.status_code >= 400:
        raise HTTPException(502, f"{provider.title()} returned an error")
    if len(response.content) > 2_000_000:
        raise HTTPException(502, "Source response exceeded the bounded review size")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(502, "Source host returned an invalid response") from exc


async def _host_pages(provider, token, path, params=None, max_pages=10):
    """Bounded pagination on a validated, fixed-host path; never follow Link URLs."""
    items = []
    for page in range(1, max_pages + 1):
        batch = await _host_get(provider, token, path, {**(params or {}), "per_page": 100, "page": page})
        if not isinstance(batch, list):
            raise HTTPException(502, "Source host returned an invalid list")
        items.extend(batch)
        if len(batch) < 100:
            return items, True
    return items, False


def _saved_token(owner_id: str, provider: str) -> str:
    _provider(provider)
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_table(conn)
        row = conn.execute(
            """SELECT encrypted_token, encrypted_refresh_token, token_expires_at
               FROM connected_source_accounts WHERE owner_id=%s AND provider=%s FOR UPDATE""",
            (owner_id, provider),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Connect {provider.title()} first")
        expires_at = row["token_expires_at"]
        if provider == "gitlab" and expires_at and expires_at <= datetime.now(UTC) + timedelta(seconds=60):
            encrypted_refresh = row["encrypted_refresh_token"]
            if not encrypted_refresh:
                raise HTTPException(401, "GitLab session expired; reconnect GitLab")
            refresh = _cipher().decrypt(encrypted_refresh.encode()).decode()
            access_token, refresh_token, new_expiry = _refresh_gitlab_token(refresh)
            row["encrypted_token"] = _cipher().encrypt(access_token.encode()).decode()
            conn.execute(
                """UPDATE connected_source_accounts SET encrypted_token=%s,
                   encrypted_refresh_token=%s, token_expires_at=%s
                   WHERE owner_id=%s AND provider=%s""",
                (row["encrypted_token"], _cipher().encrypt(refresh_token.encode()).decode(),
                 new_expiry, owner_id, provider),
            )
        return _cipher().decrypt(row["encrypted_token"].encode()).decode()


def _refresh_gitlab_token(refresh_token: str) -> tuple[str, str, datetime]:
    settings = get_settings()
    if not settings.gitlab_oauth_client_id or not settings.gitlab_oauth_client_secret:
        raise HTTPException(503, "GitLab OAuth is not configured")
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            response = client.post("https://gitlab.com/oauth/token", data={
                "grant_type": "refresh_token", "refresh_token": refresh_token,
                "client_id": settings.gitlab_oauth_client_id,
                "client_secret": settings.gitlab_oauth_client_secret,
            })
            response.raise_for_status()
            payload = response.json()
        access, refresh = payload.get("access_token"), payload.get("refresh_token")
        seconds = payload.get("expires_in")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise ValueError("GitLab token response incomplete")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
            raise ValueError("GitLab token expiry missing")
        return access, refresh, datetime.now(UTC) + timedelta(seconds=seconds)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(401, "GitLab session expired; reconnect GitLab") from exc


def _github_repository(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise HTTPException(422, "Invalid repository name")
    owner, name = value.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise HTTPException(422, "Invalid repository name")
    return value


def _gitlab_project(value: str) -> str:
    if value.isdigit() and len(value) <= 20:
        return value
    segments = value.split("/")
    if (len(value) > 255 or len(segments) < 2 or
            any(segment in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", segment)
                for segment in segments)):
        raise HTTPException(422, "Invalid GitLab project path")
    return quote(value, safe="")


@router.get("")
def list_connections(owner_id: Annotated[str, Depends(current_user)]):
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT provider, account_name, connected_at FROM connected_source_accounts WHERE owner_id=%s",
            (owner_id,),
        ).fetchall()
    return {"connections": rows}


@router.post("/{provider}")
async def connect_source(
    provider: str, body: ConnectRequest, owner_id: Annotated[str, Depends(current_user)]
):
    _provider(provider)
    user = await _host_get(provider, body.token, "/user")
    account = user.get("login") if provider == "github" else user.get("username")
    if not isinstance(account, str) or not account:
        raise HTTPException(502, "Source host did not return an account name")
    encrypted = _cipher().encrypt(body.token.encode()).decode()
    with psycopg.connect(_database_dsn()) as conn:
        _ensure_table(conn)
        conn.execute(
            """INSERT INTO connected_source_accounts
                (owner_id, provider, account_name, encrypted_token, connected_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (owner_id, provider) DO UPDATE SET
                account_name=EXCLUDED.account_name,
                encrypted_token=EXCLUDED.encrypted_token,
                encrypted_refresh_token=NULL,
                token_expires_at=NULL,
                connected_at=EXCLUDED.connected_at""",
            (owner_id, provider, account, encrypted, datetime.now(UTC)),
        )
    return {"provider": provider, "account_name": account}


@router.delete("/{provider}", status_code=204)
def disconnect_source(provider: str, owner_id: Annotated[str, Depends(current_user)]):
    _provider(provider)
    with psycopg.connect(_database_dsn()) as conn:
        _ensure_table(conn)
        conn.execute(
            "DELETE FROM connected_source_accounts WHERE owner_id=%s AND provider=%s",
            (owner_id, provider),
        )


@router.get("/{provider}/repositories")
async def repositories(provider: str, owner_id: Annotated[str, Depends(current_user)]):
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    if provider == "github":
        data, complete = await _host_pages(
            provider,
            token,
            "/user/repos",
            {
                "per_page": 100,
                "sort": "updated",
                "affiliation": "owner,collaborator,organization_member",
            },
        )
        items = [
            {
                "id": item["id"],
                "name": item["full_name"],
                "private": item["private"],
                "fork": item.get("fork", False),
                "starred": False,
                "connected_access": True,
                "url": item["html_url"],
                "description": item.get("description"),
                "updated_at": item.get("updated_at"),
            }
            for item in data
        ]
        stars, stars_complete = await _host_pages(provider, token, "/user/starred")
        complete = complete and stars_complete
        seen = {item["id"] for item in items}
        star_ids = {item["id"] for item in stars}
        for item in items:
            item["starred"] = item["id"] in star_ids
        items.extend(
            {
                "id": item["id"], "name": item["full_name"], "private": item["private"],
                "fork": item.get("fork", False), "starred": True, "url": item["html_url"],
                "connected_access": False,
                "description": item.get("description"), "updated_at": item.get("updated_at"),
            }
            for item in stars if item["id"] not in seen
        )
    else:
        data, complete = await _host_pages(
            provider,
            token,
            "/projects",
            {"membership": "true", "per_page": 100, "order_by": "last_activity_at"},
        )
        items = [
            {
                "id": item["id"],
                "name": item["path_with_namespace"],
                "private": item.get("visibility") == "private",
                "fork": bool(item.get("forked_from_project")),
                "starred": item.get("starred", False),
                "connected_access": True,
                "url": item["web_url"],
                "description": item.get("description"),
                "updated_at": item.get("last_activity_at"),
            }
            for item in data
        ]
    return {"repositories": items, "has_more": not complete,
            "scope": "Up to 1,000 connected repositories and 1,000 starred repositories; paste a direct change URL beyond this cap."}


@router.get("/{provider}/changes")
async def changes(provider: str, repository: str, owner_id: Annotated[str, Depends(current_user)]):
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    if provider == "github":
        repository = _github_repository(repository)
        data, complete = await _host_pages(
            provider, token, f"/repos/{repository}/pulls", {"state": "open", "per_page": 50}
        )
        items = [
            {
                "number": item["number"],
                "title": item["title"],
                "url": item["html_url"],
                "draft": item.get("draft", False),
                "updated_at": item.get("updated_at"),
            }
            for item in data
        ]
    else:
        repository = _gitlab_project(repository)
        data, complete = await _host_pages(
            provider,
            token,
            f"/projects/{repository}/merge_requests",
            {"state": "opened", "per_page": 50},
        )
        items = [
            {
                "number": item["iid"],
                "title": item["title"],
                "url": item["web_url"],
                "draft": item.get("draft", False),
                "updated_at": item.get("updated_at"),
            }
            for item in data
        ]
    return {"changes": items, "has_more": not complete}


@router.get("/{provider}/issues")
async def issues(provider: str, repository: str, owner_id: Annotated[str, Depends(current_user)]):
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    if provider == "github":
        repository = _github_repository(repository)
        data = await _host_get(provider, token, f"/repos/{repository}/issues", {"state": "open", "per_page": 50})
        data = [item for item in data if "pull_request" not in item]
    else:
        if not repository.isdigit():
            raise HTTPException(422, "Invalid project ID")
        data = await _host_get(provider, token, f"/projects/{repository}/issues", {"state": "opened", "per_page": 50})
    return {
        "issues": [
            {"number": item["number"] if provider == "github" else item["iid"],
             "title": item["title"], "url": item["html_url"] if provider == "github" else item["web_url"],
             "updated_at": item.get("updated_at")}
            for item in data
        ],
        "scope": "First 50 open issues; issues are metadata and are not executed.",
    }


@router.get("/review-queue")
async def review_queue(owner_id: Annotated[str, Depends(current_user)]):
    """Find recent open changes in a bounded set of recently updated projects."""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_table(conn)
        providers = [row["provider"] for row in conn.execute(
            "SELECT provider FROM connected_source_accounts WHERE owner_id=%s", (owner_id,)
        ).fetchall()]
    candidates = []
    errors = []
    for provider in providers:
        try:
            result = await repositories(provider, owner_id)
        except HTTPException as exc:
            errors.append({"provider": provider, "error": exc.detail})
            continue
        candidates.extend({**repo, "provider": provider, "key": repo["name"] if provider == "github" else str(repo["id"])}
                          for repo in result["repositories"] if repo["connected_access"])
    candidates.sort(key=lambda repo: repo.get("updated_at") or "", reverse=True)
    sampled = candidates[:8]
    semaphore = asyncio.Semaphore(4)

    async def fetch(repo):
        async with semaphore:
            try:
                result = await changes(repo["provider"], repo["key"], owner_id)
                return [{**change, "repository": repo["name"], "repository_key": repo["key"],
                         "provider": repo["provider"], "repository_url": repo["url"]}
                        for change in result["changes"][:5]]
            except HTTPException as exc:
                errors.append({"provider": repo["provider"], "repository": repo["name"], "error": exc.detail})
                return []

    groups = await asyncio.gather(*(fetch(repo) for repo in sampled))
    found = sorted((item for group in groups for item in group),
                   key=lambda item: item.get("updated_at") or "", reverse=True)[:12]
    return {"changes": found, "projects_checked": len(sampled), "projects_available": len(candidates), "errors": errors,
            "scope": "Recent open changes from up to 8 recently updated connected projects; no code was executed."}


@router.get("/reviews")
async def recent_reviews(owner_id: Annotated[str, Depends(current_user)]):
    return {"reviews": await asyncio.to_thread(source_reviews.recent, _database_dsn(), owner_id)}


@router.post("/session/investigation")
async def investigation_session(response: Response, owner_id: Annotated[str, Depends(current_user)]):
    from app.api.investigation_access import set_workbench_cookie
    set_workbench_cookie(response, owner_id)
    return {"ok": True}


@router.get("/reviews/{review_id}")
async def saved_review(review_id: uuid.UUID, owner_id: Annotated[str, Depends(current_user)]):
    result = await asyncio.to_thread(source_reviews.get, _database_dsn(), owner_id, review_id)
    if result is None:
        raise HTTPException(404, "Review not found")
    return result


@router.get("/{provider}/advice")
async def change_advice(provider: str, repository: str, number: int,
                        owner_id: Annotated[str, Depends(current_user)]):
    from app.api.source_analysis import analyze
    if number < 1:
        raise HTTPException(422, "Invalid change number")
    _provider(provider)
    display_repository = repository
    repository = _github_repository(repository) if provider == "github" else _gitlab_project(repository)
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    try:
        async with asyncio.timeout(90):
            result = await analyze(_host_get, _host_pages, provider, token, repository, display_repository, number)
    except TimeoutError as exc:
        raise HTTPException(504, "Source analysis timed out; no complete review was produced") from exc
    result["review_id"] = await asyncio.to_thread(source_reviews.save, _database_dsn(), owner_id, result)
    return result


class PairedReplayEligibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(max_length=255)
    number: int = Field(ge=1)
    expected_head_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    dependency_path: str = Field(max_length=512)


class PairedReplayRequest(PairedReplayEligibilityRequest):
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    search_strategy: Literal["sweep", "random", "adaptive"] = "sweep"


@router.post("/{provider}/paired-replay/eligibility")
async def paired_replay_eligibility(provider: str, body: PairedReplayEligibilityRequest,
                                    owner_id: Annotated[str, Depends(current_user)]):
    """Check source and environment pins before offering a run contract."""
    from app.api.source_analysis import prepare_replay

    _provider(provider)
    if get_settings().runner_mode != "worker_http":
        return {"eligible": False, "reason": "Pinned workers are not configured for connected runs."}
    repository = _github_repository(body.repository) if provider == "github" else _gitlab_project(body.repository)
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    try:
        async with asyncio.timeout(60):
            binding = await prepare_replay(_host_get, _host_pages, provider, token, repository, body.number,
                                           body.expected_head_sha, body.dependency_path)
    except TimeoutError as exc:
        raise HTTPException(504, "Run eligibility check timed out; retry this change") from exc
    except HTTPException as exc:
        if exc.status_code == 422:
            return {"eligible": False, "reason": exc.detail}
        raise
    return {"eligible": True, "scope": binding["scope"],
            "base_sha": binding["base_sha"], "head_sha": binding["head_sha"],
            "dependency_path": binding["dependency_path"], "numpy_versions": binding["numpy_versions"]}


@router.post("/{provider}/paired-replay", status_code=202)
async def paired_replay(provider: str, body: PairedReplayRequest, response: Response,
                        owner_id: Annotated[str, Depends(current_user)]):
    from app.api import investigations
    from app.api.investigation_access import approval_key, owner_context, set_workbench_cookie
    from app.api.source_analysis import prepare_replay
    from app.domain.contract import FIXTURE_CONTRACT, hash_contract
    _provider(provider)
    if body.contract_hash != hash_contract(FIXTURE_CONTRACT):
        raise HTTPException(409, "Approve the current supported contract before replay")
    if get_settings().runner_mode != "worker_http":
        raise HTTPException(503, "Connected paired replay requires pinned workers; simulation cannot verify repository execution")
    repository = _github_repository(body.repository) if provider == "github" else _gitlab_project(body.repository)
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    try:
        async with asyncio.timeout(60):
            binding = await prepare_replay(_host_get, _host_pages, provider, token, repository, body.number,
                                           body.expected_head_sha, body.dependency_path)
    except TimeoutError as exc:
        raise HTTPException(504, "Replay preparation timed out; no execution was started") from exc
    binding.update(provider=provider, repository=body.repository, number=body.number)
    set_workbench_cookie(response, owner_id)
    context = owner_context.set(owner_id)
    try:
        approval = investigations.get_store().get_approval(approval_key(str(investigations.DEMO_PROJECT_ID))) or {}
        if approval.get("contract_hash") != body.contract_hash:
            raise HTTPException(409, "Approve the supported contract before starting the paired investigation")
        record = await investigations.create_investigation(investigations.CreateInvestigationRequest(
            project_id=investigations.DEMO_PROJECT_ID,
            description="Approved supported-kernel replay of connected change",
            search_strategy=body.search_strategy,
        ))
        investigations.get_store().append_event(record["id"], "source", "source_binding", binding)
        await investigations.start_investigation(record["id"], investigations.get_runner())
        return {"investigation_id": record["id"], "scope": binding["scope"]}
    finally:
        owner_context.reset(context)
