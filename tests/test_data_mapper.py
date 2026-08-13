"""Tests for CcgxDataMapper (Step 5 of modularization)."""

import pytest
from unittest.mock import MagicMock

from data_mapper import CcgxDataMapper


@pytest.fixture
def mapper():
    return CcgxDataMapper(MagicMock())


@pytest.fixture
def full_ccgx_data():
    """Complete CCGX_data structure as filled by MQTT callbacks."""
    return {
        'grid': {'grid_power_sum': 100},
        'battery': {
            'soc': 55,
            'max_cell_voltage': 3.40,
            'min_cell_voltage': 3.28,
            'current': 3.5,
            'power': 180,
            'voltage': 52.0,
        },
        'system': {
            'L1_loads_power_consumption': 200,
            'L2_loads_power_consumption': 150,
            'L3_loads_power_consumption': 100,
        },
        'solarcharger': {
            '279': {'Power': 400, 'Current': 8.0},
            '280': {'Power': 100, 'Current': 2.0},
        },
        'settings': {},
    }


def test_full_data_maps_all_keys_and_marks_available(mapper, full_ccgx_data):
    local = {}
    mapper.read_values_to_local_dict(full_ccgx_data, local)

    assert local['all_CCGX_values_available'] is True
    assert local['battery_soc'] == 55
    assert local['battery_max_cell_voltage'] == 3.40
    assert local['battery_min_cell_voltage'] == 3.28
    assert local['battery_current'] == 3.5
    assert local['battery_voltage'] == 52.0
    assert local['battery_power'] == 180
    assert local['grid_power_sum'] == 100
    assert local['solarcharger_power_sum'] == 500
    assert local['solarcharger_current_sum'] == 10.0
    assert local['loads_total_power'] == 450
    # losses = (grid - battery + solar) - loads = (100 - 180 + 500) - 450 = -30
    assert local['losses_dc2ac_est'] == pytest.approx(-30)


def test_missing_battery_soc_marks_incomplete(mapper, full_ccgx_data):
    del full_ccgx_data['battery']['soc']
    local = {}
    mapper.read_values_to_local_dict(full_ccgx_data, local)
    assert local['all_CCGX_values_available'] is False
    assert 'battery_soc' not in local


def test_missing_min_cell_marks_incomplete(mapper, full_ccgx_data):
    del full_ccgx_data['battery']['min_cell_voltage']
    local = {}
    mapper.read_values_to_local_dict(full_ccgx_data, local)
    assert local['all_CCGX_values_available'] is False


def test_empty_solarchargers_still_complete_if_other_data_present(mapper, full_ccgx_data):
    """No solarchargers on bus is OK (sum stays 0); incomplete only if known charger lacks fields."""
    full_ccgx_data['solarcharger'] = {}
    local = {}
    mapper.read_values_to_local_dict(full_ccgx_data, local)
    assert local['all_CCGX_values_available'] is True
    assert local['solarcharger_power_sum'] == 0
    assert local['solarcharger_current_sum'] == 0


def test_solarcharger_missing_power_marks_incomplete(mapper, full_ccgx_data):
    full_ccgx_data['solarcharger'] = {'279': {'Current': 5.0}}  # Power missing
    local = {}
    mapper.read_values_to_local_dict(full_ccgx_data, local)
    assert local['all_CCGX_values_available'] is False


def test_removed_solarcharger_stub_is_ignored(mapper, full_ccgx_data):
    """Empty leftover instance must not mark the snapshot incomplete."""
    full_ccgx_data['solarcharger'] = {
        '279': {},
        '280': {'Power': 100, 'Current': 2.0},
    }
    local = {}
    mapper.read_values_to_local_dict(full_ccgx_data, local)
    assert local['all_CCGX_values_available'] is True
    assert local['solarcharger_power_sum'] == 100
    assert local['solarcharger_current_sum'] == 2.0


def test_controller_key_contract_for_battery_protector(mapper, full_ccgx_data):
    """Mapped keys must match BatteryProtector REQUIRED_BATTERY_KEYS."""
    from battery_protection import REQUIRED_BATTERY_KEYS

    local = {}
    mapper.read_values_to_local_dict(full_ccgx_data, local)
    for key in REQUIRED_BATTERY_KEYS:
        assert key in local, f"Missing key required by BatteryProtector: {key}"
    assert 'solarcharger_power_sum' in local


def test_missing_phase_load_marks_incomplete(mapper, full_ccgx_data):
    del full_ccgx_data['system']['L2_loads_power_consumption']
    local = {}
    mapper.read_values_to_local_dict(full_ccgx_data, local)
    assert local['all_CCGX_values_available'] is False
    assert 'loads_total_power' not in local


def test_empty_ccgx_data(mapper):
    local = {}
    mapper.read_values_to_local_dict(
        {'grid': {}, 'battery': {}, 'system': {}, 'solarcharger': {}},
        local,
    )
    assert local['all_CCGX_values_available'] is False
    assert local['solarcharger_power_sum'] == 0
