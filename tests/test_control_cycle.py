"""Control-cycle integration tests (no real Victron / Cerbo / broker needed).

These tests exercise the real ``essBATT_controller.ess_control_cycle_update``
path with:

- config / state loaded from in-memory fixtures (not disk)
- CCGX_data pre-filled as if MQTT callbacks already ran
- a MagicMock MQTT client under VictronOutput
- RepeatedTimer stubbed so no background threads fire

What this proves
----------------
Wiring and key contracts across mapper → state machine → battery protection →
setpoints → VictronOutput publishes.

What this does *not* prove
--------------------------
Real dbus-mqtt topic shapes, Cerbo keepalive behaviour, multi-device race
conditions, or live Venus firmware quirks. Those need a lab system or
recorded MQTT fixtures later.
"""

import copy
import json
from unittest.mock import MagicMock, patch

import pytest

from config_manager import ConfigManager
from essBATT_controller import essBATT_controller


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

class _DummyTimer:
    """Stand-in for RepeatedTimer: no threads, keep .interval for reload tests."""

    def __init__(self, interval, function, *args, **kwargs):
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.is_running = False

    def start(self):
        self.is_running = True

    def stop(self):
        self.is_running = False


def _integration_config(**overrides):
    """Config rich enough for protection + setpoints + static ESS Mode 2 writes."""
    cfg = {
        "config_version": 1.0,
        "vrm_id": "test_vrm_id",
        "debug_level": "DEBUG",
        "mqtt_username": "test",
        "mqtt_password": "test",
        "mqtt_server_COM_port": 1883,
        "control_update_rate": 2.0,
        "script_alive_logging_interval": 86400,
        "check_ess_config_changes_while_running": 0,
        "keepalive_get_all_topics": 0,
        "ess_mode_2_settings": {
            "grid_power_setpoint_2700": 1,
            "max_power_fed_to_loads_2704": 300,
            "max_battery_discharge_current": 50,
            "max_battery_charge_current_2705": 40,
            "max_system_grid_feed_in_power_2706": 0,
            "feed_excess_dc_coupled_pv_into_grid_2707": 0,
            "feed_excess_ac_coupled_pv_into_grid_2708": 0,
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
            "smooth_voltage_based_(dis)charge_limits": 0,
            "compensate_current_limit_violations": 0,
            "emergency_(dis)charge": {
                "use_emergency_(dis)charging": 0,
                "max_cell_voltage_for_emergency_discharge": 3.63,
                "min_cell_voltage_for_emergency_charge": 3.05,
                "emergency_(dis)charge_duration_minutes": 10,
            },
        },
        "balancing_settings": {
            "auto_balancing_settings": {"activate_auto_balancing": 0},
            "balancing_complete_condition": {
                "min_cell_voltage_threshold": 3.4,
                "max_diff_voltage_between_min_and_max_cell": 0.05,
            },
        },
        "winter_mode": {
            "use_winter_mode": 0,
            "winter_min_SOC": 25,
            "winter_restart_multis_SOC": 70,
            "winter_mode_start_date": "01.11.",
            "winter_mode_end_date": "01.03.",
            "winter_inactive_charge_min_voltage": 3.17,
            "winter_inactive_charge_time_minutes": 30,
            "auto_balancing_settings": {
                "use_different_winter_settings": 0,
                "weekday": "Sunday",
                "time": "03:00",
                "days_to_next_autobalancing": 7,
            },
        },
        "external_control_settings": {
            "allow_external_control_over_mqtt": 0,
            "date_format": "%d.%m.%Y",
            "time_format": "%H:%M",
            "mqtt_external_control_topics": {},
        },
    }
    cfg.update(overrides)
    return cfg


SETVALUE_LIST = {
    "settings_list_version": 1.0,
    "AcPowerSetPoint": "CGwacs/AcPowerSetPoint",
    "MaxDischargePower": "CGwacs/MaxDischargePower",
    "MaxFeedInPower": "CGwacs/MaxFeedInPower",
    "OvervoltageFeedIn": "CGwacs/OvervoltageFeedIn",
    "PreventFeedback": "CGwacs/PreventFeedback",
    "MaxChargeCurrent": "SystemSetup/MaxChargeCurrent",
}


