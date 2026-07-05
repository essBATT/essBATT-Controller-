"""Tests for constants module (Step 1)."""

import constants


def test_constants_defined():
    """All expected constants are present and have correct types/values."""
    assert isinstance(constants.DEBUGGING_ON, bool)
    assert constants.CERBO_KEEPALIVE_LENGTH == 30.0
    assert constants.MQTT_SERVER_TIMEOUT_TIMESPAN == 60
    assert isinstance(constants.LOGLEVEL_NAME_TO_NUMBER, dict)
    assert isinstance(constants.MULTIS_SWITCH_NUMBER_STRING_MAPPING, dict)
    assert "INFO" in constants.LOGLEVEL_NAME_TO_NUMBER
    assert "3" in constants.MULTIS_SWITCH_NUMBER_STRING_MAPPING


def test_mapping_values():
    """Check that mappings contain expected values for safety-critical switches."""
    mapping = constants.MULTIS_SWITCH_NUMBER_STRING_MAPPING
    assert mapping["3"] == "INVERTER AND CHARGER ON"
    assert mapping["4"] == "INVERTER AND CHARGER OFF"
