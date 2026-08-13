# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Configuration and state management for essBATT Controller.

This module handles loading of config files, setvalue lists, persistent state,
and saving changed state. It encapsulates all file I/O related to configuration.
"""

import json
import copy
from pathlib import Path

import constants


DEFAULT_CONTROLLER_STATE = {
    'ess_controller_state_version': 1.0,
    'current_state': 'normal_operation',
    'time_of_last_change': 'none',
    'time_of_last_completed_balancing': 'none',
    'winter_mode': 'not_activated',
    'winter_SOC_discharge_limit': 'not_activated',
    'charge_to_SOC': {
        'activation_time': 'none',
        'target_SOC': 'none',
        'max_current': 'none',
        'requested_current_direction': 'none',
        'scheduled_start_time': 'none',
    },
    'balancing': {
        'activation_time': 'none',
        'max_current': 'none',
        'scheduled_start_time': 'none',
    },
}


_CHARGE_ARRAY_PAIRS = (
    (
        'soc_based_charge_limit_soc_array',
        'soc_based_charge_limit_current_array',
        'ascending',
    ),
    (
        'max_cell_based_charge_limit_voltage_array',
        'max_cell_based_charge_limit_current_array',
        'ascending',
    ),
)
_DISCHARGE_ARRAY_PAIRS = (
    (
        'soc_based_discharge_limit_soc_array',
        'soc_based_discharge_limit_current_array',
        'descending',
    ),
    (
        'min_cell_based_discharge_limit_voltage_array',
        'min_cell_based_discharge_limit_current_array',
        'descending',
    ),
)


def _is_non_decreasing(seq):
    return all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))


def _is_non_increasing(seq):
    return all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))


def validate_ess_config(data):
    """Check ess_config.json for safety-relevant consistency.

    Returns:
        (ok, errors, warnings)
        ok is False when ``errors`` is non-empty (config must not be used).
        ``warnings`` are logged but do not reject the file.

    Normalizes ``debug_level`` in-place to a canonical uppercase name when
    it is a known (possibly lowercase) level.
    """
    errors = []
    warnings = []
    if not isinstance(data, dict):
        return False, ['ess_config.json root must be a JSON object'], warnings

    raw_level = data.get('debug_level', 'INFO')
    level_key = str(raw_level if raw_level is not None else 'INFO').strip().upper()
    if level_key in constants.LOGLEVEL_NAME_TO_NUMBER:
        data['debug_level'] = level_key
    else:
        warnings.append(
            'debug_level %r is unknown; falling back to INFO' % (raw_level,)
        )
        data['debug_level'] = 'INFO'

    vrm_id = str(data.get('vrm_id') or '').strip()
    if vrm_id in constants.VRM_ID_PLACEHOLDERS or vrm_id.upper() == 'YOUR VRM ID':
        warnings.append(
            'vrm_id looks like a placeholder (%r) — MQTT topics will not match Venus'
            % (data.get('vrm_id'),)
        )

    battery = data.get('battery_settings') or {}
    if isinstance(battery, dict):
        for key_a, key_b, order in _CHARGE_ARRAY_PAIRS + _DISCHARGE_ARRAY_PAIRS:
            a = battery.get(key_a)
            b = battery.get(key_b)
            if a is None and b is None:
                continue
            if not isinstance(a, list) or not isinstance(b, list):
                errors.append(
                    'battery_settings.%s and %s must both be arrays' % (key_a, key_b)
                )
                continue
            if len(a) != len(b):
                errors.append(
                    'battery_settings.%s length %d does not match %s length %d'
                    % (key_a, len(a), key_b, len(b))
                )
                continue
            if len(a) >= 2:
                ordered = (
                    _is_non_decreasing(a) if order == 'ascending'
                    else _is_non_increasing(a)
                )
                if not ordered:
                    warnings.append(
                        'battery_settings.%s should be %s to match documented limit logic'
                        % (key_a, order)
                    )

        charge_cut = battery.get('max_cell_voltage_charging')
        charge_resume = battery.get('max_cell_voltage_charging_resume')
        if charge_cut is not None and charge_resume is not None:
            try:
                if float(charge_resume) >= float(charge_cut):
                    errors.append(
                        'max_cell_voltage_charging_resume (%s) must be < '
                        'max_cell_voltage_charging (%s)'
                        % (charge_resume, charge_cut)
                    )
            except (TypeError, ValueError):
                errors.append('max_cell_voltage_charging / resume must be numeric')

        disc_cut = battery.get('min_cell_voltage_discharging')
        disc_resume = battery.get('min_cell_voltage_discharging_resume')
        if disc_cut is not None and disc_resume is not None:
            try:
                if float(disc_resume) <= float(disc_cut):
                    errors.append(
                        'min_cell_voltage_discharging_resume (%s) must be > '
                        'min_cell_voltage_discharging (%s)'
                        % (disc_resume, disc_cut)
                    )
            except (TypeError, ValueError):
                errors.append('min_cell_voltage_discharging / resume must be numeric')

    winter = data.get('winter_mode') or {}
    if isinstance(winter, dict):
        winter_on = winter.get('use_winter_mode') == 1
        wmin = winter.get('winter_min_SOC')
        wrestart = winter.get('winter_restart_multis_SOC')
        if wmin is not None and wrestart is not None:
            try:
                inverted = float(wrestart) <= float(wmin)
            except (TypeError, ValueError):
                inverted = True
                msg = 'winter_min_SOC / winter_restart_multis_SOC must be numeric'
                if winter_on:
                    errors.append(msg)
                else:
                    warnings.append(msg)
            else:
                if inverted:
                    msg = (
                        'winter_restart_multis_SOC (%s) must be > winter_min_SOC (%s)'
                        % (wrestart, wmin)
                    )
                    if winter_on:
                        errors.append(msg)
                    else:
                        warnings.append(msg)
        if winter_on:
            try:
                from state_machine import parse_dd_mm
                parse_dd_mm(winter.get('winter_mode_start_date'))
                parse_dd_mm(winter.get('winter_mode_end_date'))
            except (TypeError, ValueError) as e:
                errors.append('winter_mode dates invalid: %s' % (e,))

    return (len(errors) == 0), errors, warnings


class ConfigManager:
    """Manages loading and saving of configuration and persistent state."""

    def __init__(self, logger, debug=False):
        self.logger = logger
        self.debug = debug
        self.config_data_loaded_correctly = False
        self.setvalue_list_loaded_correctly = False
        self.controller_state_loaded_correctly = False

    def _get_config_path(self, debug_path, prod_path):
        """Return correct path depending on debug mode."""
        if self.debug:
            return Path(debug_path)
        return Path(prod_path)

    def _read_json_file(self, debug_path, prod_path, description):
        """Helper to read and parse JSON with error handling."""
        file_path = self._get_config_path(debug_path, prod_path)
        try:
            with open(file_path, encoding='utf-8') as f:
                data = json.load(f)
            self.logger.debug(f'{description} loaded successfully from {file_path}')
            return data
        except FileNotFoundError:
            self.logger.error(f'{description} not found at: {file_path}')
        except json.JSONDecodeError as e:
            self.logger.error(f'{description} contains invalid JSON: {e}')
        except OSError as e:
            self.logger.error(f'Could not read {description}: {e}')
        return None

    def load_config(self):
        """Load ess_config.json and return the data."""
        data = self._read_json_file(
            './smarthome_projects/essBATT-Controller-/ess_config.json',
            'ess_config.json',
            'ess_config.json'
        )
        if data is None:
            self.config_data_loaded_correctly = False
            return {}
        ok, errors, warnings = validate_ess_config(data)
        for warning in warnings:
            self.logger.warning('ess_config.json: ' + warning)
        if not ok:
            for error in errors:
                self.logger.error('ess_config.json: ' + error)
            self.logger.error(
                'ess_config.json rejected due to ' + str(len(errors)) + ' error(s).'
            )
            self.config_data_loaded_correctly = False
            return {}
        self.config_data_loaded_correctly = True
        return data

    def load_setvalue_list(self):
        """Load ess_setvalue_list.json."""
        data = self._read_json_file(
            './smarthome_projects/essBATT-Controller-/ess_setvalue_list.json',
            'ess_setvalue_list.json',
            'ess_setvalue_list.json'
        )
        if data is None:
            self.setvalue_list_loaded_correctly = False
            return {}
        self.setvalue_list_loaded_correctly = True
        return data

    def load_state(self):
        """Load persistent ess_controller_state file.

        A missing file is created with a safe default (normal_operation).
        A present but invalid file is not overwritten.
        """
        debug_path = './smarthome_projects/essBATT-Controller-/ess_controller_state'
        prod_path = './ess_controller_state'
        state_path = self._get_config_path(debug_path, prod_path)
        if not state_path.exists():
            default = copy.deepcopy(DEFAULT_CONTROLLER_STATE)
            if self.save_state(default):
                self.logger.warning(
                    'ess_controller_state missing; created default at ' + str(state_path)
                )
                self.controller_state_loaded_correctly = True
                return default
            self.controller_state_loaded_correctly = False
            return {}

        data = self._read_json_file(
            debug_path, prod_path, 'ess_controller_state'
        )
        if data is None:
            self.controller_state_loaded_correctly = False
            return {}
        self.controller_state_loaded_correctly = True
        self.logger.debug('ess_controller_state file loaded.')
        return data

    def save_state(self, state_dict):
        """Save the controller state to file."""
        self.controller_state_loaded_correctly = False
        state_path = self._get_config_path(
            './smarthome_projects/essBATT-Controller-/ess_controller_state',
            './ess_controller_state'
        )
        try:
            with open(state_path, 'w', encoding='utf-8') as f:
                json.dump(state_dict, f, ensure_ascii=False, indent=4)
            self.controller_state_loaded_correctly = True
            self.logger.debug('ess_controller_state file stored.')
            return True
        except OSError as e:
            self.logger.error(f'ess_controller_state could not be written: {e}')
        except TypeError as e:
            self.logger.error(f'ess_controller_state contains non-serializable data: {e}')
        return False

    def create_temporary_script_states(self, config_data):
        """Create temporary (non-persisted) script states dictionary.

        Shared by StateMachine and BatteryProtector (same object reference).
        """
        max_discharge = config_data.get('ess_mode_2_settings', {}).get(
            'max_battery_discharge_current', constants.DEFAULT_MAX_BATTERY_DISCHARGE_CURRENT
        )
        max_charge = config_data.get('ess_mode_2_settings', {}).get(
            'max_battery_charge_current_2705', constants.DEFAULT_MAX_BATTERY_CHARGE_CURRENT
        )
        return {
            "multi_switch_min_soc_debounce_time": None,
            "winter_mode_multis_switch_off_time": None,
            "winter_mode_inactive_charge_begin_time": None,
            "emergency_charge_begin_time": None,
            "emergency_discharge_begin_time": None,
            "winter_mode_charge_begin_time": None,
            "discharge_current_limit_state": max_discharge,
            "discharge_current_limit_hit_zero": False,
            "charge_current_limit_state": max_charge,
            "charge_current_limit_hit_zero": False,
            "discharge_regular_current_limit_last_cycle": 0.0,
        }

    def save_state_if_changed(self, current_state, snapshot):
        """Compare with snapshot and save only if changed."""
        if current_state != snapshot:
            success = self.save_state(current_state)
            if success:
                self.logger.debug('Change in controller state detected! Stored to file.')
            return success
        return False
