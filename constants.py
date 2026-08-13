# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Constants for essBATT Controller."""

DEBUGGING_ON = False  # Switch for debugging

# Script internal parameters
CHARGE_LIMIT_RESET_DISCHARGE_CURRENT = -4.0
DISCHARGE_LIMIT_RESET_CHARGE_CURRENT = 4.0
CERBO_KEEPALIVE_LENGTH = 30.0  # [s] Keepalive interval to Cerbo (dbus-mqtt)
# MQTT protocol keepalive to the local broker (paho connect keepalive=)
MQTT_BROKER_PROTOCOL_KEEPALIVE_S = 60
# Legacy alias (same value; prefer MQTT_BROKER_PROTOCOL_KEEPALIVE_S)
MQTT_SERVER_TIMEOUT_TIMESPAN = MQTT_BROKER_PROTOCOL_KEEPALIVE_S
# How long to wait for the first successful CONNACK before giving up
MQTT_INITIAL_CONNECT_TIMEOUT_S = 60.0
# Log a warning if broker has been down at least this long (and every interval after)
MQTT_DISCONNECT_WARN_AFTER_S = 30.0
MQTT_DISCONNECT_WARN_INTERVAL_S = 60.0

LOGLEVEL_NAME_TO_NUMBER = {
    'CRITICAL': 50, 'FATAL': 50, 'ERROR': 40, 'WARNING': 30, 'WARN': 30,
    'INFO': 20, 'DEBUG': 10, 'NOTSET': 0
}


def resolve_loglevel(name, default='INFO'):
    """Map a config debug_level string to a logging level number.

    Unknown or empty values fall back to ``default`` (INFO). Comparison is
    case-insensitive so ``info`` and ``INFO`` both work.
    """
    key = str(name if name is not None else default).strip().upper()
    if key in LOGLEVEL_NAME_TO_NUMBER:
        return LOGLEVEL_NAME_TO_NUMBER[key]
    fallback = str(default).strip().upper()
    return LOGLEVEL_NAME_TO_NUMBER.get(fallback, LOGLEVEL_NAME_TO_NUMBER['INFO'])


VRM_ID_PLACEHOLDERS = frozenset({
    '',
    'YOUR VRM ID',
    'unknown',
})
MULTIS_SWITCH_NUMBER_STRING_MAPPING = {
    '1': "CHARGER ON INVERTER OFF",
    '2': "INVERTER ON CHARGER OFF",
    '3': "INVERTER AND CHARGER ON",
    '4': "INVERTER AND CHARGER OFF"
}

# Safe default values when config is missing or incomplete (no magic numbers in business logic)
DEFAULT_MAX_BATTERY_DISCHARGE_CURRENT = 5.0
DEFAULT_MAX_BATTERY_CHARGE_CURRENT = 5.0

# Watchdog liveness: controller publishes a small MQTT heartbeat so the
# independent watchdog can detect controller failure.
# Interval must stay well below watchdog
# ``essBATT_controller_timeout_detection_duration`` (default 95s).
DEFAULT_CONTROLLER_HEARTBEAT_TOPIC = 'essbatt/controller/heartbeat'
DEFAULT_CONTROLLER_HEARTBEAT_INTERVAL_S = 30.0

# If required CCGX fields stay incomplete this long, force charge/discharge
# limits to 0 instead of leaving the last Venus setpoints in place.
# Brief meter/charger dropouts (a few seconds) are tolerated.
DEFAULT_INCOMPLETE_DATA_SAFE_STATE_TIMEOUT_S = 30.0
