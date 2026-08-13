"""Tests for mqtt_helpers (Step 6 + callback registration table)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mqtt_helpers import (
    build_subscription_list,
    build_keepalive_selected_topics,
    build_keepalive_publish,
    build_ccgx_topic_bindings,
    build_external_topic_bindings,
    register_topic_callbacks,
    is_usable_mqtt_topic,
)
from ccgx_ingestion import CcgxIngestion
from external_control import ExternalControlHandlers


def test_subscription_list_basic_without_external():
    config = {
        "external_control_settings": {
            "allow_external_control_over_mqtt": 0,
            "mqtt_external_control_topics": {},
        }
    }
    subs = build_subscription_list("N/test_vrm", config)
    topics = [t for t, _qos in subs]
    assert "N/test_vrm/battery/#" in topics
    assert "N/test_vrm/grid/#" in topics
    assert "N/test_vrm/solarcharger/#" in topics
    assert "N/test_vrm/system/+/Ac/Consumption/#" in topics
    assert "N/test_vrm/settings/#" in topics
    assert "N/test_vrm/vebus/+/Mode" in topics
    # No external topics
    assert len(subs) == 6
    assert all(qos == 1 for _, qos in subs)


def test_subscription_list_includes_external_topics():
    config = {
        "external_control_settings": {
            "allow_external_control_over_mqtt": 1,
            "mqtt_external_control_topics": {
                "charge_battery_to_SOC": "iobroker/charge",
                "activate_top_balancing_mode": "iobroker/balance",
                "deactivate_discharge": "iobroker/no_dis",
                "deactivate_charge": "iobroker/no_ch",
                "reboot_ess_controller": "iobroker/reboot",
            },
        }
    }
    subs = build_subscription_list("N/vrm1", config)
    topics = [t for t, _ in subs]
    assert "iobroker/charge" in topics
    assert "iobroker/balance" in topics
    assert "iobroker/no_dis" in topics
    assert "iobroker/no_ch" in topics
    assert "iobroker/reboot" in topics
    assert len(subs) == 11


def test_subscription_list_skips_none_external_topics():
    config = {
        "external_control_settings": {
            "allow_external_control_over_mqtt": 1,
            "mqtt_external_control_topics": {
                "charge_battery_to_SOC": "none",
                "activate_top_balancing_mode": "iobroker/balance",
                "deactivate_discharge": "none",
                "deactivate_charge": "none",
                "reboot_ess_controller": "none",
            },
        }
    }
    subs = build_subscription_list("N/vrm1", config)
    topics = [t for t, _ in subs]
    assert "iobroker/balance" in topics
    assert "none" not in topics
    assert len(subs) == 7


def test_subscription_list_skips_example_placeholder_topics():
    config = {
        "external_control_settings": {
            "allow_external_control_over_mqtt": 1,
            "mqtt_external_control_topics": {
                "charge_battery_to_SOC": "EXAMPLE: iobroker/charge",
                "activate_top_balancing_mode": "iobroker/balance",
                "deactivate_discharge": "none",
                "deactivate_charge": "EXAMPLE: iobroker/no_ch",
                "reboot_ess_controller": "none",
            },
        }
    }
    topics = [t for t, _ in build_subscription_list("N/vrm1", config)]
    assert "iobroker/balance" in topics
    assert not any(t.startswith("EXAMPLE:") for t in topics)


def test_is_usable_mqtt_topic():
    assert is_usable_mqtt_topic("iobroker/charge") is True
    assert is_usable_mqtt_topic("none") is False
    assert is_usable_mqtt_topic("EXAMPLE: iobroker/x") is False
    assert is_usable_mqtt_topic("example: iobroker/x") is False
    assert is_usable_mqtt_topic("") is False
    assert is_usable_mqtt_topic(None) is False


def test_keepalive_selected_topics_contains_critical_paths():
    topics = build_keepalive_selected_topics()
    assert "battery/+/Soc" in topics
    assert "battery/+/System/MaxCellVoltage" in topics
    assert "battery/+/System/MinCellVoltage" in topics
    assert "vebus/+/Mode" in topics
    assert "grid/+/Ac/Power" in topics


def test_keepalive_publish_all_topics_mode():
    topic, payload = build_keepalive_publish("VRM123", 1)
    assert topic == "R/VRM123/system/0/Serial"
    assert payload == ""


def test_keepalive_publish_selected_mode():
    topic, payload = build_keepalive_publish("VRM123", 0)
    assert topic == "R/VRM123/keepalive"
    parsed = json.loads(payload)
    assert isinstance(parsed, list)
    assert "battery/+/Soc" in parsed
    assert parsed == build_keepalive_selected_topics()


def test_keepalive_publish_invalid_mode():
    assert build_keepalive_publish("VRM123", 99) is None
    assert build_keepalive_publish("VRM123", -1) is None


def test_ccgx_topic_bindings_cover_core_paths():
    logger = MagicMock()
    ccgx_data = {'grid': {}, 'battery': {}, 'solarcharger': {}, 'settings': {}, 'system': {}}
    ingestion = CcgxIngestion(logger, ccgx_data)
    bindings = build_ccgx_topic_bindings("N/test_vrm", ingestion)

    topics = [t for t, _ in bindings]
    assert "N/test_vrm/grid/+/Ac/Power" in topics
    assert "N/test_vrm/battery/+/Soc" in topics
    assert "N/test_vrm/battery/+/System/MaxCellVoltage" in topics
    assert "N/test_vrm/solarcharger/+/Dc/0/#" in topics
    assert "N/test_vrm/settings/+/Settings/CGwacs/#" in topics
    assert "N/test_vrm/vebus/+/Mode" in topics
    assert len(bindings) == 20
    # All callbacks are paho-compatible (client, userdata, msg)
    for _topic, cb in bindings:
        assert callable(cb)


def test_ccgx_binding_callback_invokes_ingestion():
    logger = MagicMock()
    ccgx_data = {'grid': {}, 'battery': {}, 'solarcharger': {}, 'settings': {}, 'system': {}}
    ingestion = CcgxIngestion(logger, ccgx_data)
    bindings = build_ccgx_topic_bindings("N/vrm", ingestion)
    by_topic = dict(bindings)

    msg = SimpleNamespace(
        topic="N/vrm/battery/0/Soc",
        payload=json.dumps({"value": 77}).encode("utf-8"),
    )
    by_topic["N/vrm/battery/+/Soc"](None, None, msg)
    assert ccgx_data["battery"]["soc"] == 77


def test_external_topic_bindings_disabled():
    handlers = ExternalControlHandlers(MagicMock(), {})
    config = {
        "external_control_settings": {
            "allow_external_control_over_mqtt": 0,
            "mqtt_external_control_topics": {
                "charge_battery_to_SOC": "iobroker/charge",
            },
        }
    }
    assert build_external_topic_bindings(config, handlers) == []


def test_external_topic_bindings_skips_none():
    handlers = ExternalControlHandlers(MagicMock(), {})
    config = {
        "external_control_settings": {
            "allow_external_control_over_mqtt": 1,
            "mqtt_external_control_topics": {
                "charge_battery_to_SOC": "iobroker/charge",
                "activate_top_balancing_mode": "none",
                "deactivate_discharge": "iobroker/no_dis",
                "deactivate_charge": "none",
                "reboot_ess_controller": "none",
            },
        }
    }
    bindings = build_external_topic_bindings(config, handlers)
    topics = [t for t, _ in bindings]
    assert topics == ["iobroker/charge", "iobroker/no_dis"]


def test_register_topic_callbacks_calls_message_callback_add():
    client = MagicMock()
    cb1 = MagicMock()
    cb2 = MagicMock()
    register_topic_callbacks(client, [("topic/a", cb1), ("topic/b", cb2)])
    assert client.message_callback_add.call_count == 2
    client.message_callback_add.assert_any_call("topic/a", cb1)
    client.message_callback_add.assert_any_call("topic/b", cb2)
