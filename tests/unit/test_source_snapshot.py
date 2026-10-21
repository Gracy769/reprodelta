"""Immutable source fetch never admits a partial tree or changed blob."""

import base64
import hashlib

import pytest
from fastapi import HTTPException

from app.api.source_analysis import immutable_snapshot


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\x00" + content).hexdigest()


@pytest.mark.asyncio
async def test_snapshot_checks_git_blob_and_digest():
    content = b"def measure(values):\n    return {'result': {'score': sum(values)}}\n"
    digest = blob_sha(content)

    async def get(provider, token, path, params=None):
        if "/trees/" in path:
            return {
                "truncated": False,
                "tree": [
                    {"type": "tree", "path": "src"},
                    {
                        "type": "blob",
                        "mode": "100644",
                        "path": "src/measure.py",
                        "sha": digest,
                        "size": len(content),
                    },
                ],
            }
        return {"encoding": "base64", "content": base64.b64encode(content).decode()}

    files, source_digest = await immutable_snapshot(get, "github", "token", "owner/repo", "a" * 40)
    assert files == {"src/measure.py": content}
    assert len(source_digest) == 64


@pytest.mark.asyncio
async def test_snapshot_rejects_truncated_tree_and_blob_mismatch():
    async def truncated(provider, token, path, params=None):
        return {"truncated": True, "tree": []}

    with pytest.raises(HTTPException, match="incomplete"):
        await immutable_snapshot(truncated, "github", "token", "owner/repo", "a" * 40)

    async def mismatched(provider, token, path, params=None):
        if "/trees/" in path:
            return {
                "truncated": False,
                "tree": [
                    {
                        "type": "blob",
                        "mode": "100644",
                        "path": "run.py",
                        "sha": "b" * 40,
                        "size": 1,
                    },
                ],
            }
        return {"encoding": "base64", "content": base64.b64encode(b"x").decode()}

    with pytest.raises(HTTPException, match="does not match"):
        await immutable_snapshot(mismatched, "github", "token", "owner/repo", "a" * 40)


@pytest.mark.asyncio
async def test_snapshot_rejects_missing_file_mode():
    async def get(provider, token, path, params=None):
        return {"truncated": False, "tree": [
            {"type": "blob", "path": "linked.py", "sha": "b" * 40, "size": 1},
        ]}

    with pytest.raises(HTTPException, match="symlink"):
        await immutable_snapshot(get, "github", "token", "owner/repo", "a" * 40)
