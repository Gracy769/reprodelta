import base64
from types import SimpleNamespace

import pytest

from app.orchestrator import project_worker
from app.storage.project_snapshots import ProjectSnapshotStore


def test_snapshot_codec_is_bounded_and_validates_key_and_paths():
    with pytest.raises(ValueError, match="32-byte key"):
        ProjectSnapshotStore("postgresql://unused", "not-a-key")
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    store = ProjectSnapshotStore("postgresql://unused", key)
    files = {"package/adapter.py": b"hello\n", "requirements.txt": b"numpy==1.26.4\n"}
    assert store._decode(store._encode(files)) == files
    with pytest.raises(ValueError):
        store._aad("github:owner", "not-a-digest")
    with pytest.raises(ValueError):
        store._decode(b"not-compressed")


def test_worker_rejects_missing_snapshot_key_before_database_access(monkeypatch):
    monkeypatch.setattr(project_worker, "get_settings", lambda: SimpleNamespace(
        project_snapshot_key=None))
    with pytest.raises(project_worker.RunRejected, match="snapshot_storage_unconfigured"):
        project_worker._stored_snapshot("github:owner", {"a.py": b"pass"}, "a" * 64)
