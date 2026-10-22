"""Generated search inputs must never escape the approved numeric contract."""

import pytest

from app.domain.contract import InputField, InputSchema
from app.domain.project_candidates import candidate_inputs, feedback_candidates
from app.domain.validator import validate_input
from tests.integration.test_project_profiles import _draft


def test_sweep_and_seeded_random_are_bounded_valid_and_reproducible():
    draft = _draft()
    sweep = candidate_inputs(draft.contract, draft.sample_input, strategy="sweep", budget=3)
    assert [item["x"] for item in sweep] == [0.5, 0, 1]
    first = candidate_inputs(draft.contract, draft.sample_input,
                             strategy="seeded_random", budget=8, seed=123)
    second = candidate_inputs(draft.contract, draft.sample_input,
                              strategy="seeded_random", budget=8, seed=123)
    assert first == second
    assert 1 <= len(first) <= 8
    assert all(validate_input(draft.contract, item).valid for item in first)
    with pytest.raises(ValueError, match="1–32"):
        candidate_inputs(draft.contract, draft.sample_input, strategy="sweep", budget=33)


def test_array_candidates_change_one_coordinate_without_mutating_sample():
    draft = _draft()
    contract = draft.contract.model_copy(update={"input_schema": InputSchema(fields=[
        InputField(name="x", dtype="float32", shape=[3], min_value=-1, max_value=1)
    ])})
    sample = {"x": [0.1, 0.2, 0.3]}
    results = candidate_inputs(contract, sample, strategy="sweep", budget=9)
    assert sample == {"x": [0.1, 0.2, 0.3]}
    assert len(results) == 9
    assert all(sum(a != b for a, b in zip(item["x"], sample["x"], strict=True)) <= 1
               for item in results)
    assert all(validate_input(contract, item).valid for item in results)


def test_integer_generation_clamps_to_declared_dtype():
    draft = _draft()
    contract = draft.contract.model_copy(update={"input_schema": InputSchema(fields=[
        InputField(name="x", dtype="int32", shape=[], min_value=-1e100, max_value=1e100)
    ])})
    results = candidate_inputs(contract, {"x": 1}, strategy="sweep", budget=4)
    assert results[1]["x"] == -(2**31)
    assert max(item["x"] for item in results) <= 2**31 - 1


def test_invalid_sample_and_undeclared_fields_never_generate_candidates():
    draft = _draft()
    with pytest.raises(ValueError, match="does not satisfy"):
        candidate_inputs(draft.contract, {"x": 2}, strategy="sweep", budget=4)
    with pytest.raises(ValueError, match="undeclared"):
        candidate_inputs(draft.contract, {"x": 0.5, "secret": 1},
                         strategy="sweep", budget=4)


def test_feedback_inputs_are_reproducible_unique_and_contract_checked():
    draft = _draft()
    measured = [({"x": 0.5}, 0.49, 0.51), ({"x": 0.0}, 0.1, 0.2)]
    first = feedback_candidates(draft.contract, draft.sample_input, measured,
                                budget=4, seed=13)
    assert first == feedback_candidates(draft.contract, draft.sample_input, measured,
                                        budget=4, seed=13)
    assert 0 < len(first) <= 4
    assert len({item["x"] for item in first}) == len(first)
    assert all(item not in [row[0] for row in measured] for item in first)
    assert all(validate_input(draft.contract, item).valid for item in first)
    with pytest.raises(ValueError, match="finite measurements"):
        feedback_candidates(draft.contract, draft.sample_input,
                            [({"x": 0.5}, float("nan"), 0.5)], budget=2)
