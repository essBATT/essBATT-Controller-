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
MULTIS_SWITCH_NUMBER_STRING_MAPPING = {
    '1': "CHARGER ON INVERTER OFF",
    '2': "INVERTER ON CHARGER OFF",
    '3': "INVERTER AND CHARGER ON",
    '4': "INVERTER AND CHARGER OFF"
}

# Safe default values when config is missing or incomplete (no magic numbers in business logic)
DEFAULT_MAX_BATTERY_DISCHARGE_CURRENT = 5.0
DEFAULT_MAX_BATTERY_CHARGE_CURRENT = 5.0
