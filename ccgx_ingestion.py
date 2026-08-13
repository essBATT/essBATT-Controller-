# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Ingest Victron dbus-mqtt messages into the shared CCGX_data structure.

All handlers accept a minimal msg-like object with .topic and .payload attributes
so they can be unit-tested without a real MQTT client.
"""

import json


class CcgxIngestion:
    """Updates ccgx_data from Victron MQTT topic callbacks."""

    def __init__(self, logger, ccgx_data):
        self.logger = logger
        self.ccgx_data = ccgx_data

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def parse_victron_mqtt_value(self, msg):
        """Parse Victron JSON payload {"value": ...}. Returns value or None."""
        try:
            payload = json.loads(msg.payload)
        except json.JSONDecodeError:
            self.logger.warning(
                'Invalid JSON on MQTT topic ' + msg.topic + ': ' + str(msg.payload)
            )
            return None
        if 'value' not in payload:
            self.logger.warning('MQTT message without "value" on topic ' + msg.topic)
            return None
        return payload['value']

    def device_removed_from_bus(self, topic):
        """Drop a Victron device that left the bus (empty / invalid MQTT payload).

        Solarchargers are stored per instance and must be deleted entirely so
        the mapper does not treat a leftover ``{}`` as an incomplete charger
        (night-time MPPT dropout). Other device types use a flat dict and are
        cleared as a whole.
        """
        split_topic = topic.split('/')
        try:
            device_type = split_topic[2]
            device_instance = split_topic[3]
            if device_type != 'solarcharger':
                self.ccgx_data[device_type] = {}
            else:
                chargers = self.ccgx_data.setdefault(device_type, {})
                chargers.pop(device_instance, None)
            self.logger.info(
                'Device removed from bus — dropped '
                + device_type + '/' + str(device_instance)
                + '. Topic: ' + topic
            )
        except (KeyError, IndexError) as e:
            self.logger.error('Deleting device instance due to empty payload failed: ' + str(e))

    def _store_simple(self, category, key, msg):
        """Store json payload value under ccgx_data[category][key], or drop that field.

        Invalid/empty payloads clear only ``key``. Wiping the whole device
        class (e.g. all battery fields) on one bad SOC message would hide
        still-valid cell voltages from protection logic.
        """
        try:
            self.ccgx_data[category][key] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.ccgx_data.setdefault(category, {}).pop(key, None)
            self.logger.info(
                'Cleared ' + category + '.' + key
                + ' after invalid/empty MQTT payload. Topic: ' + msg.topic
            )

    # ------------------------------------------------------------------
    # Grid
    # ------------------------------------------------------------------
    def on_grid_power(self, msg):
        self._store_simple('grid', 'grid_power_sum', msg)

    def on_L1_power(self, msg):
        self._store_simple('grid', 'L1_power', msg)

    def on_L1_current(self, msg):
        self._store_simple('grid', 'L1_current', msg)

    def on_L2_power(self, msg):
        self._store_simple('grid', 'L2_power', msg)

    def on_L2_current(self, msg):
        self._store_simple('grid', 'L2_current', msg)

    def on_L3_power(self, msg):
        self._store_simple('grid', 'L3_power', msg)

    def on_L3_current(self, msg):
        self._store_simple('grid', 'L3_current', msg)

    # ------------------------------------------------------------------
    # Battery
    # ------------------------------------------------------------------
    def on_battery_soc(self, msg):
        self._store_simple('battery', 'soc', msg)

    def on_battery_maxcellvoltage(self, msg):
        self._store_simple('battery', 'max_cell_voltage', msg)

    def on_battery_mincellvoltage(self, msg):
        self._store_simple('battery', 'min_cell_voltage', msg)

    def on_battery_temp(self, msg):
        self._store_simple('battery', 'temperature', msg)

    def on_battery_current(self, msg):
        self._store_simple('battery', 'current', msg)

    def on_battery_power(self, msg):
        self._store_simple('battery', 'power', msg)

    def on_battery_voltage(self, msg):
        self._store_simple('battery', 'voltage', msg)

    # ------------------------------------------------------------------
    # Solarcharger
    # ------------------------------------------------------------------
    def on_solarcharger_power(self, msg):
        split_topic = msg.topic.split('/')
        try:
            payload_value = json.loads(msg.payload)['value']
            solar_id = split_topic[3]
            if solar_id not in self.ccgx_data['solarcharger']:
                self.ccgx_data['solarcharger'][solar_id] = {}
            self.ccgx_data['solarcharger'][solar_id]['Power'] = payload_value
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus(msg.topic)

    def on_solarcharger_dc_values(self, msg):
        split_topic = msg.topic.split('/')
        try:
            payload_value = json.loads(msg.payload)['value']
            solar_id = split_topic[3]
            value_name = split_topic[6]
            if solar_id not in self.ccgx_data['solarcharger']:
                self.ccgx_data['solarcharger'][solar_id] = {}
            self.ccgx_data['solarcharger'][solar_id][value_name] = payload_value
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus(msg.topic)

    # ------------------------------------------------------------------
    # System / settings / vebus
    # ------------------------------------------------------------------
    def on_system_ac_consumption(self, msg):
        split_topic = msg.topic.split('/')
        try:
            phase_number = split_topic[6]
            measurement_name = split_topic[7]
        except IndexError:
            self.logger.error('Malformed system consumption topic: ' + msg.topic)
            return
        if phase_number != 'NumberOfPhases' and measurement_name == 'Power':
            payload_value = self.parse_victron_mqtt_value(msg)
            if payload_value is not None:
                value_name = phase_number + '_loads_power_consumption'
                self.ccgx_data['system'][value_name] = payload_value

    def on_settings_cgwacs(self, msg):
        split_topic = msg.topic.split('/')
        try:
            value_name = split_topic[6]
            settings_instance = split_topic[3]
        except IndexError:
            self.logger.error('Malformed settings CGwacs topic: ' + msg.topic)
            return
        payload_value = self.parse_victron_mqtt_value(msg)
        if payload_value is None:
            return
        self.ccgx_data['settings'][value_name] = payload_value
        if 'settings_base_path' not in self.ccgx_data:
            self.ccgx_data['settings_base_path'] = (
                'settings/' + settings_instance + '/Settings/'
            )

    def on_settings_system_setup(self, msg):
        split_topic = msg.topic.split('/')
        try:
            value_name = split_topic[6]
        except IndexError:
            self.logger.error('Malformed settings SystemSetup topic: ' + msg.topic)
            return
        payload_value = self.parse_victron_mqtt_value(msg)
        if payload_value is not None:
            self.ccgx_data['settings'][value_name] = payload_value

    def on_multis_switch_mode(self, msg):
        split_topic = msg.topic.split('/')
        try:
            instance_id = split_topic[3]
            value_name = split_topic[4]
        except IndexError:
            self.logger.error('Malformed vebus topic: ' + msg.topic)
            return
        payload_value = self.parse_victron_mqtt_value(msg)
        if payload_value is None:
            return
        if 'vebus' not in self.ccgx_data:
            self.ccgx_data['vebus'] = {}
        if instance_id not in self.ccgx_data['vebus']:
            self.ccgx_data['vebus'][instance_id] = {}
        self.ccgx_data['vebus'][instance_id][value_name] = payload_value
