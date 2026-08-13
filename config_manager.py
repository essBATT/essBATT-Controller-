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
        """Load persistent ess_controller_state file."""
        data = self._read_json_file(
            './smarthome_projects/essBATT-Controller-/ess_controller_state',
            './ess_controller_state',
            'ess_controller_state'
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
