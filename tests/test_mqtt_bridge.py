"""Tests for MqttBridge (connect / reconnect / timeout / stop)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mqtt_bridge import MqttBridge, STATE_CONNECTED, STATE_DISCONNECTED, STATE_CONNECTING
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
def on_connected():
    return MagicMock()


@pytest.fixture
def bridge(logger, config, ingestion, external_handlers, mock_client, on_connected):
    return MqttBridge(
        logger,
        config,
        ingestion,
        external_handlers,
        host="localhost",
        client_factory=lambda: mock_client,
        sleep_fn=lambda _s: None,
        on_connected=on_connected,
        time_fn=lambda: 1000.0,
    )


def test_setup_sets_credentials_and_callbacks(bridge, mock_client, config):
    client = bridge.setup()
    assert client is mock_client
    mock_client.username_pw_set.assert_called_once_with(
        username=config["mqtt_username"],
        password=config["mqtt_password"],
    )
    mock_client.on_connect(None, None, None, 0)
    assert bridge.is_connected is True
    mock_client.on_disconnect(None, None, 1)
    assert bridge.is_connected is False
    assert bridge.disconnected is True


def test_on_connect_success_subscribes_and_callbacks(bridge, mock_client, on_connected, logger):
    bridge.setup()
    bridge.on_connect(mock_client, None, {"session present": 0}, 0)

    assert bridge.state == STATE_CONNECTED
    assert bridge.is_connected is True
    mock_client.subscribe.assert_called_once()
    assert mock_client.message_callback_add.call_count == 21
    on_connected.assert_called_once_with(is_reconnect=False)
    logger.info.assert_called()


def test_on_connect_reconnect_resubscribes_but_handlers_once(
    bridge, mock_client, on_connected
):
    bridge.setup()
    bridge.on_connect(mock_client, None, None, 0)
    mock_client.subscribe.reset_mock()
    mock_client.message_callback_add.reset_mock()
    on_connected.reset_mock()

    # Simulate drop + reconnect
    bridge.on_disconnect(mock_client, None, 1)
    assert bridge.state == STATE_DISCONNECTED
    assert bridge.disconnected_since is not None

    bridge.on_connect(mock_client, None, {"session present": 0}, 0)

    assert bridge.is_connected is True
    mock_client.subscribe.assert_called_once()  # must resubscribe
    # Topic handlers registered only on first connect
    mock_client.message_callback_add.assert_not_called()
    on_connected.assert_called_once_with(is_reconnect=True)


def test_on_connect_failure(bridge, logger, on_connected):
    bridge.on_connect(None, None, None, 5)
    assert bridge.is_connected is False
    assert bridge.state == STATE_DISCONNECTED
    logger.error.assert_called()
    on_connected.assert_not_called()


def test_on_disconnect_unexpected(bridge, logger):
    bridge.connection_ok = True
    bridge.on_disconnect(None, None, 1)
    assert bridge.state == STATE_DISCONNECTED
    assert bridge.disconnected is True
    logger.warning.assert_called()
    assert "auto-reconnect" in logger.warning.call_args.args[0]


def test_on_disconnect_during_stop_is_info(bridge, logger):
    bridge.setup()
    bridge._stopping = True
    bridge.on_disconnect(None, None, 0)
    logger.info.assert_called()


def test_on_message_logs_debug(bridge, logger):
    msg = SimpleNamespace(topic="N/x/y", payload=b"{}")
    bridge.on_message(None, None, msg)
    logger.debug.assert_called()


def test_subscribe_and_register_handlers(bridge, mock_client):
    bridge.setup()
    result = bridge.subscribe_and_register_handlers()
    assert result == 0
    mock_client.subscribe.assert_called_once()
    sub_list = mock_client.subscribe.call_args.args[0]
    topics = [t for t, _ in sub_list]
    assert "N/vrm123/battery/#" in topics
    assert "iobroker/charge" in topics
    assert mock_client.message_callback_add.call_count == 21


def test_subscribe_without_client_returns_none(bridge):
    assert bridge.subscribe_and_register_handlers() is None


def test_connect_starts_loop(bridge, mock_client, config):
    bridge.setup()
    bridge.connect()
    assert bridge.state == STATE_CONNECTING
    mock_client.connect.assert_called_once()
    call = mock_client.connect.call_args
    assert call.args[0] == "localhost"
    assert call.kwargs.get("port") == config["mqtt_server_COM_port"] or call.kwargs.get(
        "keepalive"
    ) == 60
    mock_client.loop_start.assert_called_once()


def test_wait_until_connected_exits_when_flag_set(bridge):
    clock = {"t": 0.0}
    sleeps = []

    def sleep_fn(s):
        sleeps.append(s)
        clock["t"] += s
        bridge.connection_ok = True

    bridge._sleep_fn = sleep_fn
    bridge._time_fn = lambda: clock["t"]
    bridge.connection_ok = False
    assert bridge.wait_until_connected(timeout=10) is True
    assert sleeps == [1]


def test_wait_until_connected_times_out(bridge, logger):
    clock = {"t": 0.0}

    def sleep_fn(s):
        clock["t"] += s

    bridge._sleep_fn = sleep_fn
    bridge._time_fn = lambda: clock["t"]
    bridge.connection_ok = False
    assert bridge.wait_until_connected(timeout=3) is False
    logger.error.assert_called()
    assert clock["t"] >= 3


def test_start_full_sequence(bridge, mock_client, on_connected):
    def connect_side_effect(*args, **kwargs):
        bridge.on_connect(mock_client, None, None, 0)

    mock_client.connect.side_effect = connect_side_effect
    bridge.start()

    mock_client.username_pw_set.assert_called()
    mock_client.connect.assert_called()
    mock_client.loop_start.assert_called()
    mock_client.subscribe.assert_called()
    assert bridge.is_connected is True
    on_connected.assert_called_with(is_reconnect=False)


def test_start_timeout_raises_and_stops(bridge, mock_client):
    # Never get CONNACK
    bridge._time_fn = lambda: 0.0
    # Make wait timeout immediately
    times = {"n": 0}

    def time_fn():
        times["n"] += 1
        # first call deadline base, then always past deadline
        return 0.0 if times["n"] == 1 else 999.0

    bridge._time_fn = time_fn
    bridge._sleep_fn = lambda _s: None

    with pytest.raises(TimeoutError):
        bridge.start(connect_timeout=1)

    mock_client.loop_stop.assert_called()
    mock_client.disconnect.assert_called()


def test_stop_disconnects_and_loop_stop(bridge, mock_client):
    bridge.setup()
    bridge.stop()
    mock_client.disconnect.assert_called_once()
    mock_client.loop_stop.assert_called_once()
    assert bridge.state == STATE_DISCONNECTED


def test_stop_without_client_is_safe(bridge):
    bridge.stop()  # must not raise


def test_update_config(bridge):
    new_cfg = {"vrm_id": "other"}
    bridge.update_config(new_cfg)
    assert bridge.config is new_cfg


def test_disconnect_duration(bridge):
    bridge._time_fn = lambda: 100.0
    bridge.connection_ok = True
    assert bridge.disconnect_duration_s() == 0.0

    bridge._time_fn = lambda: 100.0
    bridge.on_disconnect(None, None, 1)
    bridge._time_fn = lambda: 140.0
    assert bridge.disconnect_duration_s() == pytest.approx(40.0)
