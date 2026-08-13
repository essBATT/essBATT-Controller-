# This is free and unencumbered software released into the public domain.

# Anyone is free to copy, modify, publish, use, compile, sell, or
# distribute this software, either in source code form or as a compiled
# binary, for any purpose, commercial or non-commercial, and by any
# means.

# In jurisdictions that recognize copyright laws, the author or authors
# of this software dedicate any and all copyright interest in the
# software to the public domain. We make this dedication for the benefit
# of the public at large and to the detriment of our heirs and
# successors. We intend this dedication to be an overt act of
# relinquishment in perpetuity of all present and future rights to this
# software under copyright law.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

# For more information, please refer to <http://unlicense.org/>

"""essBATT controller — composition root and ESS control cycle.

Read this file top-down:
  1. ``essBATT_controller.__init__``  — wire modules together
  2. ``run`` / ``stop``               — process lifetime
  3. ``ess_control_cycle_update``     — one control tick (the main algorithm)

Everything after the "INTERNALS" banner is plumbing (signals, timers,
MQTT reconnect hooks, safe-state, config reload). Domain logic lives in
the imported modules, not here.
"""

import copy
import json
import logging
from logging.handlers import RotatingFileHandler
import signal
import time

import constants
from utils import RepeatedTimer
from config_manager import ConfigManager
from battery_protection import BatteryProtector
from state_machine import StateMachine
from data_mapper import CcgxDataMapper
from setpoint_control import SetpointCalculator
from ccgx_ingestion import CcgxIngestion
from victron_output import VictronOutput
from external_control import ExternalControlHandlers
from mqtt_bridge import MqttBridge


