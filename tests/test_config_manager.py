"""Tests for ConfigManager (Step 2 of modularization).

Uses fixtures from conftest.py for sample data and temporary directories.
"""

import json
from unittest.mock import MagicMock, mock_open, patch

import pytest

from config_manager import ConfigManager, validate_ess_config, DEFAULT_CONTROLLER_STATE


def test_config_manager_init(mocked_logger):
    """Test that ConfigManager initializes correctly."""
    manager = ConfigManager(mocked_logger, debug=True)
    assert manager.logger == mocked_logger
    assert manager.debug is True
    assert manager.config_data_loaded_correctly is False


def test_load_config_success(sample_config, temp_config_dir, mocked_logger):
    """Test successful loading of config file."""
    config_path = temp_config_dir["config"]
    config_path.write_text(json.dumps(sample_config))

    manager = ConfigManager(mocked_logger, debug=False)
    # Override path for test
    manager._get_config_path = lambda d, p: config_path

    data = manager.load_config()
    assert data == sample_config
    assert manager.config_data_loaded_correctly is True
    mocked_logger.debug.assert_called()


def test_load_config_file_not_found(mocked_logger, temp_config_dir):
    """Test graceful handling when config file is missing."""
    manager = ConfigManager(mocked_logger, debug=False)
    manager._get_config_path = lambda d, p: temp_config_dir["config"]  # non-existing

    data = manager.load_config()
    assert data == {}
    assert manager.config_data_loaded_correctly is False
    mocked_logger.error.assert_called()


def test_load_state_creates_default_when_missing(mocked_logger, temp_config_dir):
    state_path = temp_config_dir["state"]
    assert not state_path.exists()
    manager = ConfigManager(mocked_logger, debug=False)
    manager._get_config_path = lambda d, p: state_path

    loaded = manager.load_state()
    assert manager.controller_state_loaded_correctly is True
    assert loaded["current_state"] == "normal_operation"
    assert loaded["charge_to_SOC"]["target_SOC"] == "none"
    assert state_path.exists()
    assert loaded["ess_controller_state_version"] == DEFAULT_CONTROLLER_STATE["ess_controller_state_version"]


def test_load_state_success(sample_ess_controller_state, temp_config_dir, mocked_logger):
    """Test loading and saving of controller state (roundtrip)."""
    state_path = temp_config_dir["state"]
    state_path.write_text(json.dumps(sample_ess_controller_state))

    manager = ConfigManager(mocked_logger, debug=False)
    manager._get_config_path = lambda d, p: state_path

    loaded = manager.load_state()
    assert loaded == sample_ess_controller_state
    assert manager.controller_state_loaded_correctly is True

    # Test save
    new_state = dict(loaded)
    new_state["current_state"] = "balancing"
    success = manager.save_state(new_state)
    assert success is True
    assert manager.controller_state_loaded_correctly is True


def test_create_temporary_script_states(sample_config, mocked_logger):
    """Test creation of temporary states dict from config."""
    manager = ConfigManager(mocked_logger)
    states = manager.create_temporary_script_states(sample_config)

    assert isinstance(states, dict)
    assert "discharge_current_limit_state" in states
    assert states["discharge_current_limit_hit_zero"] is False
    assert "charge_current_limit_state" in states
    assert "discharge_regular_current_limit_last_cycle" in states
    assert states["discharge_regular_current_limit_last_cycle"] == 0.0
    assert states["emergency_charge_begin_time"] is None
    assert states["emergency_discharge_begin_time"] is None


def test_save_state_if_changed(mocked_logger):
    """Test conditional save only when state actually changed."""
    manager = ConfigManager(mocked_logger)
    manager.save_state = MagicMock(return_value=True)

    state1 = {"current_state": "normal_operation"}
    state2 = {"current_state": "balancing"}

    # No change
    manager.save_state_if_changed(state1, state1)
    # Change
    changed = manager.save_state_if_changed(state2, state1)

    assert changed is True
    manager.save_state.assert_called_once()


def test_validate_sample_config_is_ok(sample_config):
    ok, errors, warnings = validate_ess_config(sample_config)
    assert ok is True
    assert errors == []
    assert sample_config["debug_level"] == "DEBUG"


