"""Read immutable source observations; no checkout, import or user-code execution."""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from app.domain.dependency_review import build_review, is_manifest, parse_manifest


async def change_refs(get, provider, token, repository, number):
    if provider == "github":
        data = await get(provider, token, f"/repos/{repository}/pulls/{number}")
        if not isinstance(data, dict) or not isinstance(data.get("base"), dict) or not isinstance(data.get("head"), dict):
            raise HTTPException(502, "Source host returned invalid revision metadata")
        refs = (data.get("base", {}).get("sha"), data.get("head", {}).get("sha"))
    else:
        data = await get(provider, token, f"/projects/{repository}/merge_requests/{number}")
        if not isinstance(data, dict) or not isinstance(data.get("diff_refs"), dict):
            raise HTTPException(409, "Source host has not prepared revision metadata")
        refs = ((data.get("diff_refs") or {}).get("base_sha"), (data.get("diff_refs") or {}).get("head_sha"))
    if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40,64}", sha) for sha in refs):
        raise HTTPException(409, "Change revisions are not ready; try again after the host prepares the diff")
    return refs


async def file_content(get, provider, token, repository, sha, path):
    if len(path) > 512 or any(part in {"", ".", ".."} for part in path.split("/")):
        raise HTTPException(422, "Unsupported source path")
    endpoint = (f"/repos/{repository}/contents/{quote(path, safe='/')}" if provider == "github"
                else f"/projects/{repository}/repository/files/{quote(path, safe='')}")
    try:
        data = await get(provider, token, endpoint, {"ref": sha})
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    if not isinstance(data, dict) or data.get("encoding") != "base64" or data.get("size", 0) > 128_000:
        raise HTTPException(422, "Source file is unavailable or exceeds 128 KB")
    # Reject symlinks/submodules: never dereference or follow download URLs.
    if data.get("type", "file") != "file" or data.get("submodule_git_url") or data.get("target"):
        raise HTTPException(422, "Only regular source files are supported")
    try:
        encoded = data.get("content", "")
        if len(encoded) > 180_000:
            raise ValueError("content too large")
        raw = base64.b64decode("".join(encoded.split()), validate=True)
        if len(raw) > 128_000:
            raise ValueError("content too large")
        return raw.decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, "Source file is not bounded UTF-8 content") from exc


async def analyze(get, pages, provider, token, repository, display_repository, number):
    base_sha, head_sha = await change_refs(get, provider, token, repository, number)
    if provider == "gitlab" and display_repository.isdigit():
        project = await get(provider, token, f"/projects/{repository}")
        display_repository = project.get("path_with_namespace", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", display_repository):
            raise HTTPException(502, "Source host did not return a project path")
    endpoint = (f"/repos/{repository}/pulls/{number}/files" if provider == "github"
                else f"/projects/{repository}/merge_requests/{number}/diffs")
    data, complete = await pages(provider, token, endpoint, max_pages=30)
    files = []
    for item in data:
        patch = item.get("patch" if provider == "github" else "diff")
        path = item.get("filename" if provider == "github" else "new_path")
        if not isinstance(path, str) or (patch is not None and not isinstance(patch, str)):
            raise HTTPException(502, "Source host returned invalid changed-file metadata")
        incomplete = patch is None or len(patch) > 8_000 or item.get("too_large") or item.get("collapsed")
        files.append({"path": path,
                      "old_path": item.get("previous_filename" if provider == "github" else "old_path"),
                      "status": item.get("status", "removed" if item.get("deleted_file") else "changed"),
                      "patch": (patch or "")[:8_000]})
        complete = complete and not incomplete
    manifests = []
    candidates = [f for f in files if is_manifest(f["path"]) or (f["old_path"] and is_manifest(f["old_path"]))]
    for item in candidates[:10]:
        path, old_path = item["path"], item["old_path"] or item["path"]
        manifest = {"path": path, "old_path": old_path, "limitations": []}
        for key, sha, location in (("old", base_sha, old_path), ("new", head_sha, path)):
            try:
                manifest[key] = await file_content(get, provider, token, repository, sha, location)
            except HTTPException as exc:
                if exc.status_code != 422:
                    raise
                manifest[key] = None
                manifest["limitations"].append(f"{location} at {sha[:12]} could not be read; dependency comparison is incomplete.")
                manifest["unavailable"] = True
                complete = False
        manifests.append(manifest)
    if len(candidates) > 10:
        complete = False
    if await change_refs(get, provider, token, repository, number) != (base_sha, head_sha):
        raise HTTPException(409, "PR/MR changed during analysis; analyze the latest revision again")
    return build_review(provider, display_repository, number, base_sha, head_sha, files, manifests, complete)


async def prepare_replay(get, provider, token, repository, number, expected_head, dependency_path):
    """Only replay the exact supported kernel under the two pinned NumPy versions.

    Remote bytes are checked against trusted local bytes, never executed. Other
    repository files and environments are outside this expressly approved scope.
    """
    base, head = await change_refs(get, provider, token, repository, number)
    if head != expected_head:
        raise HTTPException(409, "PR/MR changed; analyze the latest revision before configuring replay")
    path = "fixture/project/pipeline.py"
    expected = Path(__file__).resolve().parents[2].joinpath(path).read_text(encoding="utf-8")
    hashes = {}
    versions = {}
    for label, sha in (("base", base), ("head", head)):
        source = await file_content(get, provider, token, repository, sha, path)
        if source is None or source.replace("\r\n", "\n") != expected:
            raise HTTPException(422, "This repository does not contain the exact supported numerical kernel at both revisions. Arbitrary project execution requires a separately validated adapter.")
        hashes[label] = hashlib.sha256(source.replace("\r\n", "\n").encode()).hexdigest()
        # Pin declarations must be in explicit environment files, not inferred from a package mention.
        if not is_manifest(dependency_path):
            raise HTTPException(422, "Select a supported dependency manifest from the review")
        content = await file_content(get, provider, token, repository, sha, dependency_path)
        env, limits = parse_manifest(dependency_path, content)
        wanted = "1.26.4" if label == "base" else "2.0.2"
        if env.get("numpy") != f"=={wanted}" or limits:
            raise HTTPException(422, f"An unambiguous NumPy =={wanted} pin in {dependency_path} is required")
        versions[label] = wanted
    if await change_refs(get, provider, token, repository, number) != (base, head):
        raise HTTPException(409, "PR/MR changed during replay preparation; retry analysis")
    return {"base_sha": base, "head_sha": head, "kernel_path": path, "dependency_path": dependency_path,
            "source_sha256": hashes, "numpy_versions": versions,
            "scope": "Only the byte-identical supported numerical kernel in pinned NumPy 1.26.4/2.0.2; other repository code, dependencies and PR changes are not executed."}
