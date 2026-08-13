# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Map raw Victron/CCGX MQTT data into the controller's local_values dict.

Produces the key contract expected by BatteryProtector and StateMachine:
  battery_soc, battery_min_cell_voltage, battery_max_cell_voltage,
  battery_current, battery_voltage, solarcharger_power_sum, loads_total_power, ...
"""


class CcgxDataMapper:
    """Transforms self.CCGX_data-style nested dicts into flat local_values."""

    def __init__(self, logger):
        self.logger = logger

    def read_values_to_local_dict(self, ccgx_data, local_values):
        """Fill local_values from ccgx_data; set all_CCGX_values_available flag.

        Args:
            ccgx_data: nested dict with keys grid, battery, system, solarcharger, ...
            local_values: mutable dict to populate (typically empty for this cycle)

        Returns:
            local_values (same object, for convenience)
        """
        local_values['all_CCGX_values_available'] = True

        self._copy_required(
            ccgx_data.get('grid', {}), 'grid_power_sum', local_values, 'grid_power_sum'
        )
        self._copy_required(
            ccgx_data.get('battery', {}), 'soc', local_values, 'battery_soc'
        )
        self._copy_required(
            ccgx_data.get('battery', {}), 'max_cell_voltage', local_values, 'battery_max_cell_voltage'
        )
        self._copy_required(
            ccgx_data.get('battery', {}), 'min_cell_voltage', local_values, 'battery_min_cell_voltage'
        )
        self._copy_required(
            ccgx_data.get('battery', {}), 'current', local_values, 'battery_current'
        )
        self._copy_required(
            ccgx_data.get('battery', {}), 'power', local_values, 'battery_power'
        )
        self._copy_required(
            ccgx_data.get('battery', {}), 'voltage', local_values, 'battery_voltage'
        )
        self._copy_required(
            ccgx_data.get('system', {}), 'L1_loads_power_consumption',
            local_values, 'l1_loads_power_consumtpion'  # historical key spelling kept for compatibility
        )
        self._copy_required(
            ccgx_data.get('system', {}), 'L2_loads_power_consumption',
            local_values, 'l2_loads_power_consumtpion'
        )
        self._copy_required(
            ccgx_data.get('system', {}), 'L3_loads_power_consumption',
            local_values, 'l3_loads_power_consumtpion'
        )

        # Solarchargers: sum power/current of chargers still on the bus.
        # Empty stubs (charger already removed) are ignored. A charger that
        # is present but missing Power or Current still marks the snapshot incomplete.
        local_values['solarcharger_power_sum'] = 0
        local_values['solarcharger_current_sum'] = 0
        solarchargers = ccgx_data.get('solarcharger', {})
        for element in solarchargers:
            charger = solarchargers[element]
            if not charger:
                continue
            if 'Power' in charger:
                local_values['solarcharger_power_sum'] += charger['Power']
            else:
                local_values['all_CCGX_values_available'] = False
            if 'Current' in charger:
                local_values['solarcharger_current_sum'] += charger['Current']
            else:
                local_values['all_CCGX_values_available'] = False

        if local_values['all_CCGX_values_available']:
            local_values['loads_total_power'] = (
                local_values['l1_loads_power_consumtpion']
                + local_values['l2_loads_power_consumtpion']
                + local_values['l3_loads_power_consumtpion']
            )
            self.logger.debug(
                'Loads total power: ' + str(local_values['loads_total_power'])
                + ', Loads L1 power: ' + str(local_values['l1_loads_power_consumtpion'])
                + ', Loads L2 power: ' + str(local_values['l2_loads_power_consumtpion'])
                + ', Loads L3 power: ' + str(local_values['l3_loads_power_consumtpion'])
            )
            # Estimation of DC→AC losses (grid - battery + solar) - loads
            local_values['losses_dc2ac_est'] = (
                local_values['grid_power_sum']
                - local_values['battery_power']
                + local_values['solarcharger_power_sum']
            ) - local_values['loads_total_power']
            self.logger.debug(
                'Estimated losses DC to AC: ' + str(local_values['losses_dc2ac_est']) + 'W'
            )

        self.logger.debug(
            'Solarcharger power sum: ' + str(local_values['solarcharger_power_sum'])
            + ' Solarcharger current sum: ' + str(local_values['solarcharger_current_sum'])
        )
        if not local_values['all_CCGX_values_available']:
            # Kept at info for now (original TODO: drop back to debug)
            self.logger.info(
                'all_CCGX_values_available: "'
                + str(local_values['all_CCGX_values_available']) + '"'
            )

        return local_values

    def _copy_required(self, source, source_key, local_values, dest_key):
        """Copy source[source_key] → local_values[dest_key] or mark incomplete."""
        if source_key in source:
            local_values[dest_key] = source[source_key]
        else:
            local_values['all_CCGX_values_available'] = False
