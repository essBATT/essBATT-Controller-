"""Tests for ConfigManager (Step 2 of modularization).

Uses fixtures from conftest.py for sample data and temporary directories.
"""

import json
from unittest.mock import MagicMock, mock_open, patch

import pytest

from config_manager import ConfigManager


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
