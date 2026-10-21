from app.domain.comparison import evaluate_output
from app.domain.contract import (
    ConclusionRule,
    ContractBody,
    InputField,
    InputSchema,
    OutputSelector,
)
from app.domain.validator import validate_input


def test_scalar_input_and_numeric_output_for_approved_project():
    contract = ContractBody(
        input_schema=InputSchema(
            fields=[InputField(name="x", dtype="float64", shape=[], min_value=0, max_value=1)]
        ),
        output_selector=OutputSelector(field_name="measurement"),
        conclusion=ConclusionRule(operator="gt", threshold=0.5),
    )
    assert validate_input(contract, {"x": 0.2}).valid
    assert not validate_input(contract, {"x": [0.2]}).valid
    assert (
        evaluate_output({"result": {"measurement": 0.8}}, contract)["conclusion"]
        == "above_threshold"
    )
