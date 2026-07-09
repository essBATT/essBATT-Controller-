# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Victron ESS Mode 2 setpoint calculations for essBATT.

Currently implements AcPowerSetPoint calculation based on controller state
(normal_operation, charge_to_SOC, balancing).
"""

# Safety margin above battery-limited power so MaxChargeCurrent/MaxDischargePower
# remain the hard limits, while AcPowerSetPoint is not left unbounded.
AC_POWER_SETPOINT_MARGIN = 1.1


class SetpointCalculator:
    """Calculates Victron ESS setpoints from config, state and local_values."""

    def __init__(self, config, logger, controller_state):
        self.config = config
        self.logger = logger
        self.ess_controller_state = controller_state

    def update_config(self, config):
        """Update config reference (online reload)."""
        self.config = config

    def calculate_ac_power_setpoint(self, local_values):
        """Compute AcPowerSetPoint for the current controller state.

        Mutates local_values['AcPowerSetPoint'] and returns it.
        """
        local_values['AcPowerSetPoint'] = 0  # default if something fails
        current_state = self.ess_controller_state.get('current_state')

        if current_state == 'normal_operation':
            local_values['AcPowerSetPoint'] = int(
                self.config['ess_mode_2_settings']['grid_power_setpoint_2700']
            )
            self.logger.debug(
                'AcPowerSetPoint from NORMAL OPERATION set to:'
                + str(local_values['AcPowerSetPoint'])
            )

        elif current_state == 'charge_to_SOC':
            direction = self.ess_controller_state['charge_to_SOC']['requested_current_direction']
            if direction == 'charge':
                max_charge_power_final = (
                    (local_values['charge_current_limit_final'] * local_values['battery_voltage'])
                    + local_values['loads_total_power']
                    - local_values['solarcharger_power_sum']
                ) * AC_POWER_SETPOINT_MARGIN
                local_values['AcPowerSetPoint'] = int(max_charge_power_final)
            elif direction == 'discharge':
                local_values['AcPowerSetPoint'] = int(
                    (
                        -local_values['discharge_power_limit_final']
                        - local_values['solarcharger_power_sum']
                        + local_values['loads_total_power']
                    ) * AC_POWER_SETPOINT_MARGIN
                )
            elif direction == 'SOC_reached':
                local_values['AcPowerSetPoint'] = int(
                    self.config['ess_mode_2_settings']['grid_power_setpoint_2700']
                )
            else:
                self.logger.error(
                    'Unknown requested discharge direction: ' + str(direction)
                )
                return local_values['AcPowerSetPoint']
            self.logger.debug(
                'AcPowerSetPoint from CHARGE TO SOC set to:'
                + str(local_values['AcPowerSetPoint'])
            )

        elif current_state == 'balancing':
            max_charge_power_final = (
                (local_values['charge_current_limit_final'] * local_values['battery_voltage'])
                + local_values['loads_total_power']
                - local_values['solarcharger_power_sum']
            ) * AC_POWER_SETPOINT_MARGIN
            local_values['AcPowerSetPoint'] = int(max_charge_power_final)
            self.logger.debug(
                'AcPowerSetPoint from BALANCING set to:' + str(local_values['AcPowerSetPoint'])
            )
            self.logger.debug(
                'AcPowerSetPoint Calculation:'
                + str(local_values['AcPowerSetPoint'])
                + '= (Current limit final:' + str(local_values['charge_current_limit_final'])
                + '* battery voltage: ' + str(local_values['battery_voltage'])
                + ') + total loads: ' + str(local_values['loads_total_power'])
                + ' - solarcharger input: ' + str(local_values['solarcharger_power_sum'])
            )

        else:
            self.logger.error('Unknown "current state". Check ess_controller_state file.')

        return local_values['AcPowerSetPoint']
