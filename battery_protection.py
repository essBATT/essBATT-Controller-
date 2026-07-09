# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Battery protection and limit calculation logic for essBATT.

This module encapsulates all charge/discharge current limit calculations,
voltage/SOC based protection, smoothing, winter mode limits and emergency logic.

local_values key contract (must match controller.read_values_to_local_dict):
  - battery_soc
  - battery_min_cell_voltage
  - battery_max_cell_voltage
  - battery_current
  - battery_voltage
  - solarcharger_power_sum
"""

import constants


# Keys produced by the controller and consumed by limit logic
REQUIRED_BATTERY_KEYS = (
    'battery_min_cell_voltage',
    'battery_max_cell_voltage',
    'battery_soc',
    'battery_current',
    'battery_voltage',
)


class BatteryProtector:
    """Handles all battery safety limits, charge/discharge calculations and related state."""

    def __init__(self, config_data, logger, temporary_script_states, controller_state, external_input=None):
        """
        Args:
            config_data: ess_config.json contents (mutable reference OK for online reload)
            logger: logger instance
            temporary_script_states: shared non-persisted state dict (same object as StateMachine)
            controller_state: shared ess_controller_state dict
            external_input: optional shared ess_external_input dict
        """
        self.config = config_data
        self.logger = logger
        self.temporary_script_states = temporary_script_states
        self.ess_controller_state = controller_state
        self.ess_external_input = external_input if external_input is not None else {}
        self.safe_state_active = False

    def update_config(self, config_data):
        """Update config reference (e.g. after online config reload)."""
        self.config = config_data

    def calculate_dis_charge_limits(self, local_values):
        """Main entry point for limit calculation (called from controller cycle)."""
        if not self._validate_required_data(local_values):
            self.enter_safe_state(
                local_values,
                reason="Missing critical battery data (SOC, cell voltages, current or voltage)",
            )
            return local_values

        # Valid data present — leave any previous safe-state flag
        self.safe_state_active = False

        local_values['charge_current_limit_regular'] = (
            self.get_charge_current_limit_with_battery_protection(local_values)
        )
        local_values['discharge_current_limit_regular'] = (
            self.get_discharge_current_limit_with_battery_protection(local_values)
        )

        # Violation detection (and optional compensation)
        local_values['charge_current_limit_violation'] = False
        local_values['discharge_current_limit_violation'] = False

        battery_current = local_values['battery_current']
        if (battery_current < 0.0 and
                abs(battery_current) > local_values['discharge_current_limit_regular']):
            local_values['violation_current'] = (
                abs(battery_current) - local_values['discharge_current_limit_regular']
            )
            local_values['discharge_current_limit_violation'] = True
        elif (battery_current > 0.0 and
              abs(battery_current) > local_values['charge_current_limit_regular']):
            local_values['violation_current'] = (
                abs(battery_current) - local_values['charge_current_limit_regular']
            )
            local_values['charge_current_limit_violation'] = True

        local_values['discharge_power_limit_regular'] = self.calc_discharge_power_limit_from_current(
            local_values, local_values['discharge_current_limit_regular']
        )
        self.logger.debug(
            'Discharge power limit: ' + str(local_values['discharge_power_limit_regular']) + 'W.'
        )

        # Optional compensation for observed limit violations
        if self.config.get('battery_settings', {}).get('compensate_current_limit_violations', 0) == 1:
            if local_values['discharge_current_limit_violation'] is True:
                local_values['discharge_power_limit_regular'] = (
                    local_values['discharge_power_limit_regular']
                    - (local_values['violation_current'] * local_values['battery_voltage'])
                )
                self.logger.debug(
                    'Discharge power limit violated and compensated by: '
                    + str(local_values['violation_current'])
                    + 'A. Discharge power limit now: '
                    + str(local_values['discharge_power_limit_regular'])
                )
            elif local_values['charge_current_limit_violation'] is True:
                local_values['charge_current_limit_regular'] = (
                    local_values['charge_current_limit_regular'] - local_values['violation_current']
                )
                self.logger.debug(
                    'Charge current limit violated and compensated by: '
                    + str(local_values['violation_current'])
                    + 'A. Charge current limit now: '
                    + str(local_values['charge_current_limit_regular'])
                    + 'A.'
                )

        # Winter discharge lock (only when state machine has activated it)
        if (self.ess_controller_state.get('winter_mode') == 'activated' and
                self.ess_controller_state.get('winter_SOC_discharge_limit') == 'activated'):
            local_values['winter_discharge_limit'] = 0.0

        # Stricter limits from balancing / charge_to_SOC max_current
        local_values['external_current_limit_set'] = False
        current_state = self.ess_controller_state.get('current_state', 'normal_operation')
        if current_state == 'balancing':
            max_current = self.ess_controller_state.get('balancing', {}).get('max_current', 'none')
            if max_current != 'none':
                local_values['external_current_limit'] = max_current
                local_values['external_current_limit_set'] = True
        elif current_state == 'charge_to_SOC':
            max_current = self.ess_controller_state.get('charge_to_SOC', {}).get('max_current', 'none')
            if max_current != 'none':
                local_values['external_current_limit'] = max_current
                local_values['external_current_limit_set'] = True
        elif current_state == 'normal_operation':
            pass
        else:
            self.logger.error('Unknown ess controller state: ' + str(current_state))

        # External forbid charge/discharge (also set by controller; keep here for completeness)
        if self.ess_external_input.get('deactivate_charge', {}).get('activated'):
            local_values['deactivate_charge_limit'] = 0.0
        if self.ess_external_input.get('deactivate_discharge', {}).get('activated'):
            local_values['deactivate_discharge_limit'] = 0.0

        # Voltage-based limit smoothing / hysteresis
        if self.config.get('battery_settings', {}).get('smooth_voltage_based_(dis)charge_limits', 0) == 1:
            self._apply_voltage_based_smoothing(local_values)

        self._aggregate_final_limits(local_values)

        # Store values from this cycle for next cycle
        self.temporary_script_states['discharge_regular_current_limit_last_cycle'] = (
            local_values['discharge_current_limit_regular']
        )

        return local_values

    def _validate_required_data(self, local_values):
        """Check if all critical battery data is available (controller key names)."""
        for key in REQUIRED_BATTERY_KEYS:
            if key not in local_values:
                return False
        # solarcharger_power_sum is required for discharge power conversion
        if 'solarcharger_power_sum' not in local_values:
            return False
        return True

    def enter_safe_state(self, local_values, reason="Unknown reason"):
        """Explicit method to enter safe state (zero charge/discharge, clear setpoints)."""
        self.safe_state_active = True
        local_values['charge_current_limit_final'] = 0.0
        local_values['discharge_current_limit_final'] = 0.0
        local_values['discharge_power_limit_final'] = 0
        local_values['AcPowerSetPoint'] = 0
        self.logger.error(
            f'ENTERING SAFE STATE - Reason: {reason}. All charge/discharge disabled.'
        )

    def get_charge_current_limit_with_battery_protection(self, local_values):
        """SOC and max-cell based charge current limit calculation."""
        current_charge_limit = 0.0
        if ('battery_max_cell_voltage' not in local_values or 'battery_soc' not in local_values):
            self.logger.warning('battery_max_cell_voltage or battery_soc not available for charge limit!')
            return 0.0

        battery_max_cell_voltage = local_values['battery_max_cell_voltage']
        battery_soc = local_values['battery_soc']
        max_charge = self.config.get('ess_mode_2_settings', {}).get(
            'max_battery_charge_current_2705', constants.DEFAULT_MAX_BATTERY_CHARGE_CURRENT
        )

        max_cell_voltage_charging = self.config.get('battery_settings', {}).get(
            'max_cell_voltage_charging'
        )
        if max_cell_voltage_charging is None:
            self.enter_safe_state(local_values, reason="max_cell_voltage_charging missing in config")
            return 0.0
        if battery_max_cell_voltage >= max_cell_voltage_charging:
            return 0.0

        try:
            soc_based = -1.0
            for i, soc_limit in enumerate(
                self.config['battery_settings']['soc_based_charge_limit_soc_array']
            ):
                if battery_soc >= soc_limit:
                    soc_based = self.config['battery_settings']['soc_based_charge_limit_current_array'][i]

            max_cell_based = -1.0
            for i, v_limit in enumerate(
                self.config['battery_settings']['max_cell_based_charge_limit_voltage_array']
            ):
                if battery_max_cell_voltage >= v_limit:
                    max_cell_based = self.config['battery_settings'][
                        'max_cell_based_charge_limit_current_array'
                    ][i]

            mode = self.config['battery_settings']['charge_limit_mode']
            soc_or_max_cell_limit_set = False
            if mode == 'max_cell_only':
                if max_cell_based >= 0:
                    current_charge_limit = max_cell_based
                    soc_or_max_cell_limit_set = True
            elif mode == 'soc_only':
                if soc_based >= 0:
                    current_charge_limit = soc_based
                    soc_or_max_cell_limit_set = True
            elif mode == 'soc_and_max_cell':
                limits = [x for x in (soc_based, max_cell_based) if x >= 0]
                if limits:
                    current_charge_limit = min(limits)
                    soc_or_max_cell_limit_set = True
            else:
                self.logger.error(
                    'Charge limit mode unknown in ess_config.json. You entered value: ' + str(mode)
                )

            if soc_or_max_cell_limit_set:
                current_charge_limit = min(max_charge, current_charge_limit)
            else:
                current_charge_limit = max_charge
        except (IndexError, KeyError, TypeError) as e:
            self.logger.error(f'Invalid charge limit arrays in config: {e}')
            return 0.0

        self.logger.debug(f'Charge current limit calculated: {current_charge_limit}A')
        return current_charge_limit

    def get_discharge_current_limit_with_battery_protection(self, local_values):
        """SOC and min-cell based discharge current limit calculation."""
        current_discharge_limit = 0.0
        if ('battery_min_cell_voltage' not in local_values or 'battery_soc' not in local_values):
            self.logger.warning(
                'battery_min_cell_voltage or battery_soc not available for discharge limit!'
            )
            return 0.0

        battery_min_cell_voltage = local_values['battery_min_cell_voltage']
        battery_soc = local_values['battery_soc']
        max_discharge = self.config.get('ess_mode_2_settings', {}).get(
            'max_battery_discharge_current', constants.DEFAULT_MAX_BATTERY_DISCHARGE_CURRENT
        )

        min_cell_discharging = self.config.get('battery_settings', {}).get(
            'min_cell_voltage_discharging'
        )
        if min_cell_discharging is None:
            self.enter_safe_state(local_values, reason="min_cell_voltage_discharging missing in config")
            return 0.0
        if battery_min_cell_voltage <= min_cell_discharging:
            return 0.0

        try:
            soc_array = self.config['battery_settings']['soc_based_discharge_limit_soc_array']
            soc_current_array = self.config['battery_settings']['soc_based_discharge_limit_current_array']
            cell_array = self.config['battery_settings']['min_cell_based_discharge_limit_voltage_array']
            cell_current_array = self.config['battery_settings'][
                'min_cell_based_discharge_limit_current_array'
            ]
            mode = self.config['battery_settings']['discharge_limit_mode']

            soc_based = -1.0
            for i, limit in enumerate(soc_array):
                if battery_soc <= limit:
                    soc_based = soc_current_array[i]

            cell_based = -1.0
            for i, limit in enumerate(cell_array):
                if battery_min_cell_voltage <= limit:
                    cell_based = cell_current_array[i]

            soc_or_min_cell_limit_set = False
            if mode == 'min_cell_only':
                if cell_based >= 0:
                    current_discharge_limit = cell_based
                    soc_or_min_cell_limit_set = True
            elif mode == 'soc_only':
                if soc_based >= 0:
                    current_discharge_limit = soc_based
                    soc_or_min_cell_limit_set = True
            elif mode == 'soc_and_min_cell':
                candidates = [x for x in (soc_based, cell_based) if x >= 0]
                if candidates:
                    current_discharge_limit = min(candidates)
                    soc_or_min_cell_limit_set = True
            else:
                self.logger.error(
                    'Discharge limit mode unknown in ess_config.json. You entered value: ' + str(mode)
                )

            if soc_or_min_cell_limit_set:
                current_discharge_limit = min(max_discharge, current_discharge_limit)
            else:
                current_discharge_limit = max_discharge
        except (IndexError, KeyError, TypeError) as e:
            self.logger.error(f'Invalid discharge limit arrays in config: {e}')
            return 0.0

        self.logger.debug(
            f'Discharge current limit calculated: {current_discharge_limit}A '
            f'(SOC={battery_soc}, min_cell={battery_min_cell_voltage})'
        )
        return current_discharge_limit

    def calc_discharge_power_limit_from_current(self, local_values, input_current):
        """Convert current limit to power limit taking solar into account."""
        if 'battery_voltage' not in local_values or 'solarcharger_power_sum' not in local_values:
            return 0
        return abs(input_current * local_values['battery_voltage']) + local_values['solarcharger_power_sum']

    def _apply_voltage_based_smoothing(self, local_values):
        """Snap (dis)charge limits to the strictest voltage-triggered threshold until resume conditions."""
        max_discharge = self.config['ess_mode_2_settings']['max_battery_discharge_current']
        max_charge = self.config['ess_mode_2_settings']['max_battery_charge_current_2705']

        # --- Discharge smoothing ---
        discharge_regular_limit_diff = (
            local_values['discharge_current_limit_regular']
            - self.temporary_script_states['discharge_current_limit_state']
        )
        if discharge_regular_limit_diff < -0.01 and local_values['battery_current'] < 0:
            self.temporary_script_states['discharge_current_limit_state'] = (
                local_values['discharge_current_limit_regular']
            )
            self.logger.info(
                'Discharge current limit got stricter. Now is: '
                + str(self.temporary_script_states['discharge_current_limit_state'])
                + '. discharge_regular_limit_diff: ' + str(discharge_regular_limit_diff)
                + ', discharge_current_limit_regular: '
                + str(local_values['discharge_current_limit_regular'])
            )

        if local_values['discharge_current_limit_regular'] < 0.1:
            self.temporary_script_states['discharge_current_limit_hit_zero'] = True
            self.logger.info('discharge_current_limit_hit_zero is True now')

        resume_min_cell = self.config['battery_settings']['min_cell_voltage_discharging_resume']
        hit_zero = self.temporary_script_states['discharge_current_limit_hit_zero'] is True
        min_cell_resume = local_values['battery_min_cell_voltage'] > resume_min_cell and hit_zero
        strong_charge_reset = (
            local_values['battery_current'] > constants.DISCHARGE_LIMIT_RESET_CHARGE_CURRENT
            and self.temporary_script_states['discharge_current_limit_state'] < max_discharge
        )
        if min_cell_resume or strong_charge_reset:
            if min_cell_resume:
                self.logger.info(
                    'Condition that triggered the reset: Min cell voltage above discharge '
                    'resume voltage and discharge current limit had hit zero before.'
                )
            if strong_charge_reset:
                self.logger.info(
                    'Condition that triggered the reset: Battery current above "reset charge current" '
                    '(internal script parameter) and the stored discharge limit was not yet reset. '
                    'battery_current: ' + str(local_values['battery_current'])
                    + ' discharge_current_limit_state: '
                    + str(self.temporary_script_states['discharge_current_limit_state'])
                )
            self.temporary_script_states['discharge_current_limit_state'] = max_discharge
            self.temporary_script_states['discharge_current_limit_hit_zero'] = False
            self.logger.info(
                'Discharge current limit reset to default value! Now: '
                + str(self.temporary_script_states['discharge_current_limit_state'])
            )

        # --- Charge smoothing ---
        charge_regular_limit_diff = (
            local_values['charge_current_limit_regular']
            - self.temporary_script_states['charge_current_limit_state']
        )
        if charge_regular_limit_diff < -0.01 and local_values['battery_current'] > 0:
            self.temporary_script_states['charge_current_limit_state'] = (
                local_values['charge_current_limit_regular']
            )
            self.logger.info(
                'Charge current limit got stricter. Now is: '
                + str(self.temporary_script_states['charge_current_limit_state'])
                + '. charge_regular_limit_diff: ' + str(charge_regular_limit_diff)
                + ', charge_current_limit_regular: '
                + str(local_values['charge_current_limit_regular'])
            )

        if local_values['charge_current_limit_regular'] < 0.1:
            self.temporary_script_states['charge_current_limit_hit_zero'] = True
            self.logger.info('charge_current_limit_hit_zero is True now')

        resume_max_cell = self.config['battery_settings']['max_cell_voltage_charging_resume']
        charge_hit_zero = self.temporary_script_states['charge_current_limit_hit_zero'] is True
        max_cell_resume = local_values['battery_max_cell_voltage'] <= resume_max_cell and charge_hit_zero
        strong_discharge_reset = (
            local_values['battery_current'] < constants.CHARGE_LIMIT_RESET_DISCHARGE_CURRENT
            and self.temporary_script_states['charge_current_limit_state'] < max_charge
        )
        if max_cell_resume or strong_discharge_reset:
            if max_cell_resume:
                self.logger.info(
                    'Condition that triggered the reset: Max cell voltage below charge '
                    'resume voltage and charge current limit had hit zero before.'
                )
            if strong_discharge_reset:
                self.logger.info(
                    'Condition that triggered the reset: Battery current below "reset discharge current" '
                    '(internal script parameter) and the stored charge limit was not yet reset.'
                )
            self.temporary_script_states['charge_current_limit_state'] = max_charge
            self.temporary_script_states['charge_current_limit_hit_zero'] = False
            self.logger.info(
                'Charge current limit reset to default value! Now: '
                + str(self.temporary_script_states['charge_current_limit_state'])
            )

    def _aggregate_final_limits(self, local_values):
        """Combine regular, external, winter, deactivate and smoothed limits into finals."""
        # Charge current
        charge_current_limits_list = []
        info_str = ''
        if 'charge_current_limit_regular' in local_values:
            charge_current_limits_list.append(local_values['charge_current_limit_regular'])
            info_str += 'charge_current_limit_regular, '
        if 'external_current_limit' in local_values:
            charge_current_limits_list.append(local_values['external_current_limit'])
            info_str += 'external_current_limit, '
        if 'deactivate_charge_limit' in local_values:
            charge_current_limits_list.append(local_values['deactivate_charge_limit'])
            info_str += 'deactivate_charge_limit, '
        if 'charge_current_limit_state' in self.temporary_script_states:
            charge_current_limits_list.append(
                self.temporary_script_states['charge_current_limit_state']
            )
            info_str += 'charge_current_limit_state'
        if charge_current_limits_list:
            local_values['charge_current_limit_final'] = min(charge_current_limits_list)
            self.logger.debug(
                'charge_current_limit_final: ' + str(local_values['charge_current_limit_final'])
                + ' from list: ' + ','.join(map(str, charge_current_limits_list))
                + ' (' + info_str + ').'
            )
        else:
            self.logger.error('Determination of charge current limits failed: no limits in list')
            local_values['charge_current_limit_final'] = 0.0

        # Discharge current
        discharge_current_limits_list = []
        if 'discharge_current_limit_regular' in local_values:
            discharge_current_limits_list.append(local_values['discharge_current_limit_regular'])
        if 'external_current_limit' in local_values:
            discharge_current_limits_list.append(local_values['external_current_limit'])
        if 'winter_discharge_limit' in local_values:
            discharge_current_limits_list.append(local_values['winter_discharge_limit'])
        if 'deactivate_discharge_limit' in local_values:
            discharge_current_limits_list.append(local_values['deactivate_discharge_limit'])
        if 'discharge_current_limit_state' in self.temporary_script_states:
            discharge_current_limits_list.append(
                self.temporary_script_states['discharge_current_limit_state']
            )
        if discharge_current_limits_list:
            local_values['discharge_current_limit_final'] = min(discharge_current_limits_list)
        else:
            self.logger.error('Determination of discharge current limits failed: no limits in list')
            local_values['discharge_current_limit_final'] = 0.0

        # Discharge power (each contributing current limit converted independently)
        discharge_power_limits_list = []
        info_str = ''
        if 'discharge_power_limit_regular' in local_values:
            discharge_power_limits_list.append(local_values['discharge_power_limit_regular'])
            info_str += 'discharge_power_limit_regular, '
        if 'external_current_limit' in local_values:
            discharge_power_limits_list.append(
                self.calc_discharge_power_limit_from_current(
                    local_values, local_values['external_current_limit']
                )
            )
            info_str += 'external_current_limit, '
        if 'deactivate_discharge_limit' in local_values:
            discharge_power_limits_list.append(
                self.calc_discharge_power_limit_from_current(
                    local_values, local_values['deactivate_discharge_limit']
                )
            )
            info_str += 'deactivate_discharge_limit, '
        if 'winter_discharge_limit' in local_values:
            discharge_power_limits_list.append(
                self.calc_discharge_power_limit_from_current(
                    local_values, local_values['winter_discharge_limit']
                )
            )
            info_str += 'winter_discharge_limit, '
        if 'discharge_current_limit_state' in self.temporary_script_states:
            discharge_power_limits_list.append(
                self.calc_discharge_power_limit_from_current(
                    local_values, self.temporary_script_states['discharge_current_limit_state']
                )
            )
            info_str += 'discharge_current_limit_state'
        if discharge_power_limits_list:
            local_values['discharge_power_limit_final'] = int(min(discharge_power_limits_list))
            self.logger.debug(
                'discharge_power_limit_final: ' + str(local_values['discharge_power_limit_final'])
                + ' from list: ' + ','.join(map(str, discharge_power_limits_list))
                + ' (' + info_str + ').'
            )
        else:
            self.logger.error('Determination of discharge power limits failed: no limits in list')
            local_values['discharge_power_limit_final'] = 0
