"""Tests for SetpointCalculator (Step 5 of modularization)."""

import pytest
from unittest.mock import MagicMock

from setpoint_control import SetpointCalculator, AC_POWER_SETPOINT_MARGIN


@pytest.fixture
def config():
    return {
        "ess_mode_2_settings": {
            "grid_power_setpoint_2700": 1,
        }
    }


@pytest.fixture
def controller_state():
    return {
        "current_state": "normal_operation",
        "charge_to_SOC": {
            "requested_current_direction": "none",
            "target_SOC": "none",
            "max_current": "none",
        },
        "balancing": {"max_current": "none"},
    }


@pytest.fixture
def calculator(config, controller_state):
    return SetpointCalculator(config, MagicMock(), controller_state)


def _local_for_charge(**overrides):
    base = {
        "charge_current_limit_final": 10.0,
        "discharge_power_limit_final": 500,
        "battery_voltage": 50.0,
        "loads_total_power": 200,
        "solarcharger_power_sum": 100,
    }
    base.update(overrides)
    return base


def test_normal_operation_uses_grid_setpoint(calculator, controller_state):
    controller_state["current_state"] = "normal_operation"
    local = _local_for_charge()
    result = calculator.calculate_ac_power_setpoint(local)
    assert result == 1
    assert local["AcPowerSetPoint"] == 1


def test_charge_to_soc_charge_direction(calculator, controller_state):
    controller_state["current_state"] = "charge_to_SOC"
    controller_state["charge_to_SOC"]["requested_current_direction"] = "charge"
    local = _local_for_charge(
        charge_current_limit_final=10.0,
        battery_voltage=50.0,
        loads_total_power=200,
        solarcharger_power_sum=100,
    )
    # ((10*50) + 200 - 100) * 1.1 = (500+200-100)*1.1 = 660
    result = calculator.calculate_ac_power_setpoint(local)
    assert result == int((10 * 50 + 200 - 100) * AC_POWER_SETPOINT_MARGIN)
    assert local["AcPowerSetPoint"] == 660


def test_charge_to_soc_discharge_direction(calculator, controller_state):
    controller_state["current_state"] = "charge_to_SOC"
    controller_state["charge_to_SOC"]["requested_current_direction"] = "discharge"
    local = _local_for_charge(
        discharge_power_limit_final=500,
        loads_total_power=200,
        solarcharger_power_sum=100,
    )
    # (-500 - 100 + 200) * 1.1 = (-400) * 1.1 = -440
    result = calculator.calculate_ac_power_setpoint(local)
    assert result == int((-500 - 100 + 200) * AC_POWER_SETPOINT_MARGIN)
    assert local["AcPowerSetPoint"] == -440


def test_charge_to_soc_reached_uses_grid_setpoint(calculator, controller_state):
    controller_state["current_state"] = "charge_to_SOC"
    controller_state["charge_to_SOC"]["requested_current_direction"] = "SOC_reached"
    local = _local_for_charge()
    result = calculator.calculate_ac_power_setpoint(local)
    assert result == 1


def test_charge_to_soc_unknown_direction_keeps_default(calculator, controller_state):
    controller_state["current_state"] = "charge_to_SOC"
    controller_state["charge_to_SOC"]["requested_current_direction"] = "bogus"
    local = _local_for_charge()
    result = calculator.calculate_ac_power_setpoint(local)
    assert result == 0
    assert local["AcPowerSetPoint"] == 0


def test_balancing_charge_setpoint(calculator, controller_state):
    controller_state["current_state"] = "balancing"
    local = _local_for_charge(
        charge_current_limit_final=5.0,
        battery_voltage=52.0,
        loads_total_power=300,
        solarcharger_power_sum=50,
    )
    # ((5*52) + 300 - 50) * 1.1 = (260+300-50)*1.1 = 561
    result = calculator.calculate_ac_power_setpoint(local)
    assert result == int((5 * 52 + 300 - 50) * AC_POWER_SETPOINT_MARGIN)
    assert local["AcPowerSetPoint"] == 561


def test_unknown_state_defaults_to_zero(calculator, controller_state):
    controller_state["current_state"] = "something_weird"
    local = _local_for_charge()
    result = calculator.calculate_ac_power_setpoint(local)
    assert result == 0


def test_update_config_changes_grid_setpoint(calculator, controller_state):
    controller_state["current_state"] = "normal_operation"
    calculator.update_config({"ess_mode_2_settings": {"grid_power_setpoint_2700": 42}})
    local = _local_for_charge()
    assert calculator.calculate_ac_power_setpoint(local) == 42
