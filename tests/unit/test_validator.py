"""Unit tests for app/domain/validator.py."""
from __future__ import annotations

from app.domain.contract import FIXTURE_CONTRACT
from app.domain.validator import validate_input

VALID_SIGNAL = [1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5]


class TestValidSignal:
    def test_valid_signal_passes(self):
        result = validate_input(FIXTURE_CONTRACT, {"signal": VALID_SIGNAL})
        assert result.valid is True
        assert result.reason is None
        assert "signal" in result.checked_fields

    def test_checked_fields_populated(self):
        result = validate_input(FIXTURE_CONTRACT, {"signal": VALID_SIGNAL})
        assert result.checked_fields == ["signal"]

    def test_boundary_min_value(self):
        # Exact min value should pass
        signal = [-10.0] * 8
        result = validate_input(FIXTURE_CONTRACT, {"signal": signal})
        assert result.valid is True

    def test_boundary_max_value(self):
        # Exact max value should pass
        signal = [10.0] * 8
        result = validate_input(FIXTURE_CONTRACT, {"signal": signal})
        assert result.valid is True

    def test_integers_accepted_as_numbers(self):
        result = validate_input(FIXTURE_CONTRACT, {"signal": [1, 1, 1, 1, 0, 0, 0, 0]})
        assert result.valid is True


class TestMissingField:
    def test_missing_signal_fails(self):
        result = validate_input(FIXTURE_CONTRACT, {})
        assert result.valid is False
        assert "signal" in result.reason

    def test_missing_field_checked_fields_empty(self):
        result = validate_input(FIXTURE_CONTRACT, {})
        assert result.checked_fields == []


class TestWrongType:
    def test_non_list_signal_fails(self):
        result = validate_input(FIXTURE_CONTRACT, {"signal": 1.0})
        assert result.valid is False
        assert "list" in result.reason

    def test_dict_signal_fails(self):
        result = validate_input(FIXTURE_CONTRACT, {"signal": {"a": 1}})
        assert result.valid is False

    def test_string_element_fails(self):
        signal = ["a"] + VALID_SIGNAL[1:]
        result = validate_input(FIXTURE_CONTRACT, {"signal": signal})
        assert result.valid is False
        assert "not a number" in result.reason


class TestWrongLength:
    def test_too_short_fails(self):
        result = validate_input(FIXTURE_CONTRACT, {"signal": [1.0, 2.0, 3.0]})
        assert result.valid is False
        assert "length" in result.reason or "wrong" in result.reason.lower()

    def test_too_long_fails(self):
        result = validate_input(FIXTURE_CONTRACT, {"signal": VALID_SIGNAL + [1.0]})
        assert result.valid is False

    def test_empty_fails(self):
        result = validate_input(FIXTURE_CONTRACT, {"signal": []})
        assert result.valid is False


class TestOutOfRange:
    def test_below_min_fails(self):
        signal = [-11.0] + VALID_SIGNAL[1:]
        result = validate_input(FIXTURE_CONTRACT, {"signal": signal})
        assert result.valid is False
        assert "min_value" in result.reason or "below" in result.reason

    def test_above_max_fails(self):
        signal = [11.0] + VALID_SIGNAL[1:]
        result = validate_input(FIXTURE_CONTRACT, {"signal": signal})
        assert result.valid is False
        assert "max_value" in result.reason or "above" in result.reason


class TestNaN:
    def test_nan_fails(self):
        signal = [float("nan")] + VALID_SIGNAL[1:]
        result = validate_input(FIXTURE_CONTRACT, {"signal": signal})
        assert result.valid is False
        assert "NaN" in result.reason or "nan" in result.reason.lower()


class TestInf:
    def test_positive_inf_fails(self):
        signal = [float("inf")] + VALID_SIGNAL[1:]
        result = validate_input(FIXTURE_CONTRACT, {"signal": signal})
        assert result.valid is False
        assert "Inf" in result.reason or "inf" in result.reason.lower()

    def test_negative_inf_fails(self):
        signal = [float("-inf")] + VALID_SIGNAL[1:]
        result = validate_input(FIXTURE_CONTRACT, {"signal": signal})
        assert result.valid is False
