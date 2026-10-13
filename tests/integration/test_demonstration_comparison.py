import os
from pathlib import Path

import pytest

from app.domain.types import BudgetConfig
from app.orchestrator.benchmark import compare_searches
from app.orchestrator.budget import BudgetLedger, BudgetRunner
from app.providers.runner import LocalRunnerAdapter, PinnedPythonRunnerAdapter


def assert_equal_caps(result):
    assert result["status"] == "completed"
    assert len(result["rows"]) == 9
    assert {row["pair_budget"] for row in result["rows"]} == {4}
    assert {row["completed_pairs"] for row in result["rows"]} == {4}
    controls = [row for row in result["rows"] if row["case"] == "unchanged_control"]
    assert all(row["first_witness_pair"] is None for row in controls)
    for case in ("power_ratio", "sensor_offset"):
        informed = next(
            row
            for row in result["rows"]
            if row["case"] == case and row["strategy"] == "informed_sweep"
        )
        assert informed["first_witness_pair"] == 1


def test_seeded_equal_cap_comparison():
    result = compare_searches(LocalRunnerAdapter(), 4, 42)
    assert_equal_caps(result)
    again = compare_searches(LocalRunnerAdapter(), 4, 42)
    assert [[item["input"] for item in row["records"]] for row in result["rows"]] == [
        [item["input"] for item in row["records"]] for row in again["rows"]
    ]


def test_truncated_comparison_is_labeled_incomplete():
    ledger = BudgetLedger(BudgetConfig(max_candidates=1, max_runs=3))
    result = compare_searches(BudgetRunner(LocalRunnerAdapter(), ledger), 4)
    assert result["status"] == "budget_exhausted"
    assert result["rows"][0]["completed_pairs"] == 1
    assert ledger.usage["runs"] == 3


def test_actual_pinned_second_case_and_unchanged_controls():
    old, new = os.getenv("REPRODELTA_OLD_PYTHON"), os.getenv("REPRODELTA_NEW_PYTHON")
    if not old or not new:
        pytest.skip("Pinned test interpreters not configured")
    result = compare_searches(PinnedPythonRunnerAdapter(Path(old), Path(new)), 4)
    assert_equal_caps(result)
    sensor = next(
        row
        for row in result["rows"]
        if row["case"] == "sensor_offset" and row["strategy"] == "informed_sweep"
    )
    first = sensor["records"][0]
    assert first["runs"][0]["numpy_version"] == "1.26.4"
    assert first["runs"][1]["numpy_version"] == "2.0.2"
    assert first["comparison"]["old_ratio"] > 1
    assert first["comparison"]["new_ratio"] == 1


def test_report_exposes_scope_and_comparison_limitations():
    from datetime import UTC, datetime

    from app.domain.contract import FIXTURE_CONTRACT, hash_contract
    from app.domain.evidence import InvestigationEvidence
    from app.domain.types import VerdictKind
    from app.orchestrator.report import generate_report, generate_text_summary

    comparison = compare_searches(LocalRunnerAdapter(), 4, 42)
    evidence = InvestigationEvidence(
        investigation_id="comparison",
        project_name="curated",
        contract_hash=hash_contract(FIXTURE_CONTRACT),
        contract_body=FIXTURE_CONTRACT,
        controls=[],
        witness=None,
        reduction=None,
        verdict=VerdictKind.inconclusive,
        scope="constructed cases",
        timestamp=datetime.now(UTC),
        budget_usage={"runs": 72},
        demonstration_comparison=comparison,
    )
    report = generate_report(evidence)
    assert report["demonstration_comparison"]["rows"][0]["records"]
    assert any("Informed sweep" in line for line in report["limitations"])
    assert "72" in generate_text_summary(evidence)
