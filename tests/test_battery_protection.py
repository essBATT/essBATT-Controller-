"""Tests for BatteryProtector (Step 3 of modularization).

These tests use mocked local_values and config to isolate from MQTT and real hardware.
The protector has no direct MQTT dependency — it works on prepared data dictionaries.
"""

import pytest
from unittest.mock import MagicMock

from battery_protection import BatteryProtector
import constants


@pytest.fixture
def mocked_logger():
    return MagicMock()


@pytest.fixture
def sample_config():
    return {
        "ess_mode_2_settings": {
            "max_battery_discharge_current": 50,
            "max_battery_charge_current_2705": 40,
        },
        "battery_settings": {
            "charge_limit_mode": "max_cell_only",
            "max_cell_voltage_charging": 3.55,
            "max_cell_voltage_charging_resume": 3.50,
            "soc_based_charge_limit_soc_array": [80, 90, 95],
            "soc_based_charge_limit_current_array": [25, 10, 5],
            "max_cell_based_charge_limit_voltage_array": [3.42, 3.44, 3.47, 3.49, 3.5],
            "max_cell_based_charge_limit_current_array": [50, 20, 10, 5, 1.8],
            "discharge_limit_mode": "soc_and_min_cell",
            "min_cell_voltage_discharging": 3.10,
            "min_cell_voltage_discharging_resume": 3.25,
            "soc_based_discharge_limit_soc_array": [20, 15, 11],
            "soc_based_discharge_limit_current_array": [18, 8, 2],
            "min_cell_based_discharge_limit_voltage_array": [3.16, 3.15, 3.11, 3.10],
            "min_cell_based_discharge_limit_current_array": [50, 20, 8, 0],
            "smooth_voltage_based_(dis)charge_limits": 1,
            "compensate_current_limit_violations": 0,
        },
        "winter_mode": {"use_winter_mode": 0},
    }


def test_enter_safe_state(mocked_logger, sample_config):
    """Test that enter_safe_state sets all limits to zero and logs the reason."""
    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {}

    protector.enter_safe_state(local_values, reason="Test missing data")

    assert protector.safe_state_active is True
    assert local_values.get('charge_current_limit_final') == 0.0
    assert local_values.get('discharge_current_limit_final') == 0.0
    assert local_values.get('discharge_power_limit_final') == 0
    assert local_values.get('AcPowerSetPoint') == 0
    mocked_logger.error.assert_called_once()
    assert "ENTERING SAFE STATE" in mocked_logger.error.call_args[0][0]


def test_calculate_limits_normal_case(mocked_logger, sample_config):
    """Test normal limit calculation with realistic battery data."""
    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {
        'battery_soc': 85,
        'max_cell_voltage': 3.45,
        'min_cell_voltage': 3.20,
        'battery_current': 10.0,
        'battery_voltage': 52.0,
        'solarcharger_power_sum': 200,
        'all_CCGX_values_available': True,
    }

    result = protector.calculate_dis_charge_limits(local_values)

    assert result['charge_current_limit_final'] > 0
    assert result['discharge_current_limit_final'] > 0
    assert 'discharge_power_limit_final' in result
    assert protector.safe_state_active is False


def test_safe_state_on_missing_data(mocked_logger, sample_config):
    """Test that missing critical data triggers safe state."""
    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {
        'battery_soc': 50,           # only SOC present
        # missing min_cell_voltage, max_cell_voltage, etc.
    }

    result = protector.calculate_dis_charge_limits(local_values)

    assert protector.safe_state_active is True
    assert result.get('charge_current_limit_final') == 0.0
    assert result.get('discharge_current_limit_final') == 0.0


@pytest.mark.parametrize("soc,max_cell,expected_charge", [
    (75, 3.40, 25),   # from soc_based_charge_limit_soc_array
    (92, 3.45, 10),
    (96, 3.48, 5),
    (50, 3.60, 0),    # max cell voltage exceeded
])
def test_charge_limit_scenarios(mocked_logger, sample_config, soc, max_cell, expected_charge):
    """Parametrized test for different SOC and cell voltage combinations."""
    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {
        'battery_soc': soc,
        'max_cell_voltage': max_cell,
        'min_cell_voltage': 3.20,
        'battery_current': 5.0,
        'battery_voltage': 52.0,
        'solarcharger_power_sum': 0,
        'all_CCGX_values_available': True,
    }

    protector.calculate_dis_charge_limits(local_values)
    assert local_values['charge_current_limit_final'] == pytest.approx(expected_charge, abs=0.1)


def test_discharge_limit_min_cell_trigger(mocked_logger, sample_config):
    """Test that very low min cell voltage forces discharge limit to 0."""
    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {
        'battery_soc': 50,
        'max_cell_voltage': 3.40,
        'min_cell_voltage': 3.05,      # below min_cell_voltage_discharging
        'battery_current': -10.0,
        'battery_voltage': 52.0,
        'solarcharger_power_sum': 0,
        'all_CCGX_values_available': True,
    }

    protector.calculate_dis_charge_limits(local_values)
    assert local_values['discharge_current_limit_final'] == 0.0
