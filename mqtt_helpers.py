# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Pure helpers for MQTT subscription lists, Victron keepalive topics,
and topic→callback registration tables.

No hard dependency on a live MQTT client for the builders — only
``register_topic_callbacks`` talks to the client.
"""


def is_usable_mqtt_topic(topic):
    """False for missing, ``none``, or placeholder ``EXAMPLE: ...`` topics."""
    if topic is None:
        return False
    text = str(topic).strip()
    if text == '' or text == 'none':
        return False
    if text.upper().startswith('EXAMPLE:'):
        return False
    return True


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
            if is_usable_mqtt_topic(topic):
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


def _paho_msg_callback(handler):
    """Adapt a one-arg handler(msg) to paho's (client, userdata, msg) signature."""
    def callback(client, userdata, msg):
        handler(msg)
    return callback


# Config topic key → ExternalControlHandlers method name
EXTERNAL_TOPIC_HANDLER_KEYS = (
    ('charge_battery_to_SOC', 'on_charge_to_soc'),
    ('activate_top_balancing_mode', 'on_balancing'),
    ('deactivate_discharge', 'on_deactivate_discharge'),
    ('deactivate_charge', 'on_deactivate_charge'),
    ('reboot_ess_controller', 'on_reboot'),
)


def build_ccgx_topic_bindings(base_path_str, ingestion):
    """Build (topic, paho_callback) pairs for Victron CCGX data topics.

    Args:
        base_path_str: e.g. "N/<vrm_id>"
        ingestion: CcgxIngestion instance (handlers take msg only)

    Returns:
        list of (topic_string, callback) suitable for register_topic_callbacks
    """
    relative = [
        ("/grid/+/Ac/Power", ingestion.on_grid_power),
        ("/grid/+/Ac/L1/Power", ingestion.on_L1_power),
        ("/grid/+/Ac/L1/Current", ingestion.on_L1_current),
        ("/grid/+/Ac/L2/Power", ingestion.on_L2_power),
        ("/grid/+/Ac/L2/Current", ingestion.on_L2_current),
        ("/grid/+/Ac/L3/Power", ingestion.on_L3_power),
        ("/grid/+/Ac/L3/Current", ingestion.on_L3_current),
        ("/battery/+/Soc", ingestion.on_battery_soc),
        ("/battery/+/System/MaxCellVoltage", ingestion.on_battery_maxcellvoltage),
        ("/battery/+/System/MinCellVoltage", ingestion.on_battery_mincellvoltage),
        ("/battery/+/Dc/0/Temperature", ingestion.on_battery_temp),
        ("/battery/+/Dc/0/Current", ingestion.on_battery_current),
        ("/battery/+/Dc/0/Power", ingestion.on_battery_power),
        ("/battery/+/Dc/0/Voltage", ingestion.on_battery_voltage),
        ("/solarcharger/+/Yield/Power", ingestion.on_solarcharger_power),
        ("/solarcharger/+/Dc/0/#", ingestion.on_solarcharger_dc_values),
        ("/system/+/Ac/Consumption/#", ingestion.on_system_ac_consumption),
        ("/settings/+/Settings/CGwacs/#", ingestion.on_settings_cgwacs),
        ("/settings/+/Settings/SystemSetup/#", ingestion.on_settings_system_setup),
        ("/vebus/+/Mode", ingestion.on_multis_switch_mode),
    ]
    return [
        (base_path_str + path, _paho_msg_callback(handler))
        for path, handler in relative
    ]


def build_external_topic_bindings(config, external_handlers):
    """Build (topic, paho_callback) pairs for configured external control topics.

    Args:
        config: ess_config data
        external_handlers: ExternalControlHandlers instance

    Returns:
        list of (topic_string, callback); empty if external control disabled
    """
    bindings = []
    ext = config.get('external_control_settings', {})
    if ext.get('allow_external_control_over_mqtt') != 1:
        return bindings

    topics = ext.get('mqtt_external_control_topics', {})
    for topic_key, handler_name in EXTERNAL_TOPIC_HANDLER_KEYS:
        topic = topics.get(topic_key)
        if is_usable_mqtt_topic(topic):
            handler = getattr(external_handlers, handler_name)
            bindings.append((topic, _paho_msg_callback(handler)))
    return bindings


def register_topic_callbacks(mqtt_client, bindings):
    """Register (topic, callback) pairs via mqtt_client.message_callback_add."""
    for topic, callback in bindings:
        mqtt_client.message_callback_add(topic, callback)
