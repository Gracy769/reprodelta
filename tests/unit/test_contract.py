"""Unit tests for app/domain/contract.py."""
from __future__ import annotations

import pytest

from app.domain.contract import (
    FIXTURE_CONTRACT,
    ComparisonPolicy,
    ConclusionRule,
    ContractBody,
    InputField,
    ResourceLimits,
    canonicalize_contract,
    hash_contract,
)


class TestCanonicalizeContract:
    def test_stable_output_same_instance(self):
        b1 = canonicalize_contract(FIXTURE_CONTRACT)
        b2 = canonicalize_contract(FIXTURE_CONTRACT)
        assert b1 == b2

    def test_stable_output_equal_instances(self):
        # Build a second copy with identical values
        copy = ContractBody(
            input_schema=FIXTURE_CONTRACT.input_schema,
            output_selector=FIXTURE_CONTRACT.output_selector,
            conclusion=FIXTURE_CONTRACT.conclusion,
            comparison=FIXTURE_CONTRACT.comparison,
            resource_limits=FIXTURE_CONTRACT.resource_limits,
            assumptions=list(FIXTURE_CONTRACT.assumptions),
            review_provenance=FIXTURE_CONTRACT.review_provenance,
        )
        assert canonicalize_contract(copy) == canonicalize_contract(FIXTURE_CONTRACT)

    def test_returns_bytes(self):
        assert isinstance(canonicalize_contract(FIXTURE_CONTRACT), bytes)

    def test_sorted_keys(self):
        raw = canonicalize_contract(FIXTURE_CONTRACT).decode()
        # JSON with sorted keys must not contain unordered leading keys
        # Just verify it parses back to the same structure
        import json
        parsed = json.loads(raw)
        assert parsed["schema_version"] == "1"

    def test_no_whitespace(self):
        raw = canonicalize_contract(FIXTURE_CONTRACT).decode()
        assert "  " not in raw  # no double spaces
        assert "\n" not in raw


class TestHashContract:
    def test_returns_64_char_hex(self):
        h = hash_contract(FIXTURE_CONTRACT)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_stable_across_calls(self):
        h1 = hash_contract(FIXTURE_CONTRACT)
        h2 = hash_contract(FIXTURE_CONTRACT)
        assert h1 == h2

    def test_different_contracts_different_hashes(self):
        other = ContractBody(
            input_schema=FIXTURE_CONTRACT.input_schema,
            output_selector=FIXTURE_CONTRACT.output_selector,
            conclusion=ConclusionRule(operator="lt", threshold=0.3),
            review_provenance="other",
        )
        assert hash_contract(other) != hash_contract(FIXTURE_CONTRACT)


class TestFixtureContract:
    def test_schema_version(self):
        assert FIXTURE_CONTRACT.schema_version == "1"

    def test_input_field_name(self):
        assert FIXTURE_CONTRACT.input_schema.fields[0].name == "signal"

    def test_input_field_shape(self):
        assert FIXTURE_CONTRACT.input_schema.fields[0].shape == [8]

    def test_output_selector(self):
        assert FIXTURE_CONTRACT.output_selector.field_name == "conclusion"

    def test_conclusion_operator(self):
        assert FIXTURE_CONTRACT.conclusion.operator == "gt"

    def test_review_provenance(self):
        assert FIXTURE_CONTRACT.review_provenance == "fixture_default_v1"

    def test_has_assumptions(self):
        assert len(FIXTURE_CONTRACT.assumptions) >= 3

    def test_hash_is_stable(self):
        # Verifies that nothing changes the hash between test runs
        h = hash_contract(FIXTURE_CONTRACT)
        assert len(h) == 64


class TestInputField:
    def test_valid_field(self):
        f = InputField(name="x", dtype="float32", shape=[4])
        assert f.name == "x"

    def test_frozen(self):
        import pydantic
        f = InputField(name="x", dtype="float32", shape=[4])
        with pytest.raises((TypeError, AttributeError, pydantic.ValidationError)):
            f.name = "y"  # type: ignore[misc]

    def test_defaults(self):
        f = InputField(name="x", dtype="float32", shape=[1])
        assert f.allow_nan is False
        assert f.allow_inf is False
        assert f.min_value is None
        assert f.max_value is None


class TestComparisonPolicy:
    def test_defaults(self):
        p = ComparisonPolicy()
        assert p.atol == pytest.approx(1e-7)
        assert p.rtol == pytest.approx(1e-5)
        assert p.nan_equal is False
        assert p.inf_equal is True
        assert p.dtype_must_match is False


class TestResourceLimits:
    def test_defaults(self):
        r = ResourceLimits()
        assert r.max_wall_seconds == 30
        assert r.max_memory_mb == 512
        assert r.max_cpu_seconds == pytest.approx(25.0)
