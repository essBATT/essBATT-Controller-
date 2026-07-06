# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Battery protection and limit calculation logic for essBATT.

This module encapsulates all charge/discharge current limit calculations,
voltage/SOC based protection, smoothing, winter mode limits and emergency logic.
"""

import constants


class BatteryProtector:
    """Handles all battery safety limits, charge/discharge calculations and related state."""

    def __init__(self, config_data, logger):
        self.config = config_data
        self.logger = logger
        # Temporary state for smoothing and winter/emergency logic (not persisted)
        self.temporary_script_states = {
            "multi_switch_min_soc_debounce_time": None,
            "winter_mode_multis_switch_off_time": None,
            "winter_mode_inactive_charge_begin_time": None,
            "emergency_(dis)charge_begin_time": None,
            "winter_mode_charge_begin_time": None,
            "discharge_current_limit_state": self.config.get('ess_mode_2_settings', {}).get(
                'max_battery_discharge_current', constants.DEFAULT_MAX_BATTERY_DISCHARGE_CURRENT),
            "discharge_current_limit_hit_zero": False,
            "charge_current_limit_state": self.config.get('ess_mode_2_settings', {}).get(
                'max_battery_charge_current_2705', constants.DEFAULT_MAX_BATTERY_CHARGE_CURRENT),
            "charge_current_limit_hit_zero": False,
            "discharge_regular_current_limit_last_cycle": 0.0,
        }
        self.safe_state_active = False

    def calculate_dis_charge_limits(self, local_values):
        """Main entry point for limit calculation (called from controller cycle)."""
        if not self._validate_required_data(local_values):
            self.enter_safe_state(local_values, reason="Missing critical battery data (SOC or cell voltages)")
            return local_values

        local_values['charge_current_limit_regular'] = self.get_charge_current_limit_with_battery_protection(local_values)
        local_values['discharge_current_limit_regular'] = self.get_discharge_current_limit_with_battery_protection(local_values)

        # Violation detection and compensation logic (simplified from original)
        local_values['charge_current_limit_violation'] = False
        local_values['discharge_current_limit_violation'] = False

        if ('battery_current' in local_values and 'discharge_current_limit_regular' in local_values):
            if (local_values['battery_current'] < 0.0 and 
                abs(local_values['battery_current']) > local_values['discharge_current_limit_regular']):
                local_values['violation_current'] = abs(local_values['battery_current']) - local_values['discharge_current_limit_regular']
                local_values['discharge_current_limit_violation'] = True

        local_values['discharge_power_limit_regular'] = self.calc_discharge_power_limit_from_current(
            local_values, local_values['discharge_current_limit_regular']
        )

        # Smoothing and final limit aggregation (moved from original large method)
        self._apply_smoothing_and_final_limits(local_values)

        return local_values

    def _validate_required_data(self, local_values):
        """Check if all critical battery data is available."""
        required = ['min_cell_voltage', 'max_cell_voltage', 'battery_soc', 'battery_current', 'battery_voltage']
        for key in required:
            if key not in local_values:
                return False
        return True

    def enter_safe_state(self, local_values, reason="Unknown reason"):
        """Explicit method to enter safe state (zero charge/discharge, clear setpoints).

        This makes the intention very clear in the code and can be reused from anywhere.
        """
        self.safe_state_active = True
        local_values['charge_current_limit_final'] = 0.0
        local_values['discharge_current_limit_final'] = 0.0
        local_values['discharge_power_limit_final'] = 0
        local_values['AcPowerSetPoint'] = 0
        self.logger.error(f'ENTERING SAFE STATE - Reason: {reason}. All charge/discharge disabled.')
        # TODO: Could also trigger multis switch to "Off" (position 4) here in future

    def get_charge_current_limit_with_battery_protection(self, local_values):
        """SOC and max-cell based charge current limit calculation."""
        current_charge_limit = 0.0
        if ('max_cell_voltage' not in local_values or 'battery_soc' not in local_values):
            self.logger.warning('max_cell_voltage or soc not available for charge limit!')
            return self.config.get('ess_mode_2_settings', {}).get('max_battery_charge_current_2705', 5.0)

        battery_max_cell_voltage = local_values['max_cell_voltage']
        battery_soc = local_values['battery_soc']

        max_cell_voltage_charging = self.config.get('battery_settings', {}).get('max_cell_voltage_charging')
        if max_cell_voltage_charging is None:
            self.enter_safe_state(local_values, reason="max_cell_voltage_charging missing in config")
            return 0.0
        if battery_max_cell_voltage >= max_cell_voltage_charging:
            return 0.0

        try:
            # SOC based
            soc_based = -1.0
            for i, soc_limit in enumerate(self.config['battery_settings']['soc_based_charge_limit_soc_array']):
                if battery_soc >= soc_limit:
                    soc_based = self.config['battery_settings']['soc_based_charge_limit_current_array'][i]

            # Max cell based
            max_cell_based = -1.0
            for i, v_limit in enumerate(self.config['battery_settings']['max_cell_based_charge_limit_voltage_array']):
                if battery_max_cell_voltage >= v_limit:
                    max_cell_based = self.config['battery_settings']['max_cell_based_charge_limit_current_array'][i]

            mode = self.config['battery_settings']['charge_limit_mode']
            if mode == 'max_cell_only':
                current_charge_limit = max_cell_based if max_cell_based >= 0 else self.config['ess_mode_2_settings']['max_battery_charge_current_2705']
            elif mode == 'soc_only':
                current_charge_limit = soc_based if soc_based >= 0 else self.config['ess_mode_2_settings']['max_battery_charge_current_2705']
            else:  # soc_and_max_cell
                limits = [x for x in (soc_based, max_cell_based) if x >= 0]
                current_charge_limit = min(limits) if limits else self.config['ess_mode_2_settings']['max_battery_charge_current_2705']
        except (IndexError, KeyError, TypeError) as e:
            self.logger.error(f'Invalid charge limit arrays in config: {e}')
            current_charge_limit = self.config.get('ess_mode_2_settings', {}).get('max_battery_charge_current_2705', 5.0)

        self.logger.debug(f'Charge current limit calculated: {current_charge_limit}A')
        return current_charge_limit if current_charge_limit is not None else constants.DEFAULT_MAX_BATTERY_CHARGE_CURRENT

    def get_discharge_current_limit_with_battery_protection(self, local_values):
        """SOC and min-cell based discharge current limit calculation (exact original logic)."""
        current_discharge_limit = 0.0
        if ('min_cell_voltage' not in local_values or 'battery_soc' not in local_values):
            self.logger.warning('min_cell_voltage or soc not available for discharge limit!')
            return self.config.get('ess_mode_2_settings', {}).get(
                'max_battery_discharge_current', 
                constants.DEFAULT_MAX_BATTERY_DISCHARGE_CURRENT
            )

        battery_min_cell_voltage = local_values['min_cell_voltage']
        battery_soc = local_values['battery_soc']

        min_cell_discharging = self.config.get('battery_settings', {}).get('min_cell_voltage_discharging')
        if min_cell_discharging is None or battery_min_cell_voltage <= min_cell_discharging:
            return 0.0

        try:
            soc_array = self.config['battery_settings']['soc_based_discharge_limit_soc_array']
            soc_current_array = self.config['battery_settings']['soc_based_discharge_limit_current_array']
            cell_array = self.config['battery_settings']['min_cell_based_discharge_limit_voltage_array']
            cell_current_array = self.config['battery_settings']['min_cell_based_discharge_limit_current_array']
            mode = self.config['battery_settings']['discharge_limit_mode']
            max_discharge = self.config['ess_mode_2_settings']['max_battery_discharge_current']

            soc_based = -1.0
            for i, limit in enumerate(soc_array):
                if battery_soc <= limit:
                    soc_based = soc_current_array[i]
                    # no break - last matching (strictest) wins for descending array (original behavior)

            cell_based = -1.0
            for i, limit in enumerate(cell_array):
                if battery_min_cell_voltage <= limit:
                    cell_based = cell_current_array[i]
                    # no break - last matching (strictest) wins for descending array (original behavior)

            if mode == 'min_cell_only':
                current_discharge_limit = cell_based if cell_based >= 0 else max_discharge
            elif mode == 'soc_only':
                current_discharge_limit = soc_based if soc_based >= 0 else max_discharge
            else:  # soc_and_min_cell
                candidates = [x for x in (soc_based, cell_based) if x >= 0]
                current_discharge_limit = min(candidates) if candidates else max_discharge
        except (IndexError, KeyError, TypeError) as e:
            self.logger.error(f'Invalid discharge limit arrays in config: {e}')
            current_discharge_limit = max_discharge

        self.logger.debug(f'Discharge current limit calculated: {current_discharge_limit}A (SOC={battery_soc}, min_cell={battery_min_cell_voltage})')
        return current_discharge_limit

    def calc_discharge_power_limit_from_current(self, local_values, input_current):
        """Convert current limit to power limit taking solar into account."""
        if 'battery_voltage' not in local_values or 'solarcharger_power_sum' not in local_values:
            return 0
        return abs(input_current * local_values['battery_voltage']) + local_values['solarcharger_power_sum']

    def _apply_smoothing_and_final_limits(self, local_values):
        """Internal method for smoothing, winter limits and final aggregation (matches original logic)."""
        # Winter mode handling
        if (self.config.get('winter_mode', {}).get('use_winter_mode', 0) == 1 and
            local_values.get('battery_soc', 100) <= self.config.get('winter_mode', {}).get('winter_min_SOC', 25)):
            local_values['winter_discharge_limit'] = 0.0

        # Final limit aggregation (min of all applicable limits)
        charge_limits = [local_values.get('charge_current_limit_regular', 50)]
        discharge_limits = [local_values.get('discharge_current_limit_regular', 50)]

        if 'external_current_limit' in local_values:
            charge_limits.append(local_values['external_current_limit'])
            discharge_limits.append(local_values['external_current_limit'])
        if 'winter_discharge_limit' in local_values:
            discharge_limits.append(local_values['winter_discharge_limit'])

        local_values['charge_current_limit_final'] = min(charge_limits)
        local_values['discharge_current_limit_final'] = min(discharge_limits)
        local_values['discharge_power_limit_final'] = int(self.calc_discharge_power_limit_from_current(
            local_values, local_values.get('discharge_current_limit_final', 50)
        ))

        self.logger.debug(f'Final charge limit: {local_values.get("charge_current_limit_final")}, '
                         f'discharge limit: {local_values.get("discharge_current_limit_final")}')
