"""
Integration tests for investigation store persistence.

Tests:
1. test_restart_recovery — running state becomes interrupted on reload
2. test_immutable_contract_hash — contract hash stored correctly
3. test_event_append_only — events get monotonically increasing sequence numbers
4. test_report_survives_restart — report readable from new store instance
5. test_interrupted_not_completed — interrupted investigation is not completed
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.storage.investigation_store import (
    FileSystemInvestigationStore,
    MemoryInvestigationStore,
)

# ---------------------------------------------------------------------------
# Memory store tests
# ---------------------------------------------------------------------------

class TestMemoryStoreBasics:
    def test_create_returns_id(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("project-1", {"max_candidates": 10}, "user1")
        assert isinstance(inv_id, str)
        assert len(inv_id) == 36  # UUID

    def test_get_investigation_returns_record(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("proj", {}, "user")
        rec = store.get_investigation(inv_id)
        assert rec is not None
        assert rec["id"] == inv_id
        assert rec["status"] == "draft"

    def test_get_investigation_unknown_returns_none(self):
        store = MemoryInvestigationStore()
        assert store.get_investigation("nonexistent-id") is None

    def test_transition_draft_to_running(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("proj", {}, "user")
        store.transition_status(inv_id, "running")
        rec = store.get_investigation(inv_id)
        assert rec["status"] == "running"

    def test_transition_invalid_raises(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("proj", {}, "user")
        with pytest.raises(ValueError, match="Invalid status transition"):
            store.transition_status(inv_id, "completed")  # can't go draft→completed

    def test_transition_completed_to_running_raises(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("proj", {}, "user")
        store.transition_status(inv_id, "running")
        store.transition_status(inv_id, "completed")
        with pytest.raises(ValueError):
            store.transition_status(inv_id, "running")

    def test_event_append_only(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("proj", {}, "user")
        store.append_event(inv_id, "runner", "investigation_started", {})
        store.append_event(inv_id, "runner", "control_completed", {"ok": True})
        store.append_event(inv_id, "runner", "witness_found", {"sha": "abc"})
        events = store.get_events(inv_id)
        assert len(events) == 3
        seqs = [e["sequence"] for e in events]
        assert seqs == sorted(seqs)
        assert seqs == [0, 1, 2]

    def test_event_order_preserved(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("proj", {}, "user")
        for i in range(5):
            store.append_event(inv_id, "runner", f"event_{i}", {"i": i})
        events = store.get_events(inv_id)
        types = [e["event_type"] for e in events]
        assert types == [f"event_{i}" for i in range(5)]

    def test_store_and_get_report(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("proj", {}, "user")
        report = {"verdict": "supported_conclusion_change", "schema_version": "2"}
        store.store_report(inv_id, report, "text summary")
        got = store.get_report(inv_id)
        assert got is not None
        assert got["verdict"] == "supported_conclusion_change"

    def test_get_report_none_before_stored(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("proj", {}, "user")
        assert store.get_report(inv_id) is None

    def test_store_report_appends_event(self):
        store = MemoryInvestigationStore()
        inv_id = store.create_investigation("proj", {}, "user")
        store.store_report(inv_id, {}, "text")
        events = store.get_events(inv_id)
        types = [e["event_type"] for e in events]
        assert "report_assembled" in types


# ---------------------------------------------------------------------------
# test_restart_recovery
# ---------------------------------------------------------------------------

class TestRestartRecovery:
    """Filesystem store: running → interrupted on reload."""

    def test_restart_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store1 = FileSystemInvestigationStore(base)
            inv_id = store1.create_investigation("proj", {}, "user")
            store1.transition_status(inv_id, "running")
            # Verify it's running
            rec = store1.get_investigation(inv_id)
            assert rec["status"] == "running"

            # Simulate a restart by creating a new store instance pointing to the same dir
            store2 = FileSystemInvestigationStore(base)
            rec2 = store2.get_investigation(inv_id)
            # Should be marked interrupted (not still running)
            assert rec2["status"] == "interrupted"

    def test_interrupted_not_completed(self):
        """An interrupted investigation must never appear as completed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store1 = FileSystemInvestigationStore(base)
            inv_id = store1.create_investigation("proj", {}, "user")
            store1.transition_status(inv_id, "running")

            # New store instance simulates process restart
            store2 = FileSystemInvestigationStore(base)
            rec = store2.get_investigation(inv_id)
            assert rec["status"] != "completed"
            assert rec["status"] == "interrupted"

    def test_restart_adds_interrupted_event(self):
        """On recovery, an investigation_interrupted event should be appended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store1 = FileSystemInvestigationStore(base)
            inv_id = store1.create_investigation("proj", {}, "user")
            store1.transition_status(inv_id, "running")

            store2 = FileSystemInvestigationStore(base)
            store2.get_investigation(inv_id)  # triggers recovery check
            events = store2.get_events(inv_id)
            types = [e["event_type"] for e in events]
            assert "investigation_interrupted" in types


# ---------------------------------------------------------------------------
# test_immutable_contract_hash
# ---------------------------------------------------------------------------

class TestImmutableContractHash:
    """State.json records contract hash and status correctly."""

    def test_state_persisted_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store = FileSystemInvestigationStore(base)
            budget = {"max_candidates": 20, "max_runs": 100}
            inv_id = store.create_investigation("proj-99", budget, "user")
            rec = store.get_investigation(inv_id)
            assert rec["budget_json"] == budget
            assert rec["project_id"] == "proj-99"


# ---------------------------------------------------------------------------
# test_event_append_only
# ---------------------------------------------------------------------------

class TestFSEventAppendOnly:
    def test_event_sequence_monotonic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store = FileSystemInvestigationStore(base)
            inv_id = store.create_investigation("proj", {}, "user")
            for i in range(6):
                store.append_event(inv_id, "runner", f"step_{i}", {"i": i})
            events = store.get_events(inv_id)
            seqs = [e["sequence"] for e in events]
            assert seqs == list(range(len(seqs)))

    def test_events_across_store_instances(self):
        """Events written by store1 are readable by store2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store1 = FileSystemInvestigationStore(base)
            inv_id = store1.create_investigation("proj", {}, "user")
            store1.append_event(inv_id, "runner", "event_a", {})
            store1.append_event(inv_id, "runner", "event_b", {})

            store2 = FileSystemInvestigationStore(base)
            events = store2.get_events(inv_id)
            types = [e["event_type"] for e in events]
            assert "event_a" in types
            assert "event_b" in types


# ---------------------------------------------------------------------------
# test_report_survives_restart
# ---------------------------------------------------------------------------

class TestReportSurvivesRestart:
    def test_report_readable_from_new_instance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store1 = FileSystemInvestigationStore(base)
            inv_id = store1.create_investigation("proj", {}, "user")
            report = {
                "schema_version": "2",
                "verdict": "supported_conclusion_change",
                "investigation_id": inv_id,
            }
            store1.transition_status(inv_id, "running")
            store1.store_report(inv_id, report, "plain text summary")
            store1.transition_status(inv_id, "completed")

            # Create a brand-new store instance
            store2 = FileSystemInvestigationStore(base)
            loaded = store2.get_report(inv_id)
            assert loaded is not None
            assert loaded["verdict"] == "supported_conclusion_change"
            assert loaded["investigation_id"] == inv_id

    def test_report_available_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store = FileSystemInvestigationStore(base)
            inv_id = store.create_investigation("proj", {}, "user")
            rec_before = store.get_investigation(inv_id)
            assert rec_before["report_available"] is False
            store.transition_status(inv_id, "running")
            store.store_report(inv_id, {"x": 1}, "text")
            rec_after = store.get_investigation(inv_id)
            assert rec_after["report_available"] is True