def _controller_state(**overrides):
    state = {
        "ess_controller_state_version": 1.0,
        "current_state": "normal_operation",
        "time_of_last_change": "none",
        "time_of_last_completed_balancing": "none",
        "winter_mode": "not_activated",
        "winter_SOC_discharge_limit": "not_activated",
        "charge_to_SOC": {
            "activation_time": "none",
            "target_SOC": "none",
            "max_current": "none",
            "requested_current_direction": "none",
            "scheduled_start_time": "none",
        },
        "balancing": {
            "activation_time": "none",
            "max_current": "none",
            "scheduled_start_time": "none",
        },
    }
    state.update(overrides)
    return state


def _full_ccgx_data(**overrides):
    """CCGX_data as if ingestion callbacks already filled the shared dict."""
    data = {
        "grid": {"grid_power_sum": 100},
        "battery": {
            "soc": 55,
            "max_cell_voltage": 3.30,
            "min_cell_voltage": 3.20,
            "current": 3.5,
            "power": 180,
            "voltage": 52.0,
        },
        "system": {
            "L1_loads_power_consumption": 200,
            "L2_loads_power_consumption": 150,
            "L3_loads_power_consumption": 100,
        },
        # Empty solarcharger is still "complete" (no known incomplete devices)
        "solarcharger": {},
        "settings": {
            # Intentionally different from targets so VictronOutput publishes
            "AcPowerSetPoint": 999,
            "MaxChargeCurrent": 5,
            "MaxDischargePower": 100,
            "MaxFeedInPower": 5000,
            "OvervoltageFeedIn": 1,
            "PreventFeedback": 1,
        },
        "settings_base_path": "settings/0/Settings/",
        "vebus": {
            "276": {"Mode": 3},  # On
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and key in data and isinstance(data[key], dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return data


def _published_by_leaf(mock_client):
    """Map last path segment of each publish topic → payload value."""
    out = {}
    for call in mock_client.publish.call_args_list:
        topic = call.kwargs.get("topic")
        payload = call.kwargs.get("payload")
        if topic is None and call.args:
            topic = call.args[0]
        if payload is None and len(call.args) > 1:
            payload = call.args[1]
        if topic is None or payload is None:
            continue
        leaf = topic.rstrip("/").split("/")[-1]
        out[leaf] = json.loads(payload)["value"]
    return out


@pytest.fixture
def cycle_controller():
    """Real controller wired for one manual control-cycle call."""
    config = _integration_config()
    state = _controller_state()
    setvalue = copy.deepcopy(SETVALUE_LIST)
    logger = MagicMock()

    def load_config(self):
        self.config_data_loaded_correctly = True
        return copy.deepcopy(config)

    def load_setvalue_list(self):
        self.setvalue_list_loaded_correctly = True
        return copy.deepcopy(setvalue)

    def load_state(self):
        self.controller_state_loaded_correctly = True
        return copy.deepcopy(state)

    with patch("essBATT_controller.RepeatedTimer", _DummyTimer), \
         patch.object(ConfigManager, "load_config", load_config), \
         patch.object(ConfigManager, "load_setvalue_list", load_setvalue_list), \
         patch.object(ConfigManager, "load_state", load_state), \
         patch.object(ConfigManager, "save_state", return_value=True):
        ctrl = essBATT_controller(logger)
        assert ctrl.ess_config_data_loaded_correctly is True

        mock_client = MagicMock()
        mock_client.publish.return_value = (0, 1)
        ctrl.victron_output.set_mqtt_client(mock_client)
        ctrl.mqtt_bridge.connection_ok = True
        ctrl.mqtt_bridge.disconnected = False

        # Replace shared CCGX_data contents (same object refs used by modules)
        ctrl.CCGX_data.clear()
        ctrl.CCGX_data.update(_full_ccgx_data())

        yield ctrl, mock_client
        ctrl.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cycle_skips_when_mqtt_disconnected(cycle_controller):
    ctrl, mock_client = cycle_controller
    ctrl.mqtt_bridge.connection_ok = False

    ctrl.ess_control_cycle_update()

    mock_client.publish.assert_not_called()


def test_cycle_happy_path_publishes_limits_and_setpoint(cycle_controller):
    """Full CCGX snapshot → protection + normal setpoint → MQTT publishes."""
    ctrl, mock_client = cycle_controller

    ctrl.ess_control_cycle_update()

    published = _published_by_leaf(mock_client)

    # Static ESS Mode 2 settings from config (0 / 0 / 0)
    assert published["MaxFeedInPower"] == 0
    assert published["OvervoltageFeedIn"] == 0
    assert published["PreventFeedback"] == 0

    # Normal operation AcPowerSetPoint from grid_power_setpoint_2700
    assert published["AcPowerSetPoint"] == 1

    # Mid-SOC, mid-cell voltages → full configured charge/discharge headroom
    assert published["MaxChargeCurrent"] == pytest.approx(40, abs=0.01)
    # Discharge power = min(current limits) * voltage (int)
    assert published["MaxDischargePower"] == int(50 * 52.0)

    # Multis stay On (mode 3) when charge & discharge allowed → no Mode publish
    assert "Mode" not in published


def test_cycle_incomplete_ccgx_skips_dynamic_setpoints(cycle_controller):
    """Missing required battery field → static settings only, no limit writes."""
    ctrl, mock_client = cycle_controller
    del ctrl.CCGX_data["battery"]["soc"]

    ctrl.ess_control_cycle_update()

    published = _published_by_leaf(mock_client)

    # Static path still runs
    assert published["MaxFeedInPower"] == 0
    # Dynamic path skipped — no charge/discharge/setpoint publish
    assert "MaxChargeCurrent" not in published
    assert "MaxDischargePower" not in published
    assert "AcPowerSetPoint" not in published


def test_cycle_external_deactivate_charge_forces_zero_charge(cycle_controller):
    """External deactivate_charge flag is applied in the cycle and published."""
    ctrl, mock_client = cycle_controller
    ctrl.ess_external_input["deactivate_charge"] = {
        "activated": True,
        "receive_time": None,
    }

    ctrl.ess_control_cycle_update()

    published = _published_by_leaf(mock_client)
    assert published["MaxChargeCurrent"] == 0
    # Charge limit 0 → multis switch to inverter-only (2)
    assert published["Mode"] == 2


def test_cycle_external_deactivate_discharge_forces_zero_discharge(cycle_controller):
    ctrl, mock_client = cycle_controller
    ctrl.ess_external_input["deactivate_discharge"] = {
        "activated": True,
        "receive_time": None,
    }

    ctrl.ess_control_cycle_update()

    published = _published_by_leaf(mock_client)
    assert published["MaxDischargePower"] == 0


def test_cycle_unchanged_settings_are_not_republished(cycle_controller):
    """only_set_if_deviation_to_current_setting suppresses redundant publishes."""
    ctrl, mock_client = cycle_controller
    # Align settings with what the cycle will compute for this snapshot
    ctrl.CCGX_data["settings"]["AcPowerSetPoint"] = 1
    ctrl.CCGX_data["settings"]["MaxChargeCurrent"] = 40
    ctrl.CCGX_data["settings"]["MaxDischargePower"] = int(50 * 52.0)
    ctrl.CCGX_data["settings"]["MaxFeedInPower"] = 0
    ctrl.CCGX_data["settings"]["OvervoltageFeedIn"] = 0
    ctrl.CCGX_data["settings"]["PreventFeedback"] = 0

    ctrl.ess_control_cycle_update()

    mock_client.publish.assert_not_called()


def test_cycle_high_cell_voltage_reduces_charge_current(cycle_controller):
    """Protection path is actually on the cycle (not only unit-tested in isolation)."""
    ctrl, mock_client = cycle_controller
    # At/above max cell charging threshold → charge current should collapse
    ctrl.CCGX_data["battery"]["max_cell_voltage"] = 3.56

    ctrl.ess_control_cycle_update()

    published = _published_by_leaf(mock_client)
    assert "MaxChargeCurrent" in published
    assert published["MaxChargeCurrent"] == pytest.approx(0.0, abs=0.01)


def test_cycle_persists_state_when_state_machine_changes_it(cycle_controller):
    """cleanup_after_control_loop saves when in-memory state diverges from snapshot."""
    ctrl, mock_client = cycle_controller

    with patch.object(ctrl.config_manager, "save_state_if_changed", return_value=True) as save:
        # Force a visible state change the cleanup can see
        ctrl.ess_controller_state["current_state"] = "normal_operation"
        ctrl._ess_controller_state_snapshot = copy.deepcopy(ctrl.ess_controller_state)
        ctrl.ess_controller_state["time_of_last_change"] = "forced-for-test"

        ctrl.ess_control_cycle_update()

        save.assert_called_once()
        # Snapshot refreshed after save path
        assert ctrl._ess_controller_state_snapshot["time_of_last_change"] == "forced-for-test"
