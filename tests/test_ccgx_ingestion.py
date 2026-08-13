"""Tests for CcgxIngestion (Step 7)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ccgx_ingestion import CcgxIngestion


def _msg(topic, value=None, raw_payload=None):
    """Build a minimal MQTT-like message object."""
    if raw_payload is not None:
        payload = raw_payload
    else:
        payload = json.dumps({"value": value}).encode("utf-8")
    return SimpleNamespace(topic=topic, payload=payload)


@pytest.fixture
def ccgx_data():
    return {
        "grid": {},
        "battery": {},
        "solarcharger": {},
        "settings": {},
        "system": {},
    }


@pytest.fixture
def ingestion(ccgx_data):
    return CcgxIngestion(MagicMock(), ccgx_data)


def test_battery_soc_stored(ingestion, ccgx_data):
    ingestion.on_battery_soc(_msg("N/vrm/battery/0/Soc", 67.5))
    assert ccgx_data["battery"]["soc"] == 67.5


def test_battery_cell_voltages(ingestion, ccgx_data):
    ingestion.on_battery_maxcellvoltage(_msg("N/vrm/battery/0/System/MaxCellVoltage", 3.45))
    ingestion.on_battery_mincellvoltage(_msg("N/vrm/battery/0/System/MinCellVoltage", 3.22))
    assert ccgx_data["battery"]["max_cell_voltage"] == 3.45
    assert ccgx_data["battery"]["min_cell_voltage"] == 3.22


def test_grid_power(ingestion, ccgx_data):
    ingestion.on_grid_power(_msg("N/vrm/grid/30/Ac/Power", -120))
    assert ccgx_data["grid"]["grid_power_sum"] == -120


def test_invalid_payload_clears_only_that_field(ingestion, ccgx_data):
    ccgx_data["battery"]["soc"] = 50
    ccgx_data["battery"]["voltage"] = 52.0
    ingestion.on_battery_soc(_msg("N/vrm/battery/0/Soc", raw_payload=b"not-json"))
    assert "soc" not in ccgx_data["battery"]
    assert ccgx_data["battery"]["voltage"] == 52.0


def test_solarcharger_power_creates_instance(ingestion, ccgx_data):
    ingestion.on_solarcharger_power(_msg("N/vrm/solarcharger/279/Yield/Power", 400))
    assert ccgx_data["solarcharger"]["279"]["Power"] == 400


def test_solarcharger_dc_values(ingestion, ccgx_data):
    ingestion.on_solarcharger_dc_values(
        _msg("N/vrm/solarcharger/279/Dc/0/Current", 8.5)
    )
    assert ccgx_data["solarcharger"]["279"]["Current"] == 8.5


def test_solarcharger_removed_deletes_only_that_instance(ingestion, ccgx_data):
    ccgx_data["solarcharger"]["279"] = {"Power": 100}
    ccgx_data["solarcharger"]["280"] = {"Power": 50}
    ingestion.on_solarcharger_power(
        _msg("N/vrm/solarcharger/279/Yield/Power", raw_payload=b"{}")
    )
    assert "279" not in ccgx_data["solarcharger"]
    assert ccgx_data["solarcharger"]["280"]["Power"] == 50


def test_system_ac_consumption_power(ingestion, ccgx_data):
    ingestion.on_system_ac_consumption(
        _msg("N/vrm/system/0/Ac/Consumption/L1/Power", 220)
    )
    assert ccgx_data["system"]["L1_loads_power_consumption"] == 220


def test_system_ac_consumption_ignores_number_of_phases(ingestion, ccgx_data):
    ingestion.on_system_ac_consumption(
        _msg("N/vrm/system/0/Ac/Consumption/NumberOfPhases/Power", 3)
    )
    # NumberOfPhases is not stored as loads
    assert "NumberOfPhases_loads_power_consumption" not in ccgx_data["system"]


def test_settings_cgwacs_sets_base_path(ingestion, ccgx_data):
    ingestion.on_settings_cgwacs(
        _msg("N/vrm/settings/0/Settings/CGwacs/AcPowerSetPoint", 0)
    )
    assert ccgx_data["settings"]["AcPowerSetPoint"] == 0
    assert ccgx_data["settings_base_path"] == "settings/0/Settings/"


def test_settings_system_setup(ingestion, ccgx_data):
    ingestion.on_settings_system_setup(
        _msg("N/vrm/settings/0/Settings/SystemSetup/MaxChargeCurrent", 25)
    )
    assert ccgx_data["settings"]["MaxChargeCurrent"] == 25


def test_vebus_mode(ingestion, ccgx_data):
    ingestion.on_multis_switch_mode(_msg("N/vrm/vebus/276/Mode", 3))
    assert ccgx_data["vebus"]["276"]["Mode"] == 3


def test_parse_victron_mqtt_value_missing_value_key(ingestion):
    msg = _msg("N/vrm/x", raw_payload=json.dumps({"other": 1}).encode())
    assert ingestion.parse_victron_mqtt_value(msg) is None


def test_malformed_system_topic_logs_error(ingestion, ccgx_data):
    # Too short topic → IndexError path
    ingestion.on_system_ac_consumption(_msg("N/vrm/system/0", 1))
    assert ccgx_data["system"] == {}
