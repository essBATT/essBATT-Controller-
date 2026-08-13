"""Tests for BatteryProtector (Step 3 of modularization).

Comprehensive path coverage tests:
- Good case (within limits)
- At limit (boundary)
- Above limit (violation → safe state or reduced current)
- Controller key contract (battery_min/max_cell_voltage)
- Smoothing, external limits, winter discharge lock, violation compensation

Uses parametrized tests for all major battery config parameters.
No real MQTT dependency — tests use mocked local_values.
"""

import pytest
from unittest.mock import MagicMock

from battery_protection import BatteryProtector, REQUIRED_BATTERY_KEYS


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
            "smooth_voltage_based_(dis)charge_limits": 0,  # off by default in unit tests
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


@pytest.fixture
def temp_states(sample_config):
    """Shared temporary script states (as produced by ConfigManager)."""
    return {
        "multi_switch_min_soc_debounce_time": None,
        "winter_mode_multis_switch_off_time": None,
        "winter_mode_inactive_charge_begin_time": None,
        "emergency_charge_begin_time": None,
        "emergency_discharge_begin_time": None,
        "winter_mode_charge_begin_time": None,
        "discharge_current_limit_state": sample_config["ess_mode_2_settings"]["max_battery_discharge_current"],
        "discharge_current_limit_hit_zero": False,
        "charge_current_limit_state": sample_config["ess_mode_2_settings"]["max_battery_charge_current_2705"],
        "charge_current_limit_hit_zero": False,
        "discharge_regular_current_limit_last_cycle": 0.0,
    }


@pytest.fixture
def controller_state():
    return {
        "current_state": "normal_operation",
        "winter_mode": "not_activated",
        "winter_SOC_discharge_limit": "not_activated",
        "balancing": {"activation_time": "none", "max_current": "none", "scheduled_start_time": "none"},
        "charge_to_SOC": {
            "activation_time": "none",
            "target_SOC": "none",
            "max_current": "none",
            "scheduled_start_time": "none",
            "requested_current_direction": "none",
        },
    }


@pytest.fixture
def protector(mocked_logger, sample_config, temp_states, controller_state):
    return BatteryProtector(
        sample_config,
        mocked_logger,
        temp_states,
        controller_state,
        external_input={},
    )


def _controller_shaped_local_values(**overrides):
    """local_values as produced by controller.read_values_to_local_dict()."""
    base = {
        'battery_soc': 50,
        'battery_max_cell_voltage': 3.30,
        'battery_min_cell_voltage': 3.20,
        'battery_current': 5.0,
        'battery_voltage': 52.0,
        'solarcharger_power_sum': 0,
        'all_CCGX_values_available': True,
    }
    base.update(overrides)
    return base


# ==================== KEY CONTRACT / INTEGRATION ====================

def test_controller_key_names_are_accepted(protector):
    """Controller uses battery_min/max_cell_voltage — must NOT enter safe state."""
    local_values = _controller_shaped_local_values()
    protector.calculate_dis_charge_limits(local_values)

    assert protector.safe_state_active is False
    assert local_values['charge_current_limit_final'] == pytest.approx(40, abs=0.1)
    assert local_values['discharge_current_limit_final'] == pytest.approx(50, abs=0.1)


def test_wrong_legacy_keys_trigger_safe_state(protector):
    """Old incorrect key names must fail validation (documents the contract)."""
    local_values = {
        'battery_soc': 50,
        'max_cell_voltage': 3.30,   # wrong (legacy test keys)
        'min_cell_voltage': 3.20,
        'battery_current': 5.0,
        'battery_voltage': 52.0,
        'solarcharger_power_sum': 0,
    }
    protector.calculate_dis_charge_limits(local_values)
    assert protector.safe_state_active is True
    assert local_values['charge_current_limit_final'] == 0.0
    assert local_values['discharge_current_limit_final'] == 0.0


def test_required_battery_keys_constant_matches_controller_contract():
    assert 'battery_min_cell_voltage' in REQUIRED_BATTERY_KEYS
    assert 'battery_max_cell_voltage' in REQUIRED_BATTERY_KEYS
    assert 'min_cell_voltage' not in REQUIRED_BATTERY_KEYS
    assert 'max_cell_voltage' not in REQUIRED_BATTERY_KEYS


# ==================== CHARGE LIMIT PATH COVERAGE ====================

