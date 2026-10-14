"""Read-only source-host connections and bounded PR/MR advice.

The connection token is supplied by the user, encrypted at rest, and never
returned to the browser. Repository source is never executed by this module.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated

import httpx
import jwt
import psycopg
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Header, HTTPException
from jwt import PyJWKClient
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.config import get_settings

router = APIRouter(prefix="/api/connections", tags=["connections"])

HOSTS = {
    "github": "https://api.github.com",
    "gitlab": "https://gitlab.com/api/v4",
}


class ConnectRequest(BaseModel):
    token: str = Field(min_length=12, max_length=4096)


def _database_dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _cipher() -> Fernet:
    secret = get_settings().secret_key
    if secret == "change-me-in-production":
        raise HTTPException(503, "Token storage requires a configured SECRET_KEY")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def _ensure_table(conn: psycopg.Connection) -> None:
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
        try:
            claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"], issuer="reprodelta", audience="reprodelta", options={"require": ["exp", "sub", "iss", "aud"]})
            subject = claims.get("sub")
            if isinstance(subject, str) and subject.startswith(("github:", "gitlab:", "firebase:")):
                return subject
        except jwt.PyJWTError:
            pass
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
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            response = await client.get(base + path, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"{provider.title()} is unavailable") from exc
    if response.status_code in (401, 403):
        raise HTTPException(401, f"{provider.title()} connection expired or lacks read access")
    if response.status_code == 404:
        raise HTTPException(404, "Repository or change not found")
    if response.status_code >= 400:
        raise HTTPException(502, f"{provider.title()} returned an error")
    return response.json()


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
        data = await _host_get(
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
        stars = await _host_get(provider, token, "/user/starred", {"per_page": 100})
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
        data = await _host_get(
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
    return {"repositories": items, "has_more": len(data) == 100}


@router.get("/{provider}/changes")
async def changes(provider: str, repository: str, owner_id: Annotated[str, Depends(current_user)]):
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    if provider == "github":
        repository = _github_repository(repository)
        data = await _host_get(
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
        if not repository.isdigit():
            raise HTTPException(422, "Invalid project ID")
        data = await _host_get(
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
    return {"changes": items, "has_more": len(data) == 50}


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
    for provider in providers:
        try:
            result = await repositories(provider, owner_id)
        except HTTPException:
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
            except HTTPException:
                return []

    groups = await asyncio.gather(*(fetch(repo) for repo in sampled))
    found = sorted((item for group in groups for item in group),
                   key=lambda item: item.get("updated_at") or "", reverse=True)[:12]
    return {"changes": found, "projects_checked": len(sampled), "projects_available": len(candidates),
            "scope": "Recent open changes from up to 8 recently updated connected projects; no code was executed."}


@router.get("/{provider}/advice")
async def change_advice(
    provider: str,
    repository: str,
    number: int,
    owner_id: Annotated[str, Depends(current_user)],
):
    if number < 1:
        raise HTTPException(422, "Invalid change number")
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    if provider == "github":
        repository = _github_repository(repository)
        data = await _host_get(
            provider, token, f"/repos/{repository}/pulls/{number}/files", {"per_page": 100}
        )
        files = [
            {"path": x["filename"], "status": x["status"], "patch": x.get("patch", "")[:8000]}
            for x in data
        ]
    else:
        if not repository.isdigit():
            raise HTTPException(422, "Invalid project ID")
        result = await _host_get(
            provider, token, f"/projects/{repository}/merge_requests/{number}/changes"
        )
        files = [
            {"path": x["new_path"], "status": "changed", "patch": x.get("diff", "")[:8000]}
            for x in result.get("changes", [])[:100]
        ]
    dependency_files = [
        x["path"]
        for x in files
        if re.search(
            r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|poetry\.lock|uv\.lock|Pipfile\.lock)$",
            x["path"],
            re.I,
        )
    ]
    dependency_lines = []
    for changed in files:
        if changed["path"] not in dependency_files:
            continue
        for line in changed["patch"].splitlines():
            if re.match(r"^[+-](?![+-]).*(?:numpy|scipy|pandas|torch|jax)(?:\b|\[)", line, re.I):
                dependency_lines.append(f"{changed['path']}: {line[:120]}")
                if len(dependency_lines) == 6:
                    break
        if len(dependency_lines) == 6:
            break
    numpy_mentioned = any("numpy" in x["patch"].lower() for x in files)
    advice = []
    findings = []
    if dependency_files:
        advice.append(
            "A dependency manifest changed. Compare resolved versions and rerun scientific assertions in both environments."
        )
        findings.append({"title": "Dependency inputs changed", "evidence": " · ".join(dependency_lines) if dependency_lines else ", ".join(dependency_files[:5]),
                         "action": "Compare the resolved package versions before and after this change, then rerun the assertions that depend on them."})
    if numpy_mentioned:
        advice.append(
            "NumPy appears in the change. Review dtype promotion and threshold-sensitive conclusions, then run a paired reproduction."
        )
        numpy_paths = [x["path"] for x in files if "numpy" in x["patch"].lower()]
        findings.append({"title": "NumPy appears in the diff", "evidence": ", ".join(numpy_paths[:5]),
                         "action": "Check dtype assumptions and threshold comparisons in these files against both dependency environments."})
    test_paths = [x["path"] for x in files if re.search(r"(^|/)(tests?|specs?)/|(^|/)(test_|.*_test\.)", x["path"], re.I)]
    if test_paths:
        findings.append({"title": "Tests changed alongside the code", "evidence": ", ".join(test_paths[:5]),
                         "action": "Read the changed assertions to see which behavior the author intended to preserve."})
    if not advice:
        advice.append(
            "No supported dependency-upgrade pattern was detected in the available diff. Review the change manually before drawing a conclusion."
        )
    if not findings:
        findings.append({"title": "No supported dependency signal found", "evidence": f"{len(files)} changed files inspected",
                         "action": "Inspect the source diff and test results in the host. This review cannot infer a scientific regression from the available file metadata."})
    return {
        "mode": "metadata_review_only",
        "executed_repository_code": False,
        "scope": "First 100 changed files; patches may be truncated by the source host.",
        "changed_file_count_checked": len(files),
        "dependency_files": dependency_files,
        "dependency_changes": dependency_lines,
        "numpy_mentioned": numpy_mentioned,
        "advice": advice,
        "findings": findings,
        "next_step": "Use the curated NumPy replay to inspect a reproducible example; this does not test the selected repository.",
    }
