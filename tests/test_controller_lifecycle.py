"""Lifecycle / plumbing tests for essBATT_controller.

Targets lines that control-cycle happy-path tests do not hit:
init early exits, run() error paths, signal handlers, reload, keepalive
guards, safe-state edge cases, timers, reboot stub.
"""

import copy
import signal
from unittest.mock import MagicMock, patch

import pytest

from config_manager import ConfigManager
from essBATT_controller import essBATT_controller


class _DummyTimer:
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


def _base_config(**overrides):
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
        "controller_heartbeat_topic": "essbatt/controller/heartbeat",
        "controller_heartbeat_interval_s": 30.0,
        "ess_mode_2_settings": {
            "grid_power_setpoint_2700": 1,
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


_SETVALUE = {
    "AcPowerSetPoint": "CGwacs/AcPowerSetPoint",
    "MaxDischargePower": "CGwacs/MaxDischargePower",
    "MaxFeedInPower": "CGwacs/MaxFeedInPower",
    "OvervoltageFeedIn": "CGwacs/OvervoltageFeedIn",
    "PreventFeedback": "CGwacs/PreventFeedback",
    "MaxChargeCurrent": "SystemSetup/MaxChargeCurrent",
}

_STATE = {
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


def _build_controller(
    *,
    config=None,
    setvalue=None,
    state=None,
    config_ok=True,
    setvalue_ok=True,
    state_ok=True,
):
    """Construct controller with patched ConfigManager + DummyTimer."""
    config = copy.deepcopy(config if config is not None else _base_config())
    setvalue = copy.deepcopy(setvalue if setvalue is not None else _SETVALUE)
    state = copy.deepcopy(state if state is not None else _STATE)
    logger = MagicMock()

    def load_config(self):
        self.config_data_loaded_correctly = config_ok
        return copy.deepcopy(config) if config_ok else {}

    def load_setvalue_list(self):
        self.setvalue_list_loaded_correctly = setvalue_ok
        return copy.deepcopy(setvalue) if setvalue_ok else {}

    def load_state(self):
        self.controller_state_loaded_correctly = state_ok
        return copy.deepcopy(state) if state_ok else {}

    with patch("essBATT_controller.RepeatedTimer", _DummyTimer), \
         patch.object(ConfigManager, "load_config", load_config), \
         patch.object(ConfigManager, "load_setvalue_list", load_setvalue_list), \
         patch.object(ConfigManager, "load_state", load_state), \
         patch.object(ConfigManager, "save_state", return_value=True):
        ctrl = essBATT_controller(logger)
    return ctrl, logger


# --- __init__ early exits / fallbacks ---

def test_init_stops_when_config_load_fails():
    ctrl, _ = _build_controller(config_ok=False)
    assert ctrl.ess_config_data_loaded_correctly is False
    assert not hasattr(ctrl, "mqtt_bridge") or getattr(ctrl, "mqtt_bridge", None) is None or \
        not ctrl.ess_config_data_loaded_correctly
    # Early return before modules: no battery_protector
    assert not hasattr(ctrl, "battery_protector")


def test_init_stops_when_setvalue_load_fails():
    ctrl, _ = _build_controller(setvalue_ok=False)
    assert ctrl.ess_config_data_loaded_correctly is True
    assert ctrl.ess_setvalue_list_loaded_correctly is False
    assert not hasattr(ctrl, "battery_protector")


def test_init_state_load_failure_uses_empty_dict():
    ctrl, _ = _build_controller(state_ok=False)
    assert ctrl.ess_controller_state_loaded_correctly is False
    assert ctrl.ess_controller_state == {}
    assert hasattr(ctrl, "battery_protector")


# --- run() failure paths ---

def test_run_oserror_sets_not_running():
    ctrl, logger = _build_controller()
    with patch.object(ctrl, "_install_signal_handlers"), \
         patch.object(ctrl.mqtt_bridge, "start", side_effect=OSError("refused")):
        ctrl.run()
    assert ctrl._running is False
    logger.error.assert_called()
    assert "network/OS" in logger.error.call_args.args[0]


def test_run_timeout_error():
    ctrl, logger = _build_controller()
    with patch.object(ctrl, "_install_signal_handlers"), \
         patch.object(ctrl.mqtt_bridge, "start", side_effect=TimeoutError("slow")):
        ctrl.run()
    assert ctrl._running is False
    assert "timeout" in logger.error.call_args.args[0]


def test_run_unexpected_exception():
    ctrl, logger = _build_controller()
    with patch.object(ctrl, "_install_signal_handlers"), \
         patch.object(ctrl.mqtt_bridge, "start", side_effect=RuntimeError("boom")):
        ctrl.run()
    assert ctrl._running is False
    logger.exception.assert_called()


# --- stop() timer errors ---

def test_stop_continues_if_timer_stop_raises():
    ctrl, logger = _build_controller()
    bad = MagicMock()
    bad.stop.side_effect = RuntimeError("timer dead")
    ctrl.rt_keep_alive_obj = bad
    ctrl.rt_ess_control_update_obj = None
    ctrl.rt_print_status_obj = None
    ctrl.mqtt_bridge.stop = MagicMock()
    ctrl.stop()
    logger.exception.assert_called()
    ctrl.mqtt_bridge.stop.assert_called_once()


# --- signal handlers ---

def test_install_signal_handlers_sets_running_false():
    ctrl, logger = _build_controller()
    ctrl._running = True
    handlers = {}

    def fake_signal(sig, handler):
        handlers[sig] = handler

    with patch("essBATT_controller.signal.signal", side_effect=fake_signal):
        ctrl._install_signal_handlers()

    assert signal.SIGTERM in handlers
    assert signal.SIGINT in handlers
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert ctrl._running is False
    logger.warning.assert_called()

    # Invalid signum → name falls back to str(signum)
    ctrl._running = True
    handlers[signal.SIGINT](99999, None)
    assert ctrl._running is False


def test_install_signal_handlers_tolerates_install_failure():
    ctrl, logger = _build_controller()

    def boom(sig, handler):
        raise ValueError("not main thread")

    with patch("essBATT_controller.signal.signal", side_effect=boom):
        ctrl._install_signal_handlers()  # must not raise
    logger.debug.assert_called()


# --- timers / mqtt connected ---

def test_start_timers_idempotent():
    ctrl, _ = _build_controller()
    with patch("essBATT_controller.RepeatedTimer", _DummyTimer):
        ctrl._start_timers()
        t1 = ctrl.rt_ess_control_update_obj
        ctrl._start_timers()
        assert ctrl.rt_ess_control_update_obj is t1


def test_on_mqtt_connected_reconnect_log():
    ctrl, logger = _build_controller()
    ctrl.mqtt_bridge.client = MagicMock()
    ctrl.mqtt_bridge.connection_ok = True
    with patch("essBATT_controller.RepeatedTimer", _DummyTimer), \
         patch.object(ctrl, "send_keepalive_to_cerbo"):
        ctrl._on_mqtt_connected(is_reconnect=True)
    assert any(
        "restored" in str(c.args[0]).lower()
        for c in logger.info.call_args_list
    )


# --- disconnect warn throttle ---

def test_disconnect_warn_throttled_on_second_call():
    ctrl, logger = _build_controller()
    ctrl.mqtt_bridge.connection_ok = False
    with patch.object(ctrl.mqtt_bridge, "disconnect_duration_s", return_value=45.0), \
         patch("essBATT_controller.time.time", return_value=1000.0):
        ctrl._last_disconnect_warn_at = None
        ctrl._maybe_warn_long_disconnect()
        assert logger.warning.called
        logger.warning.reset_mock()
        # Same time → still within interval → no second warn
        ctrl._maybe_warn_long_disconnect()
        logger.warning.assert_not_called()


def test_disconnect_warn_skipped_when_short():
    ctrl, logger = _build_controller()
    ctrl.mqtt_bridge.connection_ok = False
    with patch.object(ctrl.mqtt_bridge, "disconnect_duration_s", return_value=5.0):
        ctrl._maybe_warn_long_disconnect()
    logger.warning.assert_not_called()


# --- software safe state edge cases ---

def test_safe_state_when_mqtt_down():
    ctrl, logger = _build_controller()
    ctrl.mqtt_bridge.connection_ok = False
    ctrl._apply_software_safe_state(reason="test")
    assert "MQTT is down" in logger.error.call_args.args[0]


def test_safe_state_publish_exception_is_logged():
    ctrl, logger = _build_controller()
    ctrl.mqtt_bridge.connection_ok = True
    with patch.object(
        ctrl.victron_output, "set_ccgx_value", side_effect=RuntimeError("pub fail")
    ):
        ctrl._apply_software_safe_state(reason="test")
    logger.exception.assert_called()


# --- keepalive / alive status ---

def test_keepalive_skipped_when_disconnected():
    ctrl, _ = _build_controller()
    ctrl.mqtt_bridge.connection_ok = False
    with patch.object(ctrl.victron_output, "send_keepalive") as ka:
        ctrl.send_keepalive_to_cerbo()
    ka.assert_not_called()


def test_keepalive_when_connected():
    ctrl, _ = _build_controller()
    ctrl.mqtt_bridge.connection_ok = True
    with patch.object(ctrl.victron_output, "send_keepalive") as ka:
        ctrl.send_keepalive_to_cerbo()
    ka.assert_called_once_with("test_vrm_id", 0)


def test_heartbeat_skipped_when_disconnected():
    ctrl, _ = _build_controller()
    ctrl.mqtt_bridge.connection_ok = False
    ctrl.mqtt_bridge.client = MagicMock()
    ctrl.send_controller_heartbeat()
    ctrl.mqtt_bridge.client.publish.assert_not_called()


def test_heartbeat_published_when_connected():
    import json

    ctrl, _ = _build_controller()
    ctrl.mqtt_bridge.connection_ok = True
    client = MagicMock()
    ctrl.mqtt_bridge.client = client
    with patch("essBATT_controller.time.time", return_value=1234.5):
        ctrl.send_controller_heartbeat()
    client.publish.assert_called_once()
    kwargs = client.publish.call_args.kwargs
    assert kwargs["topic"] == "essbatt/controller/heartbeat"
    assert kwargs["qos"] == 0
    assert kwargs["retain"] is False
    payload = json.loads(kwargs["payload"])
    assert payload["source"] == "essBATT_controller"
    assert payload["vrm_id"] == "test_vrm_id"
    assert payload["ts"] == 1234.5


def test_heartbeat_disabled_when_topic_none():
    ctrl, _ = _build_controller()
    ctrl.ess_config_data["controller_heartbeat_topic"] = "none"
    ctrl.mqtt_bridge.connection_ok = True
    ctrl.mqtt_bridge.client = MagicMock()
    ctrl.send_controller_heartbeat()
    ctrl.mqtt_bridge.client.publish.assert_not_called()


def test_start_timers_creates_heartbeat_timer():
    ctrl, _ = _build_controller()
    with patch("essBATT_controller.RepeatedTimer", _DummyTimer):
        ctrl._start_timers()
    assert ctrl.rt_controller_heartbeat_obj is not None
    assert ctrl.rt_controller_heartbeat_obj.interval == 30.0
    assert ctrl.rt_controller_heartbeat_obj.function == ctrl.send_controller_heartbeat


def test_start_timers_skips_heartbeat_when_disabled():
    ctrl, logger = _build_controller()
    ctrl.ess_config_data["controller_heartbeat_topic"] = "none"
    with patch("essBATT_controller.RepeatedTimer", _DummyTimer):
        ctrl._start_timers()
    assert ctrl.rt_controller_heartbeat_obj is None
    assert any(
        "heartbeat disabled" in str(c.args[0]).lower()
        for c in logger.info.call_args_list
    )


def test_reload_updates_heartbeat_interval():
    ctrl, _ = _build_controller()
    ctrl.rt_controller_heartbeat_obj = _DummyTimer(30.0, lambda: None)
    new_cfg = _base_config(controller_heartbeat_interval_s=15.0)

    def load_config():
        ctrl.config_manager.config_data_loaded_correctly = True
        return copy.deepcopy(new_cfg)

    ctrl.config_manager.load_config = load_config
    ctrl.reload_config_while_running()
    assert ctrl.rt_controller_heartbeat_obj.interval == 15.0


def test_print_alive_status_connected_and_not():
    ctrl, logger = _build_controller()
    ctrl.mqtt_bridge.connection_ok = True
    ctrl.print_alive_status_to_logger()
    assert "connected" in logger.info.call_args.args[0]

    ctrl.mqtt_bridge.connection_ok = False
    ctrl.print_alive_status_to_logger()
    assert "DISCONNECTED" in logger.info.call_args.args[0]


# --- config reload ---

def test_reload_config_success_propagates_and_updates_timer():
    ctrl, logger = _build_controller()
    new_cfg = _base_config(control_update_rate=5.5, debug_level="INFO")
    ctrl.rt_ess_control_update_obj = _DummyTimer(2.0, lambda: None)

    def load_config():
        ctrl.config_manager.config_data_loaded_correctly = True
        return copy.deepcopy(new_cfg)

    ctrl.config_manager.load_config = load_config
    ctrl.reload_config_while_running()

    assert ctrl.ess_config_data["control_update_rate"] == 5.5
    assert ctrl.rt_ess_control_update_obj.interval == 5.5
    assert ctrl.battery_protector.config is ctrl.ess_config_data
    assert ctrl.mqtt_bridge.config is ctrl.ess_config_data


def test_reload_config_failure_keeps_previous():
    ctrl, logger = _build_controller()
    old = ctrl.ess_config_data

    def bad_load():
        ctrl.config_manager.config_data_loaded_correctly = False
        return {}

    ctrl.config_manager.load_config = bad_load
    ctrl.reload_config_while_running()
    assert ctrl.ess_config_data is old
    logger.error.assert_called()


def test_cycle_triggers_online_config_reload():
    ctrl, _ = _build_controller()
    ctrl.mqtt_bridge.connection_ok = True
    ctrl.ess_config_data["check_ess_config_changes_while_running"] = 1
    with patch.object(ctrl, "reload_config_while_running") as reload, \
         patch.object(ctrl.data_mapper, "read_values_to_local_dict"), \
         patch.object(ctrl.state_machine, "update"), \
         patch.object(ctrl.victron_output, "set_ccgx_value"), \
         patch.object(ctrl, "cleanup_after_control_loop"):
        # Make mapper leave all_CCGX false so we don't need full protector path
        def map_fn(ccgx, local):
            local["all_CCGX_values_available"] = False
        ctrl.data_mapper.read_values_to_local_dict = map_fn
        ctrl.ess_control_cycle_update()
    reload.assert_called_once()


# --- reboot ---

def test_reboot_requests_clean_shutdown():
    ctrl, logger = _build_controller()
    ctrl._running = True
    ctrl.reboot_ess_controller_script()
    assert ctrl._running is False
    logger.warning.assert_called()
    assert "reboot" in logger.warning.call_args.args[0].lower()
