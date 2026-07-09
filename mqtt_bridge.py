# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""MQTT connection lifecycle, subscriptions, and topic-callback registration.

Owns the paho client and connection state so the controller can stay a
composition root + control cycle without MQTT plumbing noise.
"""

import time

import paho.mqtt.client as mqtt

import constants
from mqtt_helpers import (
    build_subscription_list,
    build_ccgx_topic_bindings,
    build_external_topic_bindings,
    register_topic_callbacks,
)


class MqttBridge:
    """Connects to the local MQTT broker, subscribes, and wires topic handlers.

    Dependencies (config, ingestion, external handlers) are injected so unit
    tests can run without a real broker.
    """

    def __init__(
        self,
        logger,
        config,
        ingestion,
        external_handlers,
        host='localhost',
        client_factory=None,
        sleep_fn=None,
    ):
        """
        Args:
            logger: logger instance
            config: ess_config dict (mqtt credentials, vrm_id, external topics)
            ingestion: CcgxIngestion instance for Victron topic handlers
            external_handlers: ExternalControlHandlers for external topics
            host: MQTT broker host (default localhost)
            client_factory: callable returning a paho-like client (default mqtt.Client)
            sleep_fn: sleep used while waiting for connection (default time.sleep)
        """
        self.logger = logger
        self.config = config
        self.ingestion = ingestion
        self.external_handlers = external_handlers
        self.host = host
        self._client_factory = client_factory or mqtt.Client
        self._sleep_fn = sleep_fn or time.sleep

        self.client = None
        self.connection_ok = False
        self.disconnected = True

    @property
    def is_connected(self):
        """True while the last connect/disconnect callbacks report connected."""
        return self.connection_ok

    def update_config(self, config):
        """Replace config reference (e.g. after online reload)."""
        self.config = config

    def setup(self):
        """Create MQTT client, set credentials and lifecycle callbacks.

        Returns:
            the created client
        """
        self.client = self._client_factory()
        self.client.username_pw_set(
            username=self.config['mqtt_username'],
            password=self.config['mqtt_password'],
        )
        self.client.on_connect = self.on_connect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
        return self.client

    def connect(self):
        """Connect to the broker and start the network loop (non-blocking)."""
        if self.client is None:
            self.setup()
        port = self.config['mqtt_server_COM_port']
        self.client.connect(
            self.host,
            port=port,
            keepalive=constants.MQTT_SERVER_TIMEOUT_TIMESPAN,
            bind_address="",
        )
        self.logger.info(
            "essBATT controller: Try to connect to MQTT Server: "
            + self.host + " on port " + str(port)
            + " with timeout of " + str(constants.MQTT_SERVER_TIMEOUT_TIMESPAN) + "s"
        )
        self.client.loop_start()

    def wait_until_connected(self):
        """Block until on_connect reports success."""
        while not self.connection_ok:
            self.logger.info("Waiting for MQTT server connection...")
            self._sleep_fn(1)

    def subscribe_and_register_handlers(self):
        """Subscribe to Victron + external topics and register topic callbacks.

        Returns:
            subscribe() result code (or None if no client)
        """
        if self.client is None:
            return None

        base_path_str = "N/" + self.config['vrm_id']
        # Subscribtion to selected service_types reduces on_message load when
        # keepalive_get_all_topics is 1; selection is in VictronOutput.send_keepalive.
        subscription_list = build_subscription_list(base_path_str, self.config)
        result, _mid = self.client.subscribe(subscription_list)
        self.logger.info("MQTT subscribtion function return value: " + str(result))

        bindings = build_ccgx_topic_bindings(base_path_str, self.ingestion)
        bindings.extend(
            build_external_topic_bindings(self.config, self.external_handlers)
        )
        register_topic_callbacks(self.client, bindings)
        return result

    def start(self):
        """Full connect sequence: setup → connect → wait → subscribe/register.

        Raises:
            OSError on network/connect failure (caller may log and stop)
        """
        self.setup()
        self.connect()
        self.wait_until_connected()
        self.subscribe_and_register_handlers()

    def stop(self):
        """Stop the MQTT network loop if a client exists."""
        if self.client is not None:
            self.client.loop_stop()

    # ------------------------------------------------------------------
    # paho lifecycle callbacks
    # ------------------------------------------------------------------
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.logger.info(
                "Success: Connected to MQTT Server with result code " + str(rc)
            )
            self.connection_ok = True
            self.disconnected = False
        else:
            self.logger.error(
                "Failed to connected to MQTT Server with result code " + str(rc)
            )
            self.connection_ok = False

    def on_disconnect(self, client, userdata, rc):
        self.logger.warning("MQTT server disconnected. Reason: " + str(rc))
        self.connection_ok = False
        self.disconnected = True

    def on_message(self, client, userdata, msg):
        self.logger.debug(
            "New unused (!!!) MQTT message: " + msg.topic + " " + str(msg.payload)
        )

    def on_subscribe(self, client, userdata, mid, granted_qos):
        self.logger.debug("MQTT on_subscribe function called!")
