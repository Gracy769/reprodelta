"""Regression coverage for contract decisions and incomplete worker evidence."""
import pytest

from app.domain.contract import FIXTURE_CONTRACT
from app.domain.types import VerdictKind
from app.orchestrator.executor import compare_outputs


def output(ratio=0.6, conclusion="above_threshold", dtype="float64"):
    return {"status": "ok", "outputs": {"result": {
        "ratio": ratio, "conclusion": conclusion, "intermediate_dtype": dtype,
    }}}


@pytest.mark.parametrize("ratio", [None, "0.6", True, float("nan"), float("inf")])
def test_invalid_measurement_never_becomes_a_negative_finding(ratio):
    result = compare_outputs(output(ratio), output(), FIXTURE_CONTRACT)
    assert result["verdict_hint"] == VerdictKind.setup_failure
    assert result["conclusion_changed"] is None


@pytest.mark.parametrize("conclusion", [None, "", "unexpected"])
def test_invalid_classification_is_rejected(conclusion):
    result = compare_outputs(output(conclusion=conclusion), output(), FIXTURE_CONTRACT)
    assert result["verdict_hint"] == VerdictKind.setup_failure


def test_two_missing_results_do_not_establish_repeatability_or_no_change():
    result = compare_outputs({"status": "ok", "outputs": {}}, {"status": "ok", "outputs": {}}, FIXTURE_CONTRACT)
    assert result["verdict_hint"] == VerdictKind.setup_failure
    assert result["conclusion_changed"] is None


def test_contract_decides_exact_tie_without_rewriting_worker_observation():
    result = compare_outputs(output(0.5, "below_threshold"), output(0.5, "below_threshold"), FIXTURE_CONTRACT)
    assert result["old_conclusion"] == "inconclusive"
    assert result["old_reported_conclusion"] == "below_threshold"
    assert result["verdict_hint"] == VerdictKind.inconclusive


def test_tolerance_does_not_move_the_classification_boundary():
    result = compare_outputs(output(0.50000001), output(0.5, "below_threshold"), FIXTURE_CONTRACT)
    assert result["numeric_equal"] is True
    assert result["conclusion_changed"] is True
    assert result["new_conclusion"] == "inconclusive"


def test_numeric_drift_without_changed_conclusion_is_reported_separately():
    result = compare_outputs(output(0.6), output(0.7), FIXTURE_CONTRACT)
    assert result["conclusion_changed"] is False
    assert result["verdict_hint"] == VerdictKind.observed_drift


def test_required_dtype_difference_is_observed_drift():
    contract = FIXTURE_CONTRACT.model_copy(update={"comparison": FIXTURE_CONTRACT.comparison.model_copy(update={"dtype_must_match": True})})
    result = compare_outputs(output(dtype="float64"), output(dtype="float32"), contract)
    assert result["verdict_hint"] == VerdictKind.observed_drift


@pytest.mark.parametrize("tie,expected", [("above", "above_threshold"), ("below", "below_threshold"), ("inconclusive", "inconclusive")])
def test_explicit_tie_policies(tie, expected):
    contract = FIXTURE_CONTRACT.model_copy(update={"conclusion": FIXTURE_CONTRACT.conclusion.model_copy(update={"tie_behavior": tie})})
    result = compare_outputs(output(0.5), output(0.5), contract)
    assert result["old_conclusion"] == expected


@pytest.mark.parametrize("op,ratio,expected", [("gt", .7, "above_threshold"), ("gte", .3, "below_threshold"), ("lt", .3, "above_threshold"), ("lte", .7, "below_threshold"), ("eq", .7, "below_threshold"), ("ne", .7, "above_threshold")])
def test_declared_operator_controls_decision(op, ratio, expected):
    contract = FIXTURE_CONTRACT.model_copy(update={"conclusion": FIXTURE_CONTRACT.conclusion.model_copy(update={"operator": op})})
    assert compare_outputs(output(ratio), output(ratio), contract)["old_conclusion"] == expected
