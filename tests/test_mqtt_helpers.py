"""Tests for mqtt_helpers (Step 6 of modularization)."""

import json

import pytest

from mqtt_helpers import (
    build_subscription_list,
    build_keepalive_selected_topics,
    build_keepalive_publish,
)


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
