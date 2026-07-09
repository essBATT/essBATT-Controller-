"""Tests for external_control command parsing (Step 7)."""

import pytest
from datetime import datetime

from external_control import (
    ExternalCommandError,
    parse_bool_payload,
    parse_charge_to_soc_payload,
    parse_balancing_payload,
    apply_parsed_command,
)


# --- parse_bool_payload ---

@pytest.mark.parametrize("payload,expected", [
    (b"True", True),
    (b"true", True),
    (b"False", False),
    (b"false", False),
    ("True", True),
    ("false", False),
])
def test_parse_bool_payload_valid(payload, expected):
    assert parse_bool_payload(payload) is expected


def test_parse_bool_payload_invalid():
    with pytest.raises(ExternalCommandError):
        parse_bool_payload(b"maybe")


# --- parse_charge_to_soc_payload ---

def test_parse_charge_to_soc_full():
    result = parse_charge_to_soc_payload(b"1/90/20/12:00/01.01.2026")
    assert result["activated"] == "1"
    assert result["target_SOC"] == 90
    assert result["current_limit_input"] == 20
    assert result["time_input"] == "12:00"
    assert result["date_input"] == "01.01.2026"


def test_parse_charge_to_soc_optional_dashes():
    result = parse_charge_to_soc_payload(b"1/80/-/-/-")
    assert result["activated"] == "1"
    assert result["target_SOC"] == 80
    assert "current_limit_input" not in result
    assert "time_input" not in result
    assert "date_input" not in result


def test_parse_charge_to_soc_invalid_format():
    with pytest.raises(ExternalCommandError):
        parse_charge_to_soc_payload(b"1/not_a_number/-/-/-")


def test_parse_charge_to_soc_too_short():
    with pytest.raises(ExternalCommandError):
        parse_charge_to_soc_payload(b"1/80")


# --- parse_balancing_payload ---

def test_parse_balancing_full():
    result = parse_balancing_payload(b"1/15/08:00/02.02.2026")
    assert result["activated"] == "1"
    assert result["current_limit_input"] == 15
    assert result["time_input"] == "08:00"
    assert result["date_input"] == "02.02.2026"


def test_parse_balancing_minimal():
    result = parse_balancing_payload(b"0/-/-/-")
    assert result["activated"] == "0"
    assert "current_limit_input" not in result


def test_parse_balancing_invalid():
    with pytest.raises(ExternalCommandError):
        parse_balancing_payload(b"1")


# --- apply_parsed_command ---

def test_apply_charge_to_soc_sets_new_data_flag():
    ext = {}
    parsed = {"activated": "1", "target_SOC": 85, "current_limit_input": 10}
    ts = datetime(2026, 1, 1, 12, 0, 0)
    apply_parsed_command(ext, "charge_to_SOC", parsed, receive_time=ts)

    assert ext["new_data_received"] is True
    assert ext["charge_to_SOC"]["target_SOC"] == 85
    assert ext["charge_to_SOC"]["current_limit_input"] == 10
    assert ext["charge_to_SOC"]["receive_time"] == ts


def test_apply_balancing():
    ext = {}
    apply_parsed_command(ext, "balancing", {"activated": "1", "current_limit_input": 5})
    assert ext["balancing"]["activated"] == "1"
    assert ext["new_data_received"] is True


def test_apply_deactivate_does_not_set_new_data_flag():
    """Deactivate flags are polled each cycle; they do not use new_data_received."""
    ext = {}
    apply_parsed_command(ext, "deactivate_charge", True)
    assert ext["deactivate_charge"]["activated"] is True
    assert "new_data_received" not in ext

    apply_parsed_command(ext, "deactivate_discharge", False)
    assert ext["deactivate_discharge"]["activated"] is False
