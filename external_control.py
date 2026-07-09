# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Parse and apply external MQTT control commands for essBATT.

Payload formats (UTF-8 strings):
  charge_to_SOC: activated/target_soc/current/time/date
                 e.g. "1/90/20/12:00/01.01.2026"  (use "-" for unused fields)
  balancing:     activated/current/time/date
  deactivate_*:  "True"/"true" or "False"/"false"
  reboot:        same bool format
"""

from datetime import datetime


class ExternalCommandError(ValueError):
    """Raised when an external control payload is invalid."""


def _decode_utf8(payload):
    """Decode MQTT payload bytes/str to UTF-8 text."""
    if isinstance(payload, bytes):
        return payload.decode('utf-8')
    if isinstance(payload, str):
        return payload
    raise ExternalCommandError('payload is not valid UTF-8 text')


def parse_bool_payload(payload):
    """Parse True/False external flag payload.

    Returns:
        bool on success

    Raises:
        ExternalCommandError on invalid input
    """
    try:
        text = _decode_utf8(payload)
    except UnicodeDecodeError as e:
        raise ExternalCommandError('payload is not valid UTF-8') from e

    if text in ('False', 'false'):
        return False
    if text in ('True', 'true'):
        return True
    raise ExternalCommandError(f'Unknown bool payload: {text}')


def parse_charge_to_soc_payload(payload):
    """Parse charge_to_SOC MQTT payload.

    Returns:
        dict with keys: activated, target_SOC, and optionally
        current_limit_input, time_input, date_input
    """
    try:
        text = _decode_utf8(payload)
        parts = text.split('/')
        activated = parts[0]
        target_soc = int(parts[1])
        result = {
            'activated': activated,
            'target_SOC': target_soc,
        }
        if parts[2] != '-':
            result['current_limit_input'] = int(parts[2])
        if parts[3] != '-':
            result['time_input'] = parts[3]
        if parts[4] != '-':
            result['date_input'] = parts[4]
        return result
    except UnicodeDecodeError as e:
        raise ExternalCommandError('charge_to_SOC payload is not valid UTF-8') from e
    except (IndexError, ValueError) as e:
        raise ExternalCommandError(
            'charge_to_SOC payload format invalid '
            '(expected activated/target_soc/current/time/date): ' + str(e)
        ) from e


def parse_balancing_payload(payload):
    """Parse balancing MQTT payload.

    Returns:
        dict with keys: activated, and optionally current_limit_input, time_input, date_input
    """
    try:
        text = _decode_utf8(payload)
        parts = text.split('/')
        result = {'activated': parts[0]}
        if parts[1] != '-':
            result['current_limit_input'] = int(parts[1])
        if parts[2] != '-':
            result['time_input'] = parts[2]
        if parts[3] != '-':
            result['date_input'] = parts[3]
        return result
    except UnicodeDecodeError as e:
        raise ExternalCommandError('balancing payload is not valid UTF-8') from e
    except (IndexError, ValueError) as e:
        raise ExternalCommandError(
            'balancing payload format invalid '
            '(expected activated/current/time/date): ' + str(e)
        ) from e


def apply_parsed_command(external_input, command_key, parsed, receive_time=None):
    """Write a parsed external command into ess_external_input.

    Args:
        external_input: shared mutable dict (controller.ess_external_input)
        command_key: 'charge_to_SOC', 'balancing', 'deactivate_charge',
                     'deactivate_discharge'
        parsed: dict for structured commands, or bool for deactivate flags
        receive_time: optional datetime (defaults to now)

    Returns:
        external_input (same object)
    """
    if receive_time is None:
        receive_time = datetime.now(tz=None)

    if command_key in ('deactivate_charge', 'deactivate_discharge'):
        if command_key not in external_input:
            external_input[command_key] = {}
        external_input[command_key]['activated'] = bool(parsed)
        external_input[command_key]['receive_time'] = receive_time
        return external_input

    if command_key not in external_input:
        external_input[command_key] = {}
    # Replace command payload fields but keep structure
    external_input[command_key] = dict(parsed)
    external_input[command_key]['receive_time'] = receive_time
    external_input['new_data_received'] = True
    return external_input


class ExternalControlHandlers:
    """MQTT message handlers for external control topics.

    Methods accept a msg-like object with .payload (and optionally .topic).
    Wire them via mqtt_helpers.build_external_topic_bindings().
    """

    def __init__(self, logger, external_input, reboot_callback=None):
        """
        Args:
            logger: logger instance
            external_input: shared mutable ess_external_input dict
            reboot_callback: optional callable invoked when reboot command is True
        """
        self.logger = logger
        self.external_input = external_input
        self.reboot_callback = reboot_callback

    def on_charge_to_soc(self, msg):
        try:
            parsed = parse_charge_to_soc_payload(msg.payload)
        except ExternalCommandError as e:
            self.logger.error(str(e))
            return
        apply_parsed_command(self.external_input, 'charge_to_SOC', parsed)

    def on_balancing(self, msg):
        try:
            parsed = parse_balancing_payload(msg.payload)
        except ExternalCommandError as e:
            self.logger.error(str(e))
            return
        apply_parsed_command(self.external_input, 'balancing', parsed)

    def on_deactivate_discharge(self, msg):
        try:
            activation_state = parse_bool_payload(msg.payload)
        except ExternalCommandError as e:
            self.logger.error(str(e))
            return
        apply_parsed_command(self.external_input, 'deactivate_discharge', activation_state)
        if activation_state:
            self.logger.info('"DISCHARGING" is now "DEACTIVATED"!')
        else:
            self.logger.info('"DISCHARGING" is now "ALLOWED"!')

    def on_deactivate_charge(self, msg):
        try:
            activation_state = parse_bool_payload(msg.payload)
        except ExternalCommandError as e:
            self.logger.error(str(e))
            return
        apply_parsed_command(self.external_input, 'deactivate_charge', activation_state)
        if activation_state:
            self.logger.info('"CHARGING" is now "DEACTIVATED"!')
        else:
            self.logger.info('"CHARGING" is now "ALLOWED"!')

    def on_reboot(self, msg):
        try:
            reboot = parse_bool_payload(msg.payload)
        except ExternalCommandError as e:
            self.logger.error(str(e))
            return
        if reboot and self.reboot_callback is not None:
            self.reboot_callback()
