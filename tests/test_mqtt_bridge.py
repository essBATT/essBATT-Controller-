"""Tests for MqttBridge (Phase B / point 2)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from mqtt_bridge import MqttBridge
from external_control import ExternalControlHandlers


@pytest.fixture
def config():
    return {
        "vrm_id": "vrm123",
        "mqtt_username": "user",
        "mqtt_password": "pass",
        "mqtt_server_COM_port": 1883,
        "external_control_settings": {
            "allow_external_control_over_mqtt": 1,
            "mqtt_external_control_topics": {
                "charge_battery_to_SOC": "iobroker/charge",
                "activate_top_balancing_mode": "none",
                "deactivate_discharge": "none",
                "deactivate_charge": "none",
                "reboot_ess_controller": "none",
            },
        },
    }


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def ingestion():
    ing = MagicMock()
    # Real attribute names used by build_ccgx_topic_bindings
    for name in (
        "on_grid_power", "on_L1_power", "on_L1_current",
        "on_L2_power", "on_L2_current", "on_L3_power", "on_L3_current",
        "on_battery_soc", "on_battery_maxcellvoltage", "on_battery_mincellvoltage",
        "on_battery_temp", "on_battery_current", "on_battery_power", "on_battery_voltage",
        "on_solarcharger_power", "on_solarcharger_dc_values",
        "on_system_ac_consumption", "on_settings_cgwacs",
        "on_settings_system_setup", "on_multis_switch_mode",
    ):
        setattr(ing, name, MagicMock(name=name))
    return ing


@pytest.fixture
def external_handlers(logger):
    return ExternalControlHandlers(logger, {})


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.subscribe.return_value = (0, 1)
    return client


@pytest.fixture
def bridge(logger, config, ingestion, external_handlers, mock_client):
    return MqttBridge(
        logger,
        config,
        ingestion,
        external_handlers,
        host="localhost",
        client_factory=lambda: mock_client,
        sleep_fn=lambda _s: None,
    )


def test_setup_sets_credentials_and_callbacks(bridge, mock_client, config):
    client = bridge.setup()
    assert client is mock_client
    mock_client.username_pw_set.assert_called_once_with(
        username=config["mqtt_username"],
        password=config["mqtt_password"],
    )
    # Bound methods are recreated on access; verify wiring by invoking callbacks
    mock_client.on_connect(None, None, None, 0)
    assert bridge.connection_ok is True
    mock_client.on_disconnect(None, None, 1)
    assert bridge.connection_ok is False
    assert bridge.disconnected is True


def test_on_connect_success(bridge, logger):
    bridge.on_connect(None, None, None, 0)
    assert bridge.connection_ok is True
    assert bridge.disconnected is False
    assert bridge.is_connected is True
    logger.info.assert_called()


def test_on_connect_failure(bridge, logger):
    bridge.on_connect(None, None, None, 5)
    assert bridge.connection_ok is False
    assert bridge.is_connected is False
    logger.error.assert_called()


def test_on_disconnect(bridge, logger):
    bridge.connection_ok = True
    bridge.disconnected = False
    bridge.on_disconnect(None, None, 1)
    assert bridge.connection_ok is False
    assert bridge.disconnected is True
    logger.warning.assert_called()


def test_on_message_logs_debug(bridge, logger):
    msg = SimpleNamespace(topic="N/x/y", payload=b"{}")
    bridge.on_message(None, None, msg)
    logger.debug.assert_called()


def test_subscribe_and_register_handlers(bridge, mock_client, config):
    bridge.setup()
    result = bridge.subscribe_and_register_handlers()
    assert result == 0
    mock_client.subscribe.assert_called_once()
    sub_list = mock_client.subscribe.call_args.args[0]
    topics = [t for t, _ in sub_list]
    assert "N/vrm123/battery/#" in topics
    assert "iobroker/charge" in topics
    # CCGX bindings + 1 external topic
    assert mock_client.message_callback_add.call_count == 21


def test_subscribe_without_client_returns_none(bridge):
    assert bridge.subscribe_and_register_handlers() is None


def test_connect_starts_loop(bridge, mock_client, config):
    bridge.setup()
    bridge.connect()
    mock_client.connect.assert_called_once()
    kwargs = mock_client.connect.call_args
    assert kwargs.args[0] == "localhost"
    assert kwargs.kwargs.get("port") == config["mqtt_server_COM_port"] or (
        len(kwargs.args) > 1 and kwargs.args[1] == config["mqtt_server_COM_port"]
    ) or mock_client.connect.call_args.kwargs.get("port") == 1883
    mock_client.loop_start.assert_called_once()


def test_wait_until_connected_exits_when_flag_set(bridge):
    sleeps = []

    def sleep_fn(s):
        sleeps.append(s)
        # After first wait, simulate successful connect
        bridge.connection_ok = True

    bridge._sleep_fn = sleep_fn
    bridge.connection_ok = False
    bridge.wait_until_connected()
    assert bridge.connection_ok is True
    assert sleeps == [1]


def test_start_full_sequence(bridge, mock_client):
    # Make wait_until_connected succeed immediately via on_connect during connect
    def connect_side_effect(*args, **kwargs):
        bridge.on_connect(mock_client, None, None, 0)

    mock_client.connect.side_effect = connect_side_effect
    bridge.start()

    mock_client.username_pw_set.assert_called()
    mock_client.connect.assert_called()
    mock_client.loop_start.assert_called()
    mock_client.subscribe.assert_called()
    assert bridge.is_connected is True
    assert bridge.client is mock_client


def test_stop_calls_loop_stop(bridge, mock_client):
    bridge.setup()
    bridge.stop()
    mock_client.loop_stop.assert_called_once()


def test_stop_without_client_is_safe(bridge):
    bridge.stop()  # must not raise


def test_update_config(bridge):
    new_cfg = {"vrm_id": "other"}
    bridge.update_config(new_cfg)
    assert bridge.config is new_cfg
