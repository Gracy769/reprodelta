"""Unit tests for domain types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.types import BudgetConfig, InvestigationStatus, VerdictKind


class TestVerdictKind:
    """VerdictKind must contain exactly the 7 values specified in the PRD."""

    EXPECTED = {
        "observed_drift",
        "supported_conclusion_change",
        "pre_existing_contract_violation",
        "repair_passes_stated_checks",
        "no_violation_found",
        "inconclusive",
        "setup_failure",
    }

    def test_count(self):
        assert len(VerdictKind) == 7, (
            f"Expected 7 VerdictKind members, got {len(VerdictKind)}"
        )

    def test_all_expected_values_present(self):
        actual = {v.value for v in VerdictKind}
        assert actual == self.EXPECTED

    def test_string_coercion(self):
        """VerdictKind is a str enum — values should equal their string representations."""
        for member in VerdictKind:
            assert member == member.value
            assert VerdictKind(member.value) is member


class TestInvestigationStatus:
    def test_has_terminal_states(self):
        terminal = {"completed", "failed", "cancelled"}
        actual = {v.value for v in InvestigationStatus}
        assert terminal.issubset(actual)

    def test_draft_is_initial_state(self):
        assert InvestigationStatus.draft.value == "draft"


class TestBudgetConfig:
    def test_defaults_are_valid(self):
        b = BudgetConfig()
        assert b.max_candidates == 500
        assert b.max_runs == 2000
        assert b.max_wall_seconds == 3600
        assert b.max_llm_calls == 50
        assert b.max_cost_usd == 10.0

    def test_custom_valid(self):
        b = BudgetConfig(max_candidates=10, max_runs=20, max_wall_seconds=60)
        assert b.max_candidates == 10
        assert b.max_runs == 20

    def test_max_candidates_must_be_positive(self):
        with pytest.raises(ValidationError):
            BudgetConfig(max_candidates=0)

    def test_max_runs_must_be_positive(self):
        with pytest.raises(ValidationError):
            BudgetConfig(max_runs=0)

    def test_max_runs_must_be_gte_candidates(self):
        with pytest.raises(ValidationError):
            BudgetConfig(max_candidates=100, max_runs=50)

    def test_max_runs_equal_to_candidates_is_ok(self):
        b = BudgetConfig(max_candidates=100, max_runs=100)
        assert b.max_runs == 100

    def test_max_cost_must_be_positive(self):
        with pytest.raises(ValidationError):
            BudgetConfig(max_cost_usd=0.0)

    def test_exceeding_upper_bounds_raises(self):
        with pytest.raises(ValidationError):
            BudgetConfig(max_candidates=99_999)
        with pytest.raises(ValidationError):
            BudgetConfig(max_cost_usd=5_000.0)
