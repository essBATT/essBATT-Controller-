# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""MQTT connection lifecycle, subscriptions, and topic-callback registration.

Owns the paho client and connection state so the controller can stay a
composition root + control cycle without MQTT plumbing noise.

Reconnect behaviour
-------------------
``loop_start()`` runs paho's network thread with auto-reconnect enabled.
On every successful CONNACK (first connect *and* reconnect) we re-subscribe
and re-register topic handlers (required with clean_session=True), then
invoke an optional ``on_connected`` callback so the app can send keepalive
and start timers.
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


# Simple connection state labels (single source of truth)
STATE_DISCONNECTED = 'disconnected'
STATE_CONNECTING = 'connecting'
STATE_CONNECTED = 'connected'


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
        on_connected=None,
        time_fn=None,
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
            on_connected: optional callback(is_reconnect: bool) after subscribe
            time_fn: clock for timeouts / disconnect duration (default time.time)
        """
        self.logger = logger
        self.config = config
        self.ingestion = ingestion
        self.external_handlers = external_handlers
        self.host = host
        self._client_factory = client_factory or mqtt.Client
        self._sleep_fn = sleep_fn or time.sleep
        self._time_fn = time_fn or time.time
        self._on_connected = on_connected

        self.client = None
        self.state = STATE_DISCONNECTED
        self._successful_connects = 0
        self._handlers_registered = False
        self.disconnected_since = None  # monotonic-ish wall time of last disconnect
        self._stopping = False

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    @property
    def is_connected(self):
        """True while the last connect/disconnect callbacks report connected."""
        return self.state == STATE_CONNECTED

    @property
    def connection_ok(self):
        """Backward-compatible alias for is_connected."""
        return self.is_connected

    @connection_ok.setter
    def connection_ok(self, value):
        """Allow tests/legacy code to flip connection flag directly."""
        if value:
            self.state = STATE_CONNECTED
            self.disconnected_since = None
        else:
            self.state = STATE_DISCONNECTED
            if self.disconnected_since is None:
                self.disconnected_since = self._time_fn()

    @property
    def disconnected(self):
        """True when not connected (includes connecting)."""
        return self.state != STATE_CONNECTED

    def disconnect_duration_s(self):
        """Seconds since last disconnect event, or 0 if connected / never disconnected mid-run."""
        if self.is_connected or self.disconnected_since is None:
            return 0.0
        return max(0.0, self._time_fn() - self.disconnected_since)

    def update_config(self, config):
        """Replace config reference (e.g. after online reload)."""
        self.config = config

    def set_on_connected(self, callback):
        """Set/replace the post-connect application callback."""
        self._on_connected = callback

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------
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
        # Explicit reconnect policy (paho defaults are similar; keep visible)
        if hasattr(self.client, 'reconnect_delay_set'):
            self.client.reconnect_delay_set(min_delay=1, max_delay=120)
        self.client.on_connect = self.on_connect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
        self._handlers_registered = False
        self._successful_connects = 0
        self._stopping = False
        return self.client

    def connect(self):
        """Connect to the broker and start the network loop (non-blocking)."""
        if self.client is None:
            self.setup()
        self.state = STATE_CONNECTING
        port = self.config['mqtt_server_COM_port']
        self.client.connect(
            self.host,
            port=port,
            keepalive=constants.MQTT_BROKER_PROTOCOL_KEEPALIVE_S,
            bind_address="",
        )
        self.logger.info(
            "essBATT controller: Try to connect to MQTT Server: "
            + self.host + " on port " + str(port)
            + " with protocol keepalive of "
            + str(constants.MQTT_BROKER_PROTOCOL_KEEPALIVE_S) + "s"
        )
        self.client.loop_start()

    def wait_until_connected(self, timeout=None):
        """Block until on_connect reports success, or timeout.

        Args:
            timeout: seconds to wait (default MQTT_INITIAL_CONNECT_TIMEOUT_S)

        Returns:
            True if connected, False on timeout
        """
        if timeout is None:
            timeout = constants.MQTT_INITIAL_CONNECT_TIMEOUT_S
        deadline = self._time_fn() + float(timeout)
        while not self.is_connected:
            if self._time_fn() >= deadline:
                self.logger.error(
                    "MQTT initial connect timed out after "
                    + str(timeout) + "s (no successful CONNACK)."
                )
                return False
            self.logger.info("Waiting for MQTT server connection...")
            self._sleep_fn(1)
        return True

    def subscribe_and_register_handlers(self):
        """Subscribe to Victron + external topics and register topic callbacks.

        Safe to call on every CONNACK: subscriptions must be renewed after
        reconnect with clean_session=True; topic callbacks are registered once.

        Returns:
            subscribe() result code (or None if no client)
        """
        if self.client is None:
            return None

        base_path_str = "N/" + self.config['vrm_id']
        # Subscription to selected service_types reduces on_message load when
        # keepalive_get_all_topics is 1; selection is in VictronOutput.send_keepalive.
        subscription_list = build_subscription_list(base_path_str, self.config)
        result, _mid = self.client.subscribe(subscription_list)
        self.logger.info("MQTT subscription function return value: " + str(result))

        if not self._handlers_registered:
            bindings = build_ccgx_topic_bindings(base_path_str, self.ingestion)
            bindings.extend(
                build_external_topic_bindings(self.config, self.external_handlers)
            )
            register_topic_callbacks(self.client, bindings)
            self._handlers_registered = True
            self.logger.debug(
                "Registered " + str(len(bindings)) + " MQTT topic callbacks."
            )
        return result

    def start(self, connect_timeout=None):
        """Full first-connect sequence: setup → connect → wait (subscribe in on_connect).

        Raises:
            OSError: network/connect failure from paho connect()
            TimeoutError: no successful CONNACK within connect_timeout
        """
        self.setup()
        self.connect()
        if not self.wait_until_connected(timeout=connect_timeout):
            self.stop()
            raise TimeoutError(
                "MQTT broker did not accept the connection within the timeout"
            )

    def stop(self):
        """Graceful MQTT shutdown: disconnect + stop network loop."""
        self._stopping = True
        if self.client is None:
            self.state = STATE_DISCONNECTED
            return
        try:
            self.client.disconnect()
        except Exception as e:
            self.logger.debug("MQTT disconnect during stop: " + str(e))
        try:
            self.client.loop_stop()
        except Exception as e:
            self.logger.debug("MQTT loop_stop during stop: " + str(e))
        self.state = STATE_DISCONNECTED

    # ------------------------------------------------------------------
    # paho lifecycle callbacks
    # ------------------------------------------------------------------
    def on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self.logger.error(
                "Failed to connect to MQTT Server with result code " + str(rc)
            )
            self.state = STATE_DISCONNECTED
            if self.disconnected_since is None:
                self.disconnected_since = self._time_fn()
            return

        is_reconnect = self._successful_connects > 0
        self._successful_connects += 1
        self.state = STATE_CONNECTED
        self.disconnected_since = None

        session_present = None
        if isinstance(flags, dict):
            session_present = flags.get('session present')

        if is_reconnect:
            self.logger.info(
                "MQTT reconnected (result code " + str(rc)
                + ", session_present=" + str(session_present) + ")"
            )
        else:
            self.logger.info(
                "Success: Connected to MQTT Server with result code " + str(rc)
                + ("" if session_present is None else
                   ", session_present=" + str(session_present))
            )

        # Always re-subscribe after CONNACK (clean_session drops subscriptions)
        try:
            self.subscribe_and_register_handlers()
        except Exception:
            self.logger.exception("MQTT subscribe/register after connect failed")

        if self._on_connected is not None:
            try:
                self._on_connected(is_reconnect=is_reconnect)
            except Exception:
                self.logger.exception("on_connected application callback failed")

    def on_disconnect(self, client, userdata, rc):
        # rc == 0: client-requested disconnect; otherwise unexpected
        if self._stopping or rc == 0:
            self.logger.info("MQTT disconnected (rc=" + str(rc) + ").")
        else:
            self.logger.warning(
                "MQTT server disconnected unexpectedly. Reason code: " + str(rc)
                + ". paho will attempt auto-reconnect."
            )
        self.state = STATE_DISCONNECTED
        if self.disconnected_since is None:
            self.disconnected_since = self._time_fn()

    def on_message(self, client, userdata, msg):
        self.logger.debug(
            "New unused (!!!) MQTT message: " + msg.topic + " " + str(msg.payload)
        )

    def on_subscribe(self, client, userdata, mid, granted_qos):
        self.logger.debug("MQTT on_subscribe function called!")