@pytest.mark.parametrize("soc,max_cell,expected_charge,mode,description", [
    (50, 3.30, 40, "max_cell_only", "good_case_well_below_limits"),
    (80, 3.40, 25, "soc_and_max_cell", "at_soc_boundary_80"),
    (90, 3.40, 10, "soc_and_max_cell", "at_soc_boundary_90"),
    (95, 3.40, 5, "soc_and_max_cell", "at_soc_boundary_95"),
    (60, 3.55, 0, "max_cell_only", "at_max_cell_voltage_limit"),
    (60, 3.56, 0, "max_cell_only", "above_max_cell_voltage"),
    (92, 3.48, 10, "soc_and_max_cell", "both_soc_and_cell_active"),
])
def test_charge_current_limit_path_coverage(
    protector, sample_config, soc, max_cell, expected_charge, mode, description
):
    sample_config['battery_settings']['charge_limit_mode'] = mode
    local_values = _controller_shaped_local_values(
        battery_soc=soc,
        battery_max_cell_voltage=max_cell,
        battery_min_cell_voltage=3.20,
        battery_current=5.0,
    )
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['charge_current_limit_final'] == pytest.approx(expected_charge, abs=0.1), description


# ==================== DISCHARGE LIMIT PATH COVERAGE ====================

@pytest.mark.parametrize("soc,min_cell,expected_discharge,mode,description", [
    (50, 3.30, 50, "soc_and_min_cell", "good_case_discharge"),
    (20, 3.20, 18, "soc_and_min_cell", "at_soc_discharge_boundary_20"),
    (15, 3.20, 8, "soc_and_min_cell", "at_soc_discharge_boundary_15"),
    (11, 3.20, 2, "soc_and_min_cell", "at_soc_discharge_boundary_11"),
    (50, 3.10, 0, "soc_and_min_cell", "at_min_cell_voltage_limit"),
    (50, 3.09, 0, "soc_and_min_cell", "below_min_cell_voltage"),
])
def test_discharge_current_limit_path_coverage(
    protector, sample_config, soc, min_cell, expected_discharge, mode, description
):
    sample_config['battery_settings']['discharge_limit_mode'] = mode
    sample_config['winter_mode']['use_winter_mode'] = 0
    local_values = _controller_shaped_local_values(
        battery_soc=soc,
        battery_max_cell_voltage=3.40,
        battery_min_cell_voltage=min_cell,
        battery_current=-10.0,
    )
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['discharge_current_limit_final'] == pytest.approx(expected_discharge, abs=0.1), description


# ==================== SAFE STATE & WINTER ====================

def test_safe_state_triggered_on_missing_critical_data(protector):
    local_values = {'battery_soc': 50}
    protector.calculate_dis_charge_limits(local_values)
    assert protector.safe_state_active is True
    assert local_values.get('charge_current_limit_final') == 0.0
    assert local_values.get('discharge_current_limit_final') == 0.0
    assert local_values.get('AcPowerSetPoint') == 0


def test_winter_discharge_only_when_state_activated(protector, controller_state):
    """Winter discharge lock requires winter_mode + winter_SOC_discharge_limit activated."""
    local_values = _controller_shaped_local_values(battery_soc=20, battery_current=-5.0)

    # Config has use_winter_mode but state not activated → discharge allowed
    controller_state['winter_mode'] = 'not_activated'
    controller_state['winter_SOC_discharge_limit'] = 'not_activated'
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['discharge_current_limit_final'] > 0

    # Both activated → discharge locked
    controller_state['winter_mode'] = 'activated'
    controller_state['winter_SOC_discharge_limit'] = 'activated'
    local_values = _controller_shaped_local_values(battery_soc=20, battery_current=-5.0)
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['discharge_current_limit_final'] == 0.0
    assert local_values.get('winter_discharge_limit') == 0.0


def test_low_soc_in_summer_does_not_zero_discharge(protector, controller_state):
    """Regression: use_winter_mode=1 alone must not zero discharge year-round."""
    controller_state['winter_mode'] = 'not_activated'
    controller_state['winter_SOC_discharge_limit'] = 'not_activated'
    local_values = _controller_shaped_local_values(battery_soc=10, battery_current=-5.0)
    protector.calculate_dis_charge_limits(local_values)
    # SOC 10 hits soc discharge array (2A) but must not be forced to 0 by winter shortcut
    assert local_values['discharge_current_limit_final'] == pytest.approx(2, abs=0.1)


# ==================== EXTERNAL / MODE CURRENT LIMITS ====================

def test_balancing_max_current_restricts_limits(protector, controller_state):
    controller_state['current_state'] = 'balancing'
    controller_state['balancing']['max_current'] = 12
    local_values = _controller_shaped_local_values(battery_soc=50, battery_max_cell_voltage=3.30)
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['external_current_limit'] == 12
    assert local_values['charge_current_limit_final'] == pytest.approx(12, abs=0.1)
    assert local_values['discharge_current_limit_final'] == pytest.approx(12, abs=0.1)


