"""Tests for constants module (Step 1)."""

import constants


def test_constants_defined():
    """All expected constants are present and have correct types/values."""
    assert isinstance(constants.DEBUGGING_ON, bool)
    assert constants.CERBO_KEEPALIVE_LENGTH == 30.0
    assert constants.MQTT_BROKER_PROTOCOL_KEEPALIVE_S == 60
    assert constants.MQTT_SERVER_TIMEOUT_TIMESPAN == constants.MQTT_BROKER_PROTOCOL_KEEPALIVE_S
    assert constants.MQTT_INITIAL_CONNECT_TIMEOUT_S == 60.0
    assert constants.MQTT_DISCONNECT_WARN_AFTER_S == 30.0
    assert isinstance(constants.LOGLEVEL_NAME_TO_NUMBER, dict)
    assert isinstance(constants.MULTIS_SWITCH_NUMBER_STRING_MAPPING, dict)
    assert "INFO" in constants.LOGLEVEL_NAME_TO_NUMBER
    assert "3" in constants.MULTIS_SWITCH_NUMBER_STRING_MAPPING
    assert constants.DEFAULT_CONTROLLER_HEARTBEAT_TOPIC == 'essbatt/controller/heartbeat'
    assert constants.DEFAULT_CONTROLLER_HEARTBEAT_INTERVAL_S == 30.0
    assert constants.resolve_loglevel('info') == 20
    assert constants.resolve_loglevel('DEBUG') == 10
    assert constants.resolve_loglevel('nope') == 20
    assert constants.resolve_loglevel(None) == 20

def test_mapping_values():
    """Check that mappings contain expected values for safety-critical switches."""
    mapping = constants.MULTIS_SWITCH_NUMBER_STRING_MAPPING
    assert mapping["3"] == "INVERTER AND CHARGER ON"
    assert mapping["4"] == "INVERTER AND CHARGER OFF"
