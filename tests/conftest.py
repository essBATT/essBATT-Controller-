"""Shared pytest fixtures for essBATT tests.

This setup allows effective testing of the full functionality:
- Unit tests for pure functions (limits, state logic).
- Mocked MQTT, file I/O, timers for integration tests.
- Parametrized tests for edge cases (from documentation).
- Fixtures for config, logger, and temporary state files.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


@pytest.fixture
def sample_config():
    """Sample minimal config for tests (based on documented values)."""
    return {
        "config_version": 1.0,
        "vrm_id": "test_vrm_id",
        "debug_level": "DEBUG",
        "mqtt_username": "test",
        "mqtt_password": "test",
        "mqtt_server_COM_port": 1883,
        "control_update_rate": 2.0,
        "script_alive_logging_interval": 86400,
        "ess_mode_2_settings": {
            "grid_power_setpoint_2700": 0,
            "max_battery_discharge_current": 50,
            "max_battery_charge_current_2705": 50,
        },
        "battery_settings": {
            "charge_limit_mode": "max_cell_only",
            "max_cell_voltage_charging": 3.55,
            "max_cell_voltage_charging_resume": 3.5,
            "soc_based_charge_limit_soc_array": [80, 90, 95],
            "soc_based_charge_limit_current_array": [25, 10, 5],
            # ... (more can be added per test)
            "discharge_limit_mode": "soc_and_min_cell",
            "min_cell_voltage_discharging": 3.1,
            "min_cell_voltage_discharging_resume": 3.25,
        },
        "balancing_settings": {"auto_balancing_settings": {"activate_auto_balancing": 0}},
        "winter_mode": {"use_winter_mode": 0},
        "external_control_settings": {"allow_external_control_over_mqtt": 0},
    }


@pytest.fixture
def mocked_logger():
    """Mocked logger for tests (captures calls without real logging)."""
    return MagicMock()


@pytest.fixture
def mocked_mqtt_client():
    """Mocked paho MQTT client for testing publish/subscribe without real broker."""
    client = MagicMock()
    client.publish.return_value = (0, 1)  # success
    return client


@pytest.fixture
def temp_config_dir(tmp_path):
    """Temporary directory for config/state files during tests."""
    config_file = tmp_path / "ess_config.json"
    state_file = tmp_path / "ess_controller_state"
    return {
        "dir": tmp_path,
        "config": config_file,
        "state": state_file,
    }


@pytest.fixture
def sample_ess_controller_state():
    """Sample state dict for state machine tests."""
    return {
        "current_state": "normal_operation",
        "time_of_last_change": "01-01-2025 (12:00:00.000)",
        "balancing": {"activation_time": "none", "max_current": "none", "scheduled_start_time": "none"},
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