def test_charge_to_soc_max_current_restricts_limits(protector, controller_state):
    controller_state['current_state'] = 'charge_to_SOC'
    controller_state['charge_to_SOC']['max_current'] = 8
    local_values = _controller_shaped_local_values()
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['charge_current_limit_final'] == pytest.approx(8, abs=0.1)


def test_deactivate_charge_from_external_input(protector, sample_config, temp_states, controller_state, mocked_logger):
    protector = BatteryProtector(
        sample_config, mocked_logger, temp_states, controller_state,
        external_input={'deactivate_charge': {'activated': True}},
    )
    local_values = _controller_shaped_local_values()
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['charge_current_limit_final'] == 0.0


# ==================== VIOLATION COMPENSATION ====================

def test_charge_violation_compensation(protector, sample_config):
    sample_config['battery_settings']['compensate_current_limit_violations'] = 1
    # max charge 40; battery drawing 45A charge → violation 5A → compensated regular 35
    local_values = _controller_shaped_local_values(
        battery_soc=50,
        battery_max_cell_voltage=3.30,
        battery_current=45.0,
    )
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['charge_current_limit_violation'] is True
    assert local_values['charge_current_limit_regular'] == pytest.approx(35, abs=0.1)
    assert local_values['charge_current_limit_final'] == pytest.approx(35, abs=0.1)


def test_discharge_violation_compensation(protector, sample_config):
    sample_config['battery_settings']['compensate_current_limit_violations'] = 1
    # max discharge 50; battery current -55 → violation 5A → power reduced by 5*52
    local_values = _controller_shaped_local_values(
        battery_soc=50,
        battery_min_cell_voltage=3.30,
        battery_current=-55.0,
        battery_voltage=52.0,
        solarcharger_power_sum=0,
    )
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['discharge_current_limit_violation'] is True
    # power regular was 50*52=2600, compensated by 5*52=260 → 2340
    assert local_values['discharge_power_limit_regular'] == pytest.approx(2340, abs=1)


# ==================== SMOOTHING ====================

def test_smoothing_snaps_stricter_discharge_limit(protector, sample_config, temp_states):
    sample_config['battery_settings']['smooth_voltage_based_(dis)charge_limits'] = 1
    temp_states['discharge_current_limit_state'] = 50

    # First cycle: discharge limit becomes stricter while discharging
    local_values = _controller_shaped_local_values(
        battery_soc=20,
        battery_min_cell_voltage=3.20,
        battery_current=-10.0,
    )
    protector.calculate_dis_charge_limits(local_values)
    # SOC 20 → regular 18; state should snap to 18
    assert temp_states['discharge_current_limit_state'] == pytest.approx(18, abs=0.1)
    assert local_values['discharge_current_limit_final'] == pytest.approx(18, abs=0.1)


def test_smoothing_holds_until_resume_voltage(protector, sample_config, temp_states):
    sample_config['battery_settings']['smooth_voltage_based_(dis)charge_limits'] = 1
    temp_states['discharge_current_limit_state'] = 0
    temp_states['discharge_current_limit_hit_zero'] = True

    # Min cell still below resume (3.25) and regular would allow more → still held at 0 by state
    local_values = _controller_shaped_local_values(
        battery_soc=50,
        battery_min_cell_voltage=3.20,  # below resume 3.25
        battery_current=-2.0,
    )
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['discharge_current_limit_final'] == pytest.approx(0, abs=0.1)

    # Min cell above resume → reset
    local_values = _controller_shaped_local_values(
        battery_soc=50,
        battery_min_cell_voltage=3.30,
        battery_current=-2.0,
    )
    protector.calculate_dis_charge_limits(local_values)
    assert temp_states['discharge_current_limit_hit_zero'] is False
    assert temp_states['discharge_current_limit_state'] == pytest.approx(50, abs=0.1)


# ==================== BOUNDARIES ====================

@pytest.mark.parametrize("max_cell,expected", [
    (3.54, 5),
    (3.55, 0),
    (3.56, 0),
])
def test_max_cell_voltage_boundaries(protector, max_cell, expected):
    local_values = _controller_shaped_local_values(
        battery_soc=60,
        battery_max_cell_voltage=max_cell,
        battery_current=0,
    )
    protector.calculate_dis_charge_limits(local_values)
    assert local_values['charge_current_limit_final'] == pytest.approx(expected, abs=0.1)


def test_safe_state_cleared_after_valid_data(protector):
    """safe_state_active must clear once valid data returns."""
    protector.calculate_dis_charge_limits({'battery_soc': 50})
    assert protector.safe_state_active is True

    local_values = _controller_shaped_local_values()
    protector.calculate_dis_charge_limits(local_values)
    assert protector.safe_state_active is False
    assert local_values['charge_current_limit_final'] > 0