def test_validate_normalizes_lowercase_debug_level(sample_config):
    sample_config["debug_level"] = "info"
    ok, errors, warnings = validate_ess_config(sample_config)
    assert ok is True
    assert sample_config["debug_level"] == "INFO"
    assert errors == []


def test_validate_unknown_debug_level_falls_back(sample_config):
    sample_config["debug_level"] = "verbose"
    ok, errors, warnings = validate_ess_config(sample_config)
    assert ok is True
    assert sample_config["debug_level"] == "INFO"
    assert any("debug_level" in w for w in warnings)


def test_validate_placeholder_vrm_id_warns(sample_config):
    sample_config["vrm_id"] = "YOUR VRM ID"
    ok, errors, warnings = validate_ess_config(sample_config)
    assert ok is True
    assert any("vrm_id" in w for w in warnings)


def test_validate_mismatched_charge_arrays_is_error(sample_config):
    sample_config["battery_settings"]["soc_based_charge_limit_soc_array"] = [80, 90]
    sample_config["battery_settings"]["soc_based_charge_limit_current_array"] = [25]
    ok, errors, warnings = validate_ess_config(sample_config)
    assert ok is False
    assert any("length" in e for e in errors)


def test_validate_unsorted_charge_soc_array_warns(sample_config):
    sample_config["battery_settings"]["soc_based_charge_limit_soc_array"] = [90, 80, 95]
    sample_config["battery_settings"]["soc_based_charge_limit_current_array"] = [10, 25, 5]
    ok, errors, warnings = validate_ess_config(sample_config)
    assert ok is True
    assert any("ascending" in w for w in warnings)


def test_validate_inverted_charge_resume_is_error(sample_config):
    sample_config["battery_settings"]["max_cell_voltage_charging"] = 3.50
    sample_config["battery_settings"]["max_cell_voltage_charging_resume"] = 3.56
    ok, errors, _warnings = validate_ess_config(sample_config)
    assert ok is False
    assert any("charging_resume" in e for e in errors)


def test_validate_inverted_discharge_resume_is_error(sample_config):
    sample_config["battery_settings"]["min_cell_voltage_discharging"] = 3.25
    sample_config["battery_settings"]["min_cell_voltage_discharging_resume"] = 3.10
    ok, errors, _warnings = validate_ess_config(sample_config)
    assert ok is False
    assert any("discharging_resume" in e for e in errors)


def test_validate_winter_soc_order_error_when_enabled(sample_config):
    sample_config["winter_mode"]["use_winter_mode"] = 1
    sample_config["winter_mode"]["winter_min_SOC"] = 70
    sample_config["winter_mode"]["winter_restart_multis_SOC"] = 25
    ok, errors, _warnings = validate_ess_config(sample_config)
    assert ok is False
    assert any("winter_restart_multis_SOC" in e for e in errors)


def test_validate_winter_soc_order_warns_when_disabled(sample_config):
    sample_config["winter_mode"]["use_winter_mode"] = 0
    sample_config["winter_mode"]["winter_min_SOC"] = 70
    sample_config["winter_mode"]["winter_restart_multis_SOC"] = 25
    ok, errors, warnings = validate_ess_config(sample_config)
    assert ok is True
    assert errors == []
    assert any("winter_restart_multis_SOC" in w for w in warnings)


def test_validate_invalid_winter_dates_error_when_enabled(sample_config):
    sample_config["winter_mode"]["use_winter_mode"] = 1
    sample_config["winter_mode"]["winter_mode_start_date"] = "32.13."
    ok, errors, _warnings = validate_ess_config(sample_config)
    assert ok is False
    assert any("dates" in e for e in errors)


def test_load_config_rejects_invalid_file(mocked_logger, temp_config_dir, sample_config):
    sample_config["battery_settings"]["soc_based_charge_limit_current_array"] = [1]
    config_path = temp_config_dir["config"]
    config_path.write_text(json.dumps(sample_config))

    manager = ConfigManager(mocked_logger, debug=False)
    manager._get_config_path = lambda d, p: config_path
    data = manager.load_config()
    assert data == {}
    assert manager.config_data_loaded_correctly is False
    mocked_logger.error.assert_called()
