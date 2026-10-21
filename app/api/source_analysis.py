"""Read immutable source observations; no checkout, import or user-code execution."""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from app.domain.dependency_review import build_review, is_manifest, parse_manifest
from app.providers.e2b_runner import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_SNAPSHOT_BYTES,
    validate_snapshot,
)


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


async def immutable_snapshot(get, provider: str, token: str, repository: str, sha: str):
    """Read a bounded tree by commit SHA; never follow a host download URL."""
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise HTTPException(422, "An immutable commit SHA is required")
    if provider == "github":
        tree = await get(provider, token, f"/repos/{repository}/git/trees/{sha}", {"recursive": "1"})
        if not isinstance(tree, dict) or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
            raise HTTPException(422, "The source tree is incomplete or unavailable")
        entries = tree["tree"]
    elif provider == "gitlab":
        entries = []
        for page in range(1, 4):
            batch = await get(provider, token, f"/projects/{repository}/repository/tree",
                              {"ref": sha, "recursive": "true", "per_page": 100, "page": page})
            if not isinstance(batch, list):
                raise HTTPException(502, "Source host returned an invalid tree")
            entries.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise HTTPException(422, "Source tree exceeds the 200-file pilot limit")
    else:
        raise HTTPException(422, "Unsupported source host")
    if len([entry for entry in entries if isinstance(entry, dict) and entry.get("type") != "tree"]) > MAX_FILES:
        raise HTTPException(422, "Source tree exceeds the 200-file pilot limit")
    files: dict[str, bytes] = {}
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise HTTPException(502, "Source host returned an invalid tree entry")
        kind, mode = entry.get("type"), entry.get("mode")
        if kind == "tree":
            continue
        if kind != "blob" or mode not in {"100644", "100755", None}:
            raise HTTPException(422, "Source tree contains a symlink, submodule, or unsupported entry")
        path, blob_sha = entry.get("path"), entry.get("sha" if provider == "github" else "id")
        size = entry.get("size")
        if (not isinstance(path, str) or not isinstance(blob_sha, str)
                or not re.fullmatch(r"[0-9a-f]{40,64}", blob_sha)
                or (size is not None and (not isinstance(size, int) or size > MAX_FILE_BYTES))):
            raise HTTPException(422, "Source tree contains an oversized or invalid file")
        if provider == "github":
            blob = await get(provider, token, f"/repos/{repository}/git/blobs/{blob_sha}")
        else:
            blob = await get(provider, token, f"/projects/{repository}/repository/blobs/{blob_sha}")
        if not isinstance(blob, dict) or blob.get("encoding") != "base64":
            raise HTTPException(502, "Source host returned an invalid blob")
        encoded = blob.get("content")
        if not isinstance(encoded, str) or len(encoded) > 1_400_000:
            raise HTTPException(422, "Source blob exceeds the pilot limit")
        try:
            raw = base64.b64decode("".join(encoded.split()), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(502, "Source host returned invalid blob encoding") from exc
        if len(raw) > MAX_FILE_BYTES or (size is not None and len(raw) != size):
            raise HTTPException(422, "Source blob size does not match its tree entry")
        git_object = b"blob " + str(len(raw)).encode("ascii") + b"\x00" + raw
        observed_blob_sha = (hashlib.sha1(git_object).hexdigest() if len(blob_sha) == 40
                             else hashlib.sha256(git_object).hexdigest())
        if observed_blob_sha != blob_sha:
            raise HTTPException(502, "Source blob does not match its immutable tree entry")
        files[path] = raw
        total += len(raw)
        if total > MAX_SNAPSHOT_BYTES:
            raise HTTPException(422, "Source snapshot exceeds the 10 MB pilot limit")
    try:
        digest = validate_snapshot(files)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return files, digest


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
