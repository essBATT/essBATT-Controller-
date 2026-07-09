# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Publish Victron ESS Mode 2 setpoints and Multi switch commands over MQTT."""

import json

import constants


class VictronOutput:
    """Sends setpoints to Venus OS / Cerbo via MQTT.

    Dependencies are injected so unit tests can mock the MQTT client.
    """

    def __init__(self, logger, mqtt_client, ccgx_data, setvalue_list, write_base_path):
        """
        Args:
            logger: logger instance
            mqtt_client: paho mqtt client (or mock) with publish()
            ccgx_data: shared CCGX_data dict (reads current settings / vebus)
            setvalue_list: mapping from logical name → Victron settings path suffix
            write_base_path: e.g. "W/<vrm_id>/"
        """
        self.logger = logger
        self.mqtt_client = mqtt_client
        self.ccgx_data = ccgx_data
        self.setvalue_list = setvalue_list
        self.write_base_path = write_base_path

    def set_mqtt_client(self, mqtt_client):
        """Update MQTT client reference (set after connect)."""
        self.mqtt_client = mqtt_client

    def set_ccgx_value(self, set_val_name_str=None, set_val=0,
                       only_set_if_deviation_to_current_setting=True):
        """Set a Victron settings value over MQTT.

        Returns:
            0: ok but nothing sent
            1: value published
           -1: error
        """
        if set_val_name_str is None:
            self.logger.error('No set value name given!')
            return -1

        settings = self.ccgx_data.get('settings', {})
        if set_val_name_str not in settings:
            return 0

        current = settings[set_val_name_str]
        if only_set_if_deviation_to_current_setting and set_val == current:
            return 0

        try:
            topic_str = (
                self.write_base_path
                + self.ccgx_data['settings_base_path']
                + self.setvalue_list[set_val_name_str]
            )
            payload_str = json.dumps({"value": set_val})
            self.mqtt_client.publish(topic=topic_str, payload=payload_str, qos=1, retain=0)
            self.logger.debug(
                set_val_name_str + ': Published ' + payload_str + ' on ' + topic_str
                + '. settings["' + set_val_name_str + '"]: ' + str(current)
            )
            return 1
        except (TypeError, ValueError, KeyError) as e:
            self.logger.error(set_val_name_str + ' setpoint sending failed: ' + str(e))
            return -1

    def set_multis_switch_mode(self, switch_position):
        """Set Multi/vebus Mode: 1=Charger Only, 2=Inverter Only, 3=On, 4=Off.

        Returns:
            True if a publish was attempted, False if no vebus instance / no change
        """
        if 'vebus' not in self.ccgx_data:
            return False

        counter = 0
        current_instance_id = ''
        for key in self.ccgx_data['vebus']:
            current_instance_id = str(key)
            counter += 1

        if not current_instance_id:
            return False

        current_mode = self.ccgx_data['vebus'][current_instance_id].get('Mode')
        published = False
        if current_mode is None or current_mode != switch_position:
            topic_str = self.write_base_path + 'vebus/' + current_instance_id + '/Mode'
            payload_str = json.dumps({"value": switch_position})
            self.mqtt_client.publish(topic=topic_str, payload=payload_str, qos=1, retain=0)
            mapping = constants.MULTIS_SWITCH_NUMBER_STRING_MAPPING.get(
                str(switch_position), 'UNKNOWN'
            )
            self.logger.info(
                '"Multis SWITCH" switched to ' + mapping
                + '(value: ' + str(switch_position) + ')'
            )
            published = True

        if counter > 1:
            self.logger.error(
                'It seems that there is more than one instance of "vebus" available. '
                'This was not considered during development of the script and needs '
                'to be investigated!!!'
            )
        return published
