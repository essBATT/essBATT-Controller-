"""Tests for BatteryProtector (Step 3 of modularization).

Comprehensive path coverage tests:
- Good case (within limits)
- At limit (boundary)
- Above limit (violation → safe state or reduced current)

Uses parametrized tests for all major battery config parameters.
No real MQTT dependency — tests use mocked local_values.
"""

import pytest
from unittest.mock import MagicMock

from battery_protection import BatteryProtector


@pytest.fixture
def mocked_logger():
    return MagicMock()


@pytest.fixture
def sample_config():
    """Realistic config based on the project's ess_config.json."""
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
            "max_cell_based_charge_limit_voltage_array": [3.42, 3.44, 3.47, 3.49, 3.55],
            "max_cell_based_charge_limit_current_array": [50, 20, 10, 5, 0],
            "discharge_limit_mode": "soc_and_min_cell",
            "min_cell_voltage_discharging": 3.10,
            "min_cell_voltage_discharging_resume": 3.25,
            "soc_based_discharge_limit_soc_array": [20, 15, 11],
            "soc_based_discharge_limit_current_array": [18, 8, 2],
            "min_cell_based_discharge_limit_voltage_array": [3.16, 3.15, 3.11, 3.10],
            "min_cell_based_discharge_limit_current_array": [50, 20, 8, 0],
            "smooth_voltage_based_(dis)charge_limits": 1,
            "compensate_current_limit_violations": 0,
            "emergency_(dis)charge": {
                "use_emergency_(dis)charging": 1,
                "max_cell_voltage_for_emergency_discharge": 3.63,
                "min_cell_voltage_for_emergency_charge": 3.05,
            }
        },
        "winter_mode": {
            "use_winter_mode": 1,
            "winter_min_SOC": 25,
        },
    }


# ==================== CHARGE LIMIT PATH COVERAGE ====================

@pytest.mark.parametrize("soc,max_cell,expected_charge,description", [
    # Good case - well below limits
    (50, 3.30, 40, "good_case_well_below_limits"),
    # At SOC boundary
    (80, 3.40, 25, "at_soc_boundary_80"),
    (90, 3.40, 10, "at_soc_boundary_90"),
    (95, 3.40, 5, "at_soc_boundary_95"),
    # At max cell voltage boundary
    (60, 3.55, 0, "at_max_cell_voltage_limit"),
    # Above limit → should be 0
    (60, 3.56, 0, "above_max_cell_voltage"),
    # SOC + Cell both active
    (92, 3.48, 5, "both_soc_and_cell_active"),
])
def test_charge_current_limit_path_coverage(mocked_logger, sample_config, soc, max_cell, expected_charge, description):
    """Test all relevant paths for charge current limits (good, boundary, violation)."""
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

    assert local_values['charge_current_limit_final'] == pytest.approx(expected_charge, abs=0.1), f"Failed for {description}"


# ==================== DISCHARGE LIMIT PATH COVERAGE ====================

@pytest.mark.parametrize("soc,min_cell,expected_discharge,description", [
    # Good case
    (50, 3.30, 50, "good_case_discharge"),
    # At SOC boundary
    (20, 3.20, 18, "at_soc_discharge_boundary_20"),
    (15, 3.20, 8, "at_soc_discharge_boundary_15"),
    (11, 3.20, 2, "at_soc_discharge_boundary_11"),
    # At min cell voltage boundary
    (50, 3.10, 0, "at_min_cell_voltage_limit"),
    # Below limit → 0
    (50, 3.09, 0, "below_min_cell_voltage"),
])
def test_discharge_current_limit_path_coverage(mocked_logger, sample_config, soc, min_cell, expected_discharge, description):
    """Test all relevant paths for discharge current limits."""
    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {
        'battery_soc': soc,
        'max_cell_voltage': 3.40,
        'min_cell_voltage': min_cell,
        'battery_current': -10.0,
        'battery_voltage': 52.0,
        'solarcharger_power_sum': 0,
        'all_CCGX_values_available': True,
    }

    protector.calculate_dis_charge_limits(local_values)

    assert local_values['discharge_current_limit_final'] == pytest.approx(expected_discharge, abs=0.1), f"Failed for {description}"


# ==================== SAFE STATE & WINTER MODE ====================

def test_safe_state_triggered_on_missing_critical_data(mocked_logger, sample_config):
    """Missing critical data (SOC or cell voltages) must trigger safe state."""
    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {
        'battery_soc': 50,           # only SOC present
        # missing min_cell_voltage, max_cell_voltage, battery_voltage etc.
    }

    protector.calculate_dis_charge_limits(local_values)

    assert protector.safe_state_active is True
    assert local_values.get('charge_current_limit_final') == 0.0
    assert local_values.get('discharge_current_limit_final') == 0.0
    assert local_values.get('AcPowerSetPoint') == 0


def test_winter_mode_discharge_limit(mocked_logger, sample_config):
    """Winter mode should force discharge limit to 0 when SOC is too low."""
    sample_config["winter_mode"]["use_winter_mode"] = 1
    sample_config["winter_mode"]["winter_min_SOC"] = 30

    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {
        'battery_soc': 25,                    # below winter_min_SOC
        'max_cell_voltage': 3.40,
        'min_cell_voltage': 3.20,
        'battery_current': -5.0,
        'battery_voltage': 52.0,
        'solarcharger_power_sum': 0,
        'all_CCGX_values_available': True,
    }

    protector.calculate_dis_charge_limits(local_values)

    assert local_values.get('discharge_current_limit_final') == 0.0


# ==================== BOUNDARY & EDGE CASES ====================

@pytest.mark.parametrize("max_cell,expected", [
    (3.54, 5),      # just below charging limit
    (3.55, 0),      # exactly at limit
    (3.56, 0),      # above limit
])
def test_max_cell_voltage_boundaries(mocked_logger, sample_config, max_cell, expected):
    """Explicit boundary testing for max_cell_voltage_charging."""
    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {
        'battery_soc': 60,
        'max_cell_voltage': max_cell,
        'min_cell_voltage': 3.20,
        'battery_current': 0,
        'battery_voltage': 52.0,
        'solarcharger_power_sum': 0,
        'all_CCGX_values_available': True,
    }

    protector.calculate_dis_charge_limits(local_values)
    assert local_values['charge_current_limit_final'] == pytest.approx(expected, abs=0.1)


def test_emergency_charge_condition(mocked_logger, sample_config):
    """Test emergency charge trigger when min cell voltage is critically low."""
    protector = BatteryProtector(sample_config, mocked_logger)
    local_values = {
        'battery_soc': 20,
        'max_cell_voltage': 3.40,
        'min_cell_voltage': 3.04,          # below emergency threshold
        'battery_current': 0,
        'battery_voltage': 52.0,
        'solarcharger_power_sum': 0,
        'all_CCGX_values_available': True,
    }

    protector.calculate_dis_charge_limits(local_values)

    # In current implementation this should trigger safe state or emergency logic
    # (depending on how emergency is wired in the state machine)
    assert 'emergency_(dis)charge_begin_time' in protector.temporary_script_states
