# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Pure helpers for MQTT subscription lists and Victron keepalive topics.

No MQTT client dependency — only builds topic structures from config.
"""


def build_subscription_list(base_path_str, config):
    """Build MQTT subscription list for Victron + optional external control topics.

    Args:
        base_path_str: e.g. "N/<vrm_id>"
        config: ess_config data (needs external_control_settings when enabled)

    Returns:
        list of (topic, qos) tuples
    """
    subscription_list = [
        (base_path_str + "/battery/#", 1),
        (base_path_str + "/grid/#", 1),
        (base_path_str + "/solarcharger/#", 1),
        (base_path_str + "/system/+/Ac/Consumption/#", 1),
        (base_path_str + "/settings/#", 1),
        (base_path_str + "/vebus/+/Mode", 1),
    ]

    ext = config.get('external_control_settings', {})
    if ext.get('allow_external_control_over_mqtt') == 1:
        topics = ext.get('mqtt_external_control_topics', {})
        for key in (
            'charge_battery_to_SOC',
            'activate_top_balancing_mode',
            'deactivate_discharge',
            'deactivate_charge',
            'reboot_ess_controller',
        ):
            topic = topics.get(key)
            if topic and topic != "none":
                subscription_list.append((topic, 1))

    return subscription_list


def build_keepalive_selected_topics():
    """Return the list of Victron topic filters requested on selective keepalive."""
    return [
        "battery/+/Dc/0/#",
        "battery/+/Soc",
        "battery/+/System/MaxCellVoltage",
        "battery/+/System/MinCellVoltage",
        "grid/+/Ac/Power",
        "grid/+/Ac/L1/Power",
        "grid/+/Ac/L1/Current",
        "grid/+/Ac/L2/Power",
        "grid/+/Ac/L2/Current",
        "grid/+/Ac/L3/Power",
        "grid/+/Ac/L3/Current",
        "system/+/Ac/Consumption/#",
        "solarcharger/+/Yield/Power",
        "solarcharger/+/Dc/0/#",
        "+/+/ProductId",
        "settings/+/Settings/CGwacs/#",
        "settings/+/Settings/SystemSetup/#",
        "vebus/+/Mode",
    ]


def build_keepalive_publish(vrm_id, keepalive_get_all_topics):
    """Build topic and payload for a Cerbo keepalive publish.

    Args:
        vrm_id: Victron VRM portal id
        keepalive_get_all_topics: 0 = selected topics JSON list, 1 = empty serial request

    Returns:
        (topic_string, payload_string) or None if mode invalid
    """
    if keepalive_get_all_topics == 1:
        return ("R/" + vrm_id + "/system/0/Serial", "")
    if keepalive_get_all_topics == 0:
        import json
        topics_list = build_keepalive_selected_topics()
        return ("R/" + vrm_id + "/keepalive", json.dumps(topics_list))
    return None