class essBATT_controller:
    """Composition root + ESS control cycle.

    Process lifetime follows ``self._running`` (signals / intentional stop),
    not momentary MQTT connectivity — short broker interruptions are
    tolerated while paho auto-reconnects and resubscribes.
    """

    # ==================================================================
    # Composition
    # ==================================================================

    def __init__(self, logger):
        self.logger = logger
        self._running = False
        self._timers_started = False
        self._last_disconnect_warn_at = None
        self._incomplete_data_since = None
        self._incomplete_data_safe_state_active = False
        self.ess_internal_state = {}
        self.ess_config_data = {}
        self.ess_setvalue_list = {}
        self.ess_controller_state = {}
        self.ess_external_input = {}
        self.ess_config_data_loaded_correctly = False
        self.ess_setvalue_list_loaded_correctly = False
        self.ess_controller_state_loaded_correctly = False
        self.CCGX_data = {
            'grid': {}, 'battery': {}, 'solarcharger': {},
            'settings': {}, 'system': {},
        }

        self.config_manager = ConfigManager(self.logger, debug=constants.DEBUGGING_ON)

        self.ess_config_data = self.config_manager.load_config()
        self.ess_config_data_loaded_correctly = self.config_manager.config_data_loaded_correctly
        if not self.ess_config_data_loaded_correctly:
            return

        self.ess_setvalue_list = self.config_manager.load_setvalue_list()
        self.ess_setvalue_list_loaded_correctly = self.config_manager.setvalue_list_loaded_correctly
        if not self.ess_setvalue_list_loaded_correctly:
            return

        self.ess_controller_state = self.config_manager.load_state()
        self.ess_controller_state_loaded_correctly = self.config_manager.controller_state_loaded_correctly
        if not self.ess_controller_state_loaded_correctly:
            self.ess_controller_state = {}

        self._ess_controller_state_snapshot = copy.deepcopy(self.ess_controller_state)
        self.write_base_path = 'W/' + self.ess_config_data.get('vrm_id', 'unknown') + '/'

        self.logger.setLevel(
            constants.LOGLEVEL_NAME_TO_NUMBER[self.ess_config_data.get('debug_level', 'INFO')]
        )
        self.logger.info('Effective logger level: ' + str(self.logger.getEffectiveLevel()))

        self.temporary_script_states = self.config_manager.create_temporary_script_states(
            self.ess_config_data
        )

        self.battery_protector = BatteryProtector(
            self.ess_config_data,
            self.logger,
            self.temporary_script_states,
            self.ess_controller_state,
            self.ess_external_input,
        )
        self.state_machine = StateMachine(
            self.ess_config_data,
            self.logger,
            self.ess_controller_state,
            self.temporary_script_states,
            self.ess_external_input,
        )
        self.data_mapper = CcgxDataMapper(self.logger)
        self.setpoint_calculator = SetpointCalculator(
            self.ess_config_data,
            self.logger,
            self.ess_controller_state,
        )

        self.ingestion = CcgxIngestion(self.logger, self.CCGX_data)
        self.victron_output = VictronOutput(
            self.logger,
            mqtt_client=None,
            ccgx_data=self.CCGX_data,
            setvalue_list=self.ess_setvalue_list,
            write_base_path=self.write_base_path,
        )
        self.external_handlers = ExternalControlHandlers(
            self.logger,
            self.ess_external_input,
            reboot_callback=self.reboot_ess_controller_script,
        )
        self.mqtt_bridge = MqttBridge(
            self.logger,
            self.ess_config_data,
            self.ingestion,
            self.external_handlers,
            on_connected=self._on_mqtt_connected,
        )

        # Started after first successful MQTT connect (see _on_mqtt_connected)
        self.rt_keep_alive_obj = None
        self.rt_ess_control_update_obj = None
        self.rt_print_status_obj = None
        self.rt_controller_heartbeat_obj = None

    # ==================================================================
    # MAIN LOGIC — process lifetime
    # ==================================================================

    def run(self):
        """Connect MQTT, then keep the process alive until stop/signal.

        Short MQTT drops do *not* end the process: paho reconnects, the bridge
        resubscribes, and ``ess_control_cycle_update`` resumes when connected.
        Timers + first keepalive start from ``_on_mqtt_connected``.
        """
        self._running = True
        self._install_signal_handlers()
        try:
            self.mqtt_bridge.start(
                connect_timeout=constants.MQTT_INITIAL_CONNECT_TIMEOUT_S
            )

            ####### MAIN CONTROL LOOP #############
            while self._running:
                # While we are sleeping in this loop the ess_control_cycle_update() function is called periodically (how often defined in ess_config.json) by the timer
                self._maybe_warn_long_disconnect()
                time.sleep(1)
            #######################################

        # TimeoutError is an OSError subclass — must come first
        except TimeoutError as e:
            self.logger.error('MQTT connection failed (timeout): ' + str(e))
            self._running = False
        except OSError as e:
            self.logger.error('MQTT connection failed (network/OS): ' + str(e))
            self._running = False
        except Exception:
            self.logger.exception('Unexpected error during MQTT setup / main loop')
            self._running = False

    def stop(self):
        """Stop background timers and MQTT (used on shutdown)."""
        self._running = False
        for timer_attr in (
            'rt_keep_alive_obj',
            'rt_ess_control_update_obj',
            'rt_print_status_obj',
            'rt_controller_heartbeat_obj',
        ):
            timer = getattr(self, timer_attr, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    self.logger.exception('Error stopping timer ' + timer_attr)
        if hasattr(self, 'mqtt_bridge') and self.mqtt_bridge is not None:
            self.mqtt_bridge.stop()
        self._timers_started = False

    # ==================================================================
    # MAIN LOGIC — ESS control cycle (called by timer while running)
    # ==================================================================

    def ess_control_cycle_update(self):
        """One control tick: map inputs → state/limits/setpoints → publish.

        Skips work while MQTT is down. Domain exceptions are logged and
        converted into a software safe state (zero charge/discharge limits)
        so the timer thread keeps living.
        """
        if not self.mqtt_bridge.is_connected:
            return

        try:
            # --- optional online config reload ---
            if self.ess_config_data.get('check_ess_config_changes_while_running', 0) == 1:
                self.reload_config_while_running()

            # --- read latest CCGX snapshot (filled by MQTT ingestion) ---
            local_values = {}
            self.data_mapper.read_values_to_local_dict(self.CCGX_data, local_values)

            # --- external deactivate flags (charge / discharge forbid) ---
            if self.ess_external_input.get('deactivate_charge', {}).get('activated'):
                local_values['deactivate_charge_limit'] = 0.0
            if self.ess_external_input.get('deactivate_discharge', {}).get('activated'):
                local_values['deactivate_discharge_limit'] = 0.0

            # --- operating mode (normal / charge_to_SOC / balancing / …) ---
            self.state_machine.update(local_values)

            # --- static ESS Mode 2 settings (always, when connected) ---
            mode2 = self.ess_config_data.get('ess_mode_2_settings', {})
            self.victron_output.set_ccgx_value(
                set_val_name_str='MaxFeedInPower',
                set_val=mode2.get('max_system_grid_feed_in_power_2706', 0),
                only_set_if_deviation_to_current_setting=True,
            )
            self.victron_output.set_ccgx_value(
                set_val_name_str='OvervoltageFeedIn',
                set_val=mode2.get('feed_excess_dc_coupled_pv_into_grid_2707', 0),
                only_set_if_deviation_to_current_setting=True,
            )
            self.victron_output.set_ccgx_value(
                set_val_name_str='PreventFeedback',
                set_val=mode2.get('feed_excess_ac_coupled_pv_into_grid_2708', 0),
                only_set_if_deviation_to_current_setting=True,
            )

            # --- dynamic path only when all required CCGX fields are present ---
            if local_values.get('all_CCGX_values_available', False):
                self._clear_incomplete_data_timeout()
                self.battery_protector.calculate_dis_charge_limits(local_values)

                self.state_machine.multis_switch_handling(local_values)
                if 'multis_switch_position' in local_values:
                    self.victron_output.set_multis_switch_mode(
                        switch_position=local_values['multis_switch_position']
                    )

                self.setpoint_calculator.calculate_ac_power_setpoint(local_values)

                self.victron_output.set_ccgx_value(
                    set_val_name_str='AcPowerSetPoint',
                    set_val=local_values['AcPowerSetPoint'],
                    only_set_if_deviation_to_current_setting=True,
                )
                self.victron_output.set_ccgx_value(
                    set_val_name_str='MaxChargeCurrent',
                    set_val=local_values['charge_current_limit_final'],
                    only_set_if_deviation_to_current_setting=True,
                )
                self.victron_output.set_ccgx_value(
                    set_val_name_str='MaxDischargePower',
                    set_val=local_values['discharge_power_limit_final'],
                    only_set_if_deviation_to_current_setting=True,
                )
            else:
                # Missing meter/charger/battery fields: do not leave last
                # Venus setpoints in place forever.
                self._handle_incomplete_ccgx_data()

            # --- persist controller state if it changed this tick ---
            self.cleanup_after_control_loop()

        except Exception:
            self.logger.exception(
                'Unhandled exception in control cycle — applying software safe state'
            )
            self._apply_software_safe_state(reason='control cycle exception')

    # ==================================================================
    # INTERNALS — plumbing only (signals, timers, MQTT hooks, safety)
    # Skip this section unless you are changing lifecycle / resilience.
    # ==================================================================

    def _install_signal_handlers(self):
        """Map SIGTERM/SIGINT to a clean ``_running = False`` exit."""
        def _handler(signum, frame):
            try:
                name = signal.Signals(signum).name
            except Exception:
                name = str(signum)
            self.logger.warning(
                'Received signal ' + name + ' — shutting down cleanly.'
            )
            self._running = False

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError) as e:
                self.logger.debug(
                    'Could not install handler for ' + str(sig) + ': ' + str(e)
                )

    def _start_timers(self):
        """Start background timers once MQTT is available."""
        if self._timers_started:
            return
        self.rt_keep_alive_obj = RepeatedTimer(
            constants.CERBO_KEEPALIVE_LENGTH, self.send_keepalive_to_cerbo
        )
        self.rt_ess_control_update_obj = RepeatedTimer(
            self.ess_config_data.get('control_update_rate', 2.0),
            self.ess_control_cycle_update,
        )
        self.rt_print_status_obj = RepeatedTimer(
            self.ess_config_data.get('script_alive_logging_interval', 86400),
            self.print_alive_status_to_logger,
        )
        heartbeat_topic = self._heartbeat_topic()
        if heartbeat_topic is not None:
            self.rt_controller_heartbeat_obj = RepeatedTimer(
                self._heartbeat_interval_s(),
                self.send_controller_heartbeat,
            )
        else:
            self.rt_controller_heartbeat_obj = None
            self.logger.info(
                'Controller heartbeat disabled '
                '(controller_heartbeat_topic is none/empty).'
            )
        self._timers_started = True
        self.logger.info(
            'Background timers started (control cycle, keepalive, alive log'
            + (', heartbeat' if heartbeat_topic is not None else '')
            + ').'
        )

    def _on_mqtt_connected(self, is_reconnect=False):
        """After every successful CONNACK + subscribe: client, keepalive, timers."""
        if self.mqtt_bridge.client is not None:
            self.victron_output.set_mqtt_client(self.mqtt_bridge.client)
        self.send_keepalive_to_cerbo()
        self.send_controller_heartbeat()
        if not self._timers_started:
            self._start_timers()
        if is_reconnect:
            self.logger.info(
                'MQTT session restored: resubscribed, keepalive sent, control continues.'
            )

    def _maybe_warn_long_disconnect(self):
        """Periodic warning while the broker is unreachable."""
        if self.mqtt_bridge.is_connected:
            self._last_disconnect_warn_at = None
            return
        duration = self.mqtt_bridge.disconnect_duration_s()
        if duration < constants.MQTT_DISCONNECT_WARN_AFTER_S:
            return
        now = time.time()
        if (
            self._last_disconnect_warn_at is not None
            and (now - self._last_disconnect_warn_at)
            < constants.MQTT_DISCONNECT_WARN_INTERVAL_S
        ):
            return
        self._last_disconnect_warn_at = now
        self.logger.warning(
            'MQTT still disconnected for '
            + str(int(duration))
            + 's — control cycle paused; waiting for paho auto-reconnect. '
            + 'Venus keeps last setpoints until we reassert them.'
        )

    def _incomplete_data_timeout_s(self):
        return float(
            self.ess_config_data.get(
                'incomplete_data_safe_state_timeout_s',
                constants.DEFAULT_INCOMPLETE_DATA_SAFE_STATE_TIMEOUT_S,
            )
        )

    def _clear_incomplete_data_timeout(self):
        if self._incomplete_data_safe_state_active:
            self.logger.info(
                'CCGX data complete again — leaving incomplete-data software safe state.'
            )
        self._incomplete_data_since = None
        self._incomplete_data_safe_state_active = False

    def _handle_incomplete_ccgx_data(self):
        """After a timeout, force charge/discharge limits to 0.

        Brief dropouts (grid meter, solarcharger) stay idle so Venus keeps
        the last setpoints. A sustained gap must not leave those setpoints.
        """
        now = time.time()
        if self._incomplete_data_since is None:
            self._incomplete_data_since = now
            self.logger.info(
                'CCGX data incomplete — starting '
                + str(self._incomplete_data_timeout_s())
                + 's software safe-state timeout.'
            )
            return
        elapsed = now - self._incomplete_data_since
        if elapsed < self._incomplete_data_timeout_s():
            return
        reason = 'CCGX data incomplete for ' + str(int(elapsed)) + 's'
        if not self._incomplete_data_safe_state_active:
            self._incomplete_data_safe_state_active = True
            self._apply_software_safe_state(reason=reason)
        else:
            self._apply_software_safe_state(reason=reason, quiet=True)

    def _apply_software_safe_state(self, reason='', quiet=False):
        """Best-effort: force charge/discharge limits to 0 while MQTT is up."""
        if not self.mqtt_bridge.is_connected:
            self.logger.error(
                'Software safe state requested (' + reason
                + ') but MQTT is down — cannot publish safe limits.'
            )
            return
        log_fn = self.logger.debug if quiet else self.logger.error
        log_fn(
            'Applying software safe state (' + reason
            + '): MaxChargeCurrent=0, MaxDischargePower=0'
        )
        try:
            self.victron_output.set_ccgx_value(
                set_val_name_str='MaxChargeCurrent',
                set_val=0,
                only_set_if_deviation_to_current_setting=False,
            )
            self.victron_output.set_ccgx_value(
                set_val_name_str='MaxDischargePower',
                set_val=0,
                only_set_if_deviation_to_current_setting=False,
            )
        except Exception:
            self.logger.exception('Failed to publish software safe state limits')

    def cleanup_after_control_loop(self):
        """Persist controller state when it diverged from the last snapshot."""
        if self.ess_controller_state != self._ess_controller_state_snapshot:
            self.config_manager.save_state_if_changed(
                self.ess_controller_state, self._ess_controller_state_snapshot
            )
            self._ess_controller_state_snapshot = copy.deepcopy(self.ess_controller_state)

    def send_keepalive_to_cerbo(self):
        """Timer entry: Cerbo/Venus dbus-mqtt keepalive while connected."""
        if not self.mqtt_bridge.is_connected:
            return
        self.victron_output.send_keepalive(
            self.ess_config_data.get('vrm_id'),
            self.ess_config_data.get('keepalive_get_all_topics', 0),
        )

    def _heartbeat_topic(self):
        """Return configured heartbeat topic, or None if disabled."""
        topic = self.ess_config_data.get(
            'controller_heartbeat_topic',
            constants.DEFAULT_CONTROLLER_HEARTBEAT_TOPIC,
        )
        if topic is None or topic == '' or topic == 'none':
            return None
        return topic

    def _heartbeat_interval_s(self):
        """Heartbeat publish interval in seconds."""
        return float(
            self.ess_config_data.get(
                'controller_heartbeat_interval_s',
                constants.DEFAULT_CONTROLLER_HEARTBEAT_INTERVAL_S,
            )
        )

    def send_controller_heartbeat(self):
        """Publish a lightweight liveness message for the essBATT watchdog.

        Topic/interval come from ess_config.json. Payload is JSON (not retained)
        so a dead controller does not leave a sticky "alive" message on the broker.
        """
        if not self.mqtt_bridge.is_connected:
            return
        topic = self._heartbeat_topic()
        if topic is None:
            return
        client = self.mqtt_bridge.client
        if client is None:
            return
        payload = json.dumps({
            'source': 'essBATT_controller',
            'vrm_id': self.ess_config_data.get('vrm_id'),
            'ts': time.time(),
        })
        try:
            client.publish(topic=topic, payload=payload, qos=0, retain=False)
            self.logger.debug(
                'Controller heartbeat published on ' + topic + ': ' + payload
            )
        except Exception:
            self.logger.exception(
                'Failed to publish controller heartbeat on ' + topic
            )

    def print_alive_status_to_logger(self):
        connected = 'connected' if self.mqtt_bridge.is_connected else 'DISCONNECTED'
        self.logger.info(
            'ESS Controller script is up and running! MQTT: ' + connected
        )

    def reload_config_while_running(self):
        """Reload ess_config.json and push it into dependent modules."""
        new_config = self.config_manager.load_config()
        if not self.config_manager.config_data_loaded_correctly:
            self.logger.error('Online config reload failed; keeping previous config.')
            return
        self.ess_config_data = new_config
        self.battery_protector.update_config(new_config)
        self.state_machine.config = new_config
        self.setpoint_calculator.update_config(new_config)
        self.mqtt_bridge.update_config(new_config)
        self.logger.setLevel(
            constants.LOGLEVEL_NAME_TO_NUMBER[self.ess_config_data.get('debug_level', 'INFO')]
        )
        if self.rt_ess_control_update_obj is not None:
            self.rt_ess_control_update_obj.interval = self.ess_config_data.get(
                'control_update_rate', 2.0
            )
        if self.rt_controller_heartbeat_obj is not None:
            self.rt_controller_heartbeat_obj.interval = self._heartbeat_interval_s()
        self.logger.debug('ess_config.json reloaded while running.')

    def reboot_ess_controller_script(self):
        # TODO
        self.logger.error('Reboot function not yet implemented!')


# ======================================================================
# Entry point
# ======================================================================

if __name__ == '__main__':
    log_formatter = logging.Formatter(
        '%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s'
    )
    my_handler = RotatingFileHandler(
        'essBATT_controller.log',
        mode='a',
        maxBytes=50 * 1024 * 1024,
        backupCount=1,
        encoding=None,
        delay=0,
    )
    my_handler.setFormatter(log_formatter)
    my_handler.setLevel(logging.DEBUG)
    app_log = logging.getLogger('root')
    app_log.setLevel(logging.INFO)
    app_log.addHandler(my_handler)

    ess_controller_obj = essBATT_controller(app_log)

    if (
        ess_controller_obj.ess_config_data_loaded_correctly is True
        and ess_controller_obj.ess_setvalue_list_loaded_correctly is True
        and ess_controller_obj.ess_controller_state_loaded_correctly is True
    ):
        try:
            ess_controller_obj.run()
        finally:
            ess_controller_obj.logger.warning(
                'essBATT controller: Shutdown. Stopping timers and MQTT.'
            )
            ess_controller_obj.stop()
    else:
        ess_controller_obj.logger.warning(
            'essBATT controller not running and needs restart!'
        )
        ess_controller_obj.stop()
