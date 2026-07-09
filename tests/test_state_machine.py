"""Tests for the StateMachine (Step 4 of modularization).

We start with basic tests for the public interface and state transition helpers.
This allows us to drive the implementation of the StateMachine.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta

from state_machine import StateMachine


@pytest.fixture
def sample_controller_state():
    """Minimal controller state for state machine tests."""
    return {
        "current_state": "normal_operation",
        "time_of_last_change": "01-01-2025 (12:00:00.000)",
        "balancing": {
            "activation_time": "none",
            "max_current": "none",
            "scheduled_start_time": "none",
        },
        "charge_to_SOC": {
            "activation_time": "none",
            "target_SOC": "none",
            "max_current": "none",
            "scheduled_start_time": "none",
            "requested_current_direction": "none",
        },
        "winter_mode": "not_activated",
        "winter_SOC_discharge_limit": "not_activated",
        "time_of_last_completed_balancing": "none",
    }


@pytest.fixture
def sample_temporary_states():
    """Temporary script states used by state machine."""
    return {
        "multi_switch_min_soc_debounce_time": None,
        "winter_mode_multis_switch_off_time": None,
        "winter_mode_inactive_charge_begin_time": None,
        "emergency_(dis)charge_begin_time": None,
        "winter_mode_charge_begin_time": None,
        "discharge_current_limit_state": 50.0,
        "discharge_current_limit_hit_zero": False,
        "charge_current_limit_state": 40.0,
        "charge_current_limit_hit_zero": False,
    }


@pytest.fixture
def sample_external_input():
    """Empty external input dict (will be mutated by tests)."""
    return {}


@pytest.fixture
def state_machine(mocked_logger, sample_config, sample_controller_state, sample_temporary_states, sample_external_input):
    """Fully wired StateMachine instance for testing."""
    return StateMachine(
        config=sample_config,
        logger=mocked_logger,
        controller_state=sample_controller_state,
        temporary_script_states=sample_temporary_states,
        external_input=sample_external_input,
    )


@pytest.fixture
def state_machine_with_external(mocked_logger, sample_config, sample_controller_state, sample_temporary_states):
    """StateMachine with its own mutable external_input for external handling tests."""
    ext = {}
    return StateMachine(
        config=sample_config,
        logger=mocked_logger,
        controller_state=sample_controller_state,
        temporary_script_states=sample_temporary_states,
        external_input=ext,
    ), ext


def test_state_machine_initialization(state_machine):
    """StateMachine can be instantiated with the required dependencies."""
    assert state_machine is not None
    assert hasattr(state_machine, "update")
    assert hasattr(state_machine, "do_state_update")


def test_do_state_update_changes_current_state(state_machine, sample_controller_state):
    """do_state_update should correctly transition the current_state."""
    # Ensure clean starting state for this test
    sample_controller_state["current_state"] = "normal_operation"
    sample_controller_state["time_of_last_change"] = "01-01-2025 (00:00:00.000000)"

    initial_time = sample_controller_state["time_of_last_change"]

    state_machine.do_state_update("balancing")

    assert sample_controller_state["current_state"] == "balancing"
    assert sample_controller_state["balancing"]["activation_time"] != "none"
    assert sample_controller_state["time_of_last_change"] != initial_time


def test_do_state_update_to_normal_resets_other_states(state_machine, sample_controller_state):
    """Going back to normal_operation should reset balancing and charge_to_SOC data."""
    # First go to balancing
    state_machine.do_state_update("balancing")
    assert sample_controller_state["balancing"]["activation_time"] != "none"

    # Then go back to normal
    state_machine.do_state_update("normal_operation")

    assert sample_controller_state["current_state"] == "normal_operation"
    assert sample_controller_state["balancing"]["activation_time"] == "none"
    assert sample_controller_state["charge_to_SOC"]["activation_time"] == "none"


def test_activate_charge_to_soc_from_script(state_machine, sample_controller_state):
    """activate_charge_to_SOC_from_script should set the right fields and change state."""
    state_machine.activate_charge_to_SOC_from_script(
        target_soc=85,
        max_current=25,
        current_direction="charge"
    )

    assert sample_controller_state["current_state"] == "charge_to_SOC"
    assert sample_controller_state["charge_to_SOC"]["target_SOC"] == 85
    assert sample_controller_state["charge_to_SOC"]["max_current"] == 25
    assert sample_controller_state["charge_to_SOC"]["requested_current_direction"] == "charge"


def test_activate_charge_to_soc_clips_values(state_machine, sample_controller_state):
    """Target SOC should be clipped to valid range [0.1, 99.9]."""
    state_machine.activate_charge_to_SOC_from_script(target_soc=150)
    assert sample_controller_state["charge_to_SOC"]["target_SOC"] == 99.9

    state_machine.activate_charge_to_SOC_from_script(target_soc=-5)
    assert sample_controller_state["charge_to_SOC"]["target_SOC"] == 0.1


def test_reset_single_state_data(state_machine, sample_controller_state):
    """reset_single_state_data should clear the relevant state data."""
    # Put some data in balancing
    sample_controller_state["balancing"]["activation_time"] = "something"
    sample_controller_state["balancing"]["max_current"] = 30

    state_machine.reset_single_state_data("balancing")

    assert sample_controller_state["balancing"]["activation_time"] == "none"
    assert sample_controller_state["balancing"]["max_current"] == "none"


def test_reset_winter_mode_states(state_machine, sample_temporary_states):
    """reset_winter_mode_states should clear winter-related temporary timers."""
    sample_temporary_states["winter_mode_multis_switch_off_time"] = datetime.now()
    sample_temporary_states["winter_mode_inactive_charge_begin_time"] = datetime.now()

    state_machine.reset_winter_mode_states()

    assert sample_temporary_states["winter_mode_multis_switch_off_time"] is None
    assert sample_temporary_states["winter_mode_inactive_charge_begin_time"] is None


def test_update_does_not_crash(state_machine, mocked_logger):
    """Calling update should not raise even with minimal data."""
    local_values = {
        "all_CCGX_values_available": True,
        "battery_soc": 50,
        "battery_min_cell_voltage": 3.2,
        "battery_max_cell_voltage": 3.4,
    }

    # Should not raise
    state_machine.update(local_values)
    # Basic smoke test passed


def test_multis_switch_handling_sets_position(state_machine, sample_temporary_states, sample_controller_state):
    """multis_switch_handling should compute and set a switch position based on limits."""
    local_values = {
        "charge_current_limit_final": 10.0,
        "discharge_power_limit_final": 1000,
        "solarcharger_power_sum": 50,
        "discharge_current_limit_regular": 20.0,
    }
    # Start in normal
    sample_controller_state["current_state"] = "normal_operation"

    state_machine.multis_switch_handling(local_values)

    assert "multis_switch_position" in local_values
    # With positive limits and solar < discharge*1.2, should stay at default On (3) or adjusted by logic
    assert local_values["multis_switch_position"] in (1, 2, 3, 4)


def test_multis_switch_handling_zero_charge_prefers_inverter_only(state_machine, sample_controller_state):
    """When charge final is ~0, prefers charger deactivated (2 = Inverter only)."""
    local_values = {
        "charge_current_limit_final": 0.0,
        "discharge_power_limit_final": 500,
        "solarcharger_power_sum": 10,
        "discharge_current_limit_regular": 10.0,
    }
    sample_controller_state["current_state"] = "normal_operation"

    state_machine.multis_switch_handling(local_values)

    # Expect 2 (inverter only) as highest
    assert local_values["multis_switch_position"] == 2


def test_multis_switch_handling_winter_forces_off(state_machine, sample_controller_state):
    """Winter activated + SOC limit forces switch to Off (4)."""
    local_values = {
        "charge_current_limit_final": 20.0,
        "discharge_power_limit_final": 800,
        "solarcharger_power_sum": 0,
        "discharge_current_limit_regular": 15.0,
    }
    sample_controller_state["current_state"] = "normal_operation"
    sample_controller_state["winter_mode"] = "activated"
    sample_controller_state["winter_SOC_discharge_limit"] = "activated"

    state_machine.multis_switch_handling(local_values)

    assert local_values["multis_switch_position"] == 4


def test_multis_switch_handling_balancing_forces_charger_only(state_machine, sample_controller_state):
    """Balancing state forces charger-only (1)."""
    local_values = {
        "charge_current_limit_final": 20.0,
        "discharge_power_limit_final": 800,
        "solarcharger_power_sum": 0,
        "discharge_current_limit_regular": 15.0,
    }
    sample_controller_state["current_state"] = "balancing"

    state_machine.multis_switch_handling(local_values)

    assert local_values["multis_switch_position"] == 1


# ------------------------------------------------------------------
# Edge case tests for external input, scheduling, winter, emergency, transitions
# ------------------------------------------------------------------

def test_external_control_disabled_no_op(state_machine, sample_config, sample_controller_state, mocked_logger):
    """When allow_external... == 0, _handle_external_input does nothing."""
    sample_config["external_control_settings"]["allow_external_control_over_mqtt"] = 0
    local = {"all_CCGX_values_available": True}
    # Should not raise or change state
    state_machine.update(local)
    assert sample_controller_state["current_state"] == "normal_operation"


def test_external_input_priority_and_copy(state_machine_with_external, sample_controller_state, sample_config):
    """Latest receive_time wins; new_data_received triggers copy and sets external_receive_info."""
    sm, ext = state_machine_with_external
    sample_config["external_control_settings"]["allow_external_control_over_mqtt"] = 1
    now = datetime.now(tz=None)

    # Simulate two external commands, balancing more recent
    ext["balancing"] = {"receive_time": now, "activated": "1", "current_limit_input": 30}
    ext["charge_to_SOC"] = {"receive_time": now - timedelta(seconds=10), "activated": "1", "target_SOC": 90}
    ext["new_data_received"] = True

    local = {
        "all_CCGX_values_available": True,
        "battery_soc": 45,
        "battery_min_cell_voltage": 3.3,
        "battery_max_cell_voltage": 3.4,
    }
    sm.update(local)

    # Should have processed balancing (latest)
    assert "external_receive_info" in local
    assert local["external_receive_info"].get("target_state") == "balancing"
    assert sample_controller_state["balancing"]["max_current"] == 30
    assert ext.get("new_data_received") is False  # cleared


def test_external_deactivate_via_receive_info(state_machine_with_external, sample_controller_state, sample_config):
    """activated=False via external path leads to normal_operation."""
    sm, ext = state_machine_with_external
    sample_config["external_control_settings"]["allow_external_control_over_mqtt"] = 1

    # First activate
    ext["charge_to_SOC"] = {"receive_time": datetime.now(tz=None), "activated": "1", "target_SOC": 60}
    ext["new_data_received"] = True
    local = {"battery_soc": 50}
    sm.update(local)
    assert sample_controller_state["current_state"] == "charge_to_SOC"

    # Now send deactivate
    ext["charge_to_SOC"] = {"receive_time": datetime.now(tz=None), "activated": "0"}
    ext["new_data_received"] = True
    sm.update(local)
    assert sample_controller_state["current_state"] == "normal_operation"


def test_scheduled_start_time_reached(state_machine, sample_controller_state, sample_config):
    """If scheduled_start_time in past, the scheduled check in external handler transitions."""
    sample_config["external_control_settings"]["allow_external_control_over_mqtt"] = 1
    # Provide a receive_time for some external so we don't early-return before scheduled for-loop
    state_machine.ess_external_input["balancing"] = {"receive_time": datetime.now(tz=None) - timedelta(hours=1)}
    state_machine.ess_external_input["new_data_received"] = False

    # Prepare a past scheduled time in controller state
    past = (datetime.now(tz=None) - timedelta(minutes=5)).strftime(
        sample_config["external_control_settings"]["date_format"] + " " +
        sample_config["external_control_settings"]["time_format"]
    )
    sample_controller_state["charge_to_SOC"]["scheduled_start_time"] = past
    sample_controller_state["charge_to_SOC"]["activation_time"] = "none"

    local = {}
    state_machine._handle_external_input(local)

    assert sample_controller_state["current_state"] == "charge_to_SOC"


def test_auto_balancing_triggers(state_machine, sample_controller_state, sample_config):
    """Auto balancing condition (old last + weekday + time) triggers balancing."""
    # Enable
    sample_config["balancing_settings"]["auto_balancing_settings"]["activate_auto_balancing"] = 1
    sample_config["balancing_settings"]["auto_balancing_settings"]["weekday"] = datetime.now().strftime("%A")
    sample_config["balancing_settings"]["auto_balancing_settings"]["time"] = (datetime.now() - timedelta(minutes=1)).strftime("%H:%M")
    sample_config["balancing_settings"]["auto_balancing_settings"]["days_to_next_autobalancing"] = 0

    # Last balancing long ago
    long_ago = (datetime.now(tz=None) - timedelta(days=10)).strftime("%d-%b-%Y (%H:%M:%S.%f)")
    sample_controller_state["time_of_last_completed_balancing"] = long_ago

    local = {}
    state_machine._handle_auto_balancing()

    assert sample_controller_state["current_state"] == "balancing"


def test_winter_mode_activates_and_sets_discharge_limit(state_machine, sample_controller_state, sample_config, sample_temporary_states):
    """Winter dates covering 'now' + low SOC activates winter + discharge limit."""
    sample_config["winter_mode"]["use_winter_mode"] = 1
    # Choose dates that include today (robust)
    today = datetime.now(tz=None)
    start = (today - timedelta(days=1)).strftime("%d.%m.")
    end = (today + timedelta(days=1)).strftime("%d.%m.")
    sample_config["winter_mode"]["winter_mode_start_date"] = start
    sample_config["winter_mode"]["winter_mode_end_date"] = end
    sample_config["winter_mode"]["winter_min_SOC"] = 40
    sample_config["winter_mode"]["winter_restart_multis_SOC"] = 50

    local = {"battery_soc": 30}

    state_machine._handle_winter_mode(local)

    assert sample_controller_state["winter_mode"] == "activated"
    assert sample_controller_state["winter_SOC_discharge_limit"] == "activated"
    assert "winter_mode_multis_switch_off_time" in sample_temporary_states  # still present


def test_winter_mode_deactivates_on_high_soc(state_machine, sample_controller_state, sample_config):
    """High SOC while winter active deactivates the discharge limit (date window covering now)."""
    sample_config["winter_mode"]["use_winter_mode"] = 1
    today = datetime.now(tz=None)
    start = (today - timedelta(days=1)).strftime("%d.%m.")
    end = (today + timedelta(days=1)).strftime("%d.%m.")
    sample_config["winter_mode"]["winter_mode_start_date"] = start
    sample_config["winter_mode"]["winter_mode_end_date"] = end
    sample_controller_state["winter_mode"] = "activated"
    sample_controller_state["winter_SOC_discharge_limit"] = "activated"
    sample_config["winter_mode"]["winter_min_SOC"] = 20
    sample_config["winter_mode"]["winter_restart_multis_SOC"] = 80

    local = {"battery_soc": 85}
    state_machine._handle_winter_mode(local)

    assert sample_controller_state["winter_SOC_discharge_limit"] == "not_activated"


def test_winter_inactive_charge_starts_after_debounce(
    state_machine, sample_controller_state, sample_config, sample_temporary_states
):
    """After Multis off long enough and min cell low, winter inactive charge starts."""
    sample_config["winter_mode"]["use_winter_mode"] = 1
    today = datetime.now(tz=None)
    sample_config["winter_mode"]["winter_mode_start_date"] = (today - timedelta(days=1)).strftime("%d.%m.")
    sample_config["winter_mode"]["winter_mode_end_date"] = (today + timedelta(days=1)).strftime("%d.%m.")
    sample_config["winter_mode"]["winter_inactive_charge_min_voltage"] = 3.17
    sample_config["winter_mode"]["winter_inactive_charge_time_minutes"] = 30
    sample_config["winter_mode"]["winter_min_SOC"] = 25
    sample_config["winter_mode"]["winter_restart_multis_SOC"] = 70

    sample_controller_state["winter_mode"] = "activated"
    sample_controller_state["winter_SOC_discharge_limit"] = "activated"
    # Multis have been off for > 10 minutes
    sample_temporary_states["winter_mode_multis_switch_off_time"] = today - timedelta(minutes=15)
    sample_temporary_states["winter_mode_inactive_charge_begin_time"] = None

    local = {
        "battery_soc": 20,
        "all_CCGX_values_available": True,
        "battery_min_cell_voltage": 3.15,
        "battery_max_cell_voltage": 3.30,
    }
    state_machine._handle_winter_mode(local)

    assert sample_controller_state["current_state"] == "charge_to_SOC"
    assert sample_controller_state["charge_to_SOC"]["target_SOC"] == 80
    assert sample_controller_state["charge_to_SOC"]["max_current"] == 20
    assert sample_temporary_states["winter_mode_inactive_charge_begin_time"] is not None


def test_winter_inactive_charge_ends_after_duration(
    state_machine, sample_controller_state, sample_config, sample_temporary_states
):
    """Winter inactive charge returns to normal after configured duration."""
    sample_config["winter_mode"]["use_winter_mode"] = 1
    today = datetime.now(tz=None)
    sample_config["winter_mode"]["winter_mode_start_date"] = (today - timedelta(days=1)).strftime("%d.%m.")
    sample_config["winter_mode"]["winter_mode_end_date"] = (today + timedelta(days=1)).strftime("%d.%m.")
    sample_config["winter_mode"]["winter_inactive_charge_min_voltage"] = 3.17
    sample_config["winter_mode"]["winter_inactive_charge_time_minutes"] = 0.01  # ~0.6s
    sample_config["winter_mode"]["winter_min_SOC"] = 25
    sample_config["winter_mode"]["winter_restart_multis_SOC"] = 70

    sample_controller_state["winter_mode"] = "activated"
    sample_controller_state["winter_SOC_discharge_limit"] = "activated"
    sample_controller_state["current_state"] = "charge_to_SOC"
    sample_temporary_states["winter_mode_multis_switch_off_time"] = today - timedelta(minutes=20)
    sample_temporary_states["winter_mode_inactive_charge_begin_time"] = today - timedelta(seconds=2)

    local = {
        "battery_soc": 20,
        "all_CCGX_values_available": True,
        "battery_min_cell_voltage": 3.20,
        "battery_max_cell_voltage": 3.30,
    }
    state_machine._handle_winter_mode(local)

    assert sample_controller_state["current_state"] == "normal_operation"
    assert sample_temporary_states["winter_mode_inactive_charge_begin_time"] is None


def test_emergency_charge_starts_and_expires(state_machine, sample_controller_state, sample_temporary_states, sample_config):
    """Low min_cell starts emergency charge_to_SOC; after duration returns to normal."""
    sample_config["battery_settings"]["emergency_(dis)charge"]["use_emergency_(dis)charging"] = 1
    sample_config["battery_settings"]["emergency_(dis)charge"]["min_cell_voltage_for_emergency_charge"] = 3.0
    sample_config["battery_settings"]["emergency_(dis)charge"]["emergency_(dis)charge_duration_minutes"] = 0.01  # ~0.6s

    local = {
        "all_CCGX_values_available": True,
        "battery_min_cell_voltage": 2.7,
        "battery_max_cell_voltage": 3.3,
    }

    state_machine._handle_emergency(local)
    assert sample_controller_state["current_state"] == "charge_to_SOC"
    assert sample_controller_state["charge_to_SOC"]["target_SOC"] == 80
    assert sample_temporary_states["emergency_(dis)charge_begin_time"] is not None

    # Wait out the short duration
    import time
    time.sleep(0.7)

    # Call again to expire
    state_machine._handle_emergency(local)
    assert sample_controller_state["current_state"] == "normal_operation"
    assert sample_temporary_states["emergency_(dis)charge_begin_time"] is None


def test_balancing_complete_condition(state_machine, sample_controller_state):
    """When in balancing and cells good, _check_transition_back_to_normal goes to normal + records time."""
    sample_controller_state["current_state"] = "balancing"
    local = {
        "all_CCGX_values_available": True,
        "battery_min_cell_voltage": 3.45,
        "battery_max_cell_voltage": 3.46,
    }
    # Config threshold is 3.4 / 0.05 from sample_config

    state_machine._check_transition_back_to_normal(local)

    assert sample_controller_state["current_state"] == "normal_operation"
    assert sample_controller_state["time_of_last_completed_balancing"] != "none"


def test_charge_to_soc_target_reached(state_machine, sample_controller_state):
    """charge_to_SOC with SOC reached or target crossed goes back to normal."""
    sample_controller_state["current_state"] = "charge_to_SOC"
    sample_controller_state["charge_to_SOC"]["requested_current_direction"] = "charge"
    sample_controller_state["charge_to_SOC"]["target_SOC"] = 70

    local = {"all_CCGX_values_available": True, "battery_soc": 72}
    state_machine._check_transition_back_to_normal(local)
    assert sample_controller_state["current_state"] == "normal_operation"

    # Discharge case
    sample_controller_state["current_state"] = "charge_to_SOC"
    sample_controller_state["charge_to_SOC"]["requested_current_direction"] = "discharge"
    sample_controller_state["charge_to_SOC"]["target_SOC"] = 30
    local["battery_soc"] = 25
    state_machine._check_transition_back_to_normal(local)
    assert sample_controller_state["current_state"] == "normal_operation"


@pytest.mark.parametrize("timestr,datestr,expected_type", [
    ("-", "-", type(None)),
    ("12:00", "-", datetime),
    ("-", "01.01.2026", datetime),
    ("12:30", "01.01.2026", datetime),
])
def test_datetime_obj_from_input_timestamp_variants(state_machine, sample_config, timestr, datestr, expected_type):
    """Various combinations of - / time / date produce correct return types."""
    result = state_machine.datetime_obj_from_input_timestamp(timestr, datestr)
    if expected_type is type(None):
        assert result is None
    else:
        assert isinstance(result, datetime)


def test_multis_discharge_limit_below_solar_forces_charger_only(state_machine, sample_controller_state):
    """discharge_power_final low relative to solar → switch to charger only (1)."""
    local = {
        "charge_current_limit_final": 20.0,
        "discharge_power_limit_final": 10,   # very low
        "solarcharger_power_sum": 100,
        "discharge_current_limit_regular": 5.0,
    }
    sample_controller_state["current_state"] = "normal_operation"

    state_machine.multis_switch_handling(local)
    assert local["multis_switch_position"] == 1


def test_multis_both_limits_zero_and_winter_off(state_machine, sample_controller_state):
    """Charge 0 + discharge_power 0 → 4 (Off)."""
    local = {
        "charge_current_limit_final": 0.0,
        "discharge_power_limit_final": 0,
        "solarcharger_power_sum": 0,
        "discharge_current_limit_regular": 0.0,
    }
    sample_controller_state["current_state"] = "normal_operation"

    state_machine.multis_switch_handling(local)
    assert local["multis_switch_position"] == 4


def test_charge_to_soc_discharge_direction_forces_on(state_machine, sample_controller_state):
    """charge_to_SOC discharge direction forces 3 (On) per original comment."""
    local = {
        "charge_current_limit_final": 0.0,
        "discharge_power_limit_final": 0,
        "solarcharger_power_sum": 0,
        "discharge_current_limit_regular": 0.0,
    }
    sample_controller_state["current_state"] = "charge_to_SOC"
    sample_controller_state["charge_to_SOC"]["requested_current_direction"] = "discharge"

    state_machine.multis_switch_handling(local)
    assert local["multis_switch_position"] == 3
