"""Unit tests for content-addressed artifact storage."""

from __future__ import annotations

import hashlib
import os

import pytest

from app.domain.types import ArtifactRef
from app.storage.artifacts import load_artifact, store_artifact


@pytest.fixture(autouse=True)
def tmp_artifact_dir(monkeypatch, tmp_path):
    """Redirect ARTIFACT_DIR to a temporary directory for each test."""
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    # Reset the cached settings singleton so the new env var is picked up.
    import app.config as cfg
    cfg._settings = None
    yield tmp_path
    cfg._settings = None


class TestStoreArtifact:
    def test_returns_artifact_ref(self):
        ref = store_artifact(b"hello world")
        assert isinstance(ref, ArtifactRef)

    def test_sha256_is_correct(self):
        data = b"test content"
        expected = hashlib.sha256(data).hexdigest()
        ref = store_artifact(data)
        assert ref.sha256 == expected

    def test_byte_size_is_correct(self):
        data = b"test content"
        ref = store_artifact(data)
        assert ref.byte_size == len(data)

    def test_storage_key_uses_prefix_sharding(self):
        data = b"test content"
        ref = store_artifact(data)
        assert ref.storage_key.startswith(ref.sha256[:2] + "/")
        assert ref.sha256 in ref.storage_key

    def test_file_exists_at_storage_key(self, tmp_artifact_dir):
        data = b"hello"
        ref = store_artifact(data)
        dest = tmp_artifact_dir / ref.sha256[:2] / ref.sha256
        assert dest.exists()

    def test_idempotent_double_store(self):
        data = b"idempotent"
        ref1 = store_artifact(data)
        ref2 = store_artifact(data)
        assert ref1 == ref2

    def test_media_type_forwarded(self):
        ref = store_artifact(b"json", media_type="application/json")
        assert ref.media_type == "application/json"

    def test_empty_bytes(self):
        ref = store_artifact(b"")
        assert ref.byte_size == 0
        assert len(ref.sha256) == 64


class TestLoadArtifact:
    def test_round_trip(self):
        data = b"round trip content"
        ref = store_artifact(data)
        loaded = load_artifact(ref.sha256)
        assert loaded == data

    def test_missing_raises_file_not_found(self):
        fake_sha = "a" * 64
        with pytest.raises(FileNotFoundError):
            load_artifact(fake_sha)

    def test_invalid_sha_raises_value_error(self):
        with pytest.raises(ValueError):
            load_artifact("not-a-valid-sha256")

    def test_corrupted_content_raises_value_error(self, tmp_artifact_dir):
        data = b"original"
        ref = store_artifact(data)
        # Corrupt the file in place.
        dest = tmp_artifact_dir / ref.sha256[:2] / ref.sha256
        dest.write_bytes(b"corrupted")
        with pytest.raises(ValueError, match="integrity"):
            load_artifact(ref.sha256)

    def test_large_payload(self):
        data = os.urandom(1024 * 1024)  # 1 MiB
        ref = store_artifact(data)
        loaded = load_artifact(ref.sha256)
        assert loaded == data
