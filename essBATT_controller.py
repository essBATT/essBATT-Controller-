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

import copy
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


##################### essBATT Controller Class ##############
class essBATT_controller:
    """Composition root + ESS control cycle.

    Domain modules and MQTT plumbing are composed here; business logic lives
    in the dedicated modules (protection, state machine, setpoints, etc.).

    Process lifetime follows ``self._running`` (signals / intentional stop),
    *not* momentary MQTT connectivity — short broker interruptions are
    tolerated while paho auto-reconnects and resubscribes.
    """

    def __init__(self, logger):
        self.logger = logger
        self._running = False
        self._timers_started = False
        self._last_disconnect_warn_at = None
        self.ess_internal_state = {}
        self.ess_config_data = {}
        self.ess_setvalue_list = {}
        self.ess_controller_state = {}
        self.ess_external_input = {}
        self.ess_config_data_loaded_correctly = False
        self.ess_setvalue_list_loaded_correctly = False
        self.ess_controller_state_loaded_correctly = False
        self.CCGX_data = {'grid': {}, 'battery': {}, 'solarcharger': {}, 'settings': {}, 'system': {}}

        # Config / state
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
            self.ess_controller_state = {}  # fallback

        self._ess_controller_state_snapshot = copy.deepcopy(self.ess_controller_state)
        self.write_base_path = 'W/' + self.ess_config_data.get('vrm_id', 'unknown') + '/'

        self.logger.setLevel(constants.LOGLEVEL_NAME_TO_NUMBER[self.ess_config_data.get('debug_level', 'INFO')])
        self.logger.info('Effective logger level: ' + str(self.logger.getEffectiveLevel()))

        # Shared temporary (non-persisted) script states (StateMachine + BatteryProtector)
        self.temporary_script_states = self.config_manager.create_temporary_script_states(self.ess_config_data)

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
            self.ess_external_input
        )

        self.data_mapper = CcgxDataMapper(self.logger)
        self.setpoint_calculator = SetpointCalculator(
            self.ess_config_data,
            self.logger,
            self.ess_controller_state,
        )

        # MQTT path: ingestion → bridge callbacks; output → setpoints/keepalive
        self.ingestion = CcgxIngestion(self.logger, self.CCGX_data)
        self.victron_output = VictronOutput(
            self.logger,
            mqtt_client=None,  # set on first MQTT connect
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

        # Timers are started only after the first successful MQTT connect (P3)
        self.rt_keep_alive_obj = None
        self.rt_ess_control_update_obj = None
        self.rt_print_status_obj = None

    # ------------------------------------------------------------------
    # Application lifecycle
    # ------------------------------------------------------------------
    def _install_signal_handlers(self):
        """Map SIGTERM/SIGINT to a clean ``_running = False`` exit."""
        def _handler(signum, frame):
            try:
                name = signal.Signals(signum).name
            except Exception:
                name = str(signum)
            self.logger.warning(
                "Received signal " + name + " — shutting down cleanly."
            )
            self._running = False

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError) as e:
                # ValueError if not in main thread — ignore in tests
                self.logger.debug("Could not install handler for " + str(sig) + ": " + str(e))

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
        self._timers_started = True
        self.logger.info("Background timers started (control cycle, keepalive, alive log).")

    def _on_mqtt_connected(self, is_reconnect=False):
        """Called from MqttBridge after every successful CONNACK + subscribe."""
        if self.mqtt_bridge.client is not None:
            self.victron_output.set_mqtt_client(self.mqtt_bridge.client)
        self.send_keepalive_to_cerbo()
        if not self._timers_started:
            self._start_timers()
        if is_reconnect:
            self.logger.info(
                "MQTT session restored: resubscribed, keepalive sent, control continues."
            )

    def run(self):
        """Connect MQTT, then keep the process alive until stop/signal.

        Short MQTT drops do *not* end the process: paho reconnects, bridge
        resubscribes, and the control cycle resumes when ``is_connected``.
        """
        self._running = True
        self._install_signal_handlers()
        try:
            self.mqtt_bridge.start(
                connect_timeout=constants.MQTT_INITIAL_CONNECT_TIMEOUT_S
            )
            # Timers + first keepalive are started from _on_mqtt_connected

            while self._running:
                self._maybe_warn_long_disconnect()
                time.sleep(1)
        except OSError as e:
            self.logger.error('MQTT connection failed (network/OS): ' + str(e))
            self._running = False
        except TimeoutError as e:
            self.logger.error('MQTT connection failed (timeout): ' + str(e))
            self._running = False
        except Exception:
            self.logger.exception('Unexpected error during MQTT setup / main loop')
            self._running = False

    def stop(self):
        """Stop background timers and MQTT loop (used on shutdown)."""
        self._running = False
        for timer_attr in ('rt_keep_alive_obj', 'rt_ess_control_update_obj', 'rt_print_status_obj'):
            timer = getattr(self, timer_attr, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    self.logger.exception("Error stopping timer " + timer_attr)
        if hasattr(self, 'mqtt_bridge') and self.mqtt_bridge is not None:
            self.mqtt_bridge.stop()
        self._timers_started = False

    def _maybe_warn_long_disconnect(self):
        """Periodic warning while the broker is unreachable (P2 observability)."""
        if self.mqtt_bridge.is_connected:
            self._last_disconnect_warn_at = None
            return
        duration = self.mqtt_bridge.disconnect_duration_s()
        if duration < constants.MQTT_DISCONNECT_WARN_AFTER_S:
            return
        now = time.time()
        if (self._last_disconnect_warn_at is not None
                and (now - self._last_disconnect_warn_at)
                < constants.MQTT_DISCONNECT_WARN_INTERVAL_S):
            return
        self._last_disconnect_warn_at = now
        self.logger.warning(
            "MQTT still disconnected for "
            + str(int(duration))
            + "s — control cycle paused; waiting for paho auto-reconnect. "
            + "Venus keeps last setpoints until we reassert them."
        )

    # ------------------------------------------------------------------
    # Control cycle
    # ------------------------------------------------------------------
    def ess_control_cycle_update(self):
        # Only update if MQTT connection is active
        if not self.mqtt_bridge.is_connected:
            return

        try:
            self._ess_control_cycle_body()
        except Exception:
            # P2: never kill the timer thread; log and try software safe limits
            self.logger.exception(
                "Unhandled exception in control cycle — applying software safe state"
            )
            self._apply_software_safe_state(reason="control cycle exception")

    def _ess_control_cycle_body(self):
        """Core control cycle (exceptions handled by caller)."""
        # If feature is activated, read the ess_config.json file in each run
        if self.ess_config_data.get('check_ess_config_changes_while_running', 0) == 1:
            self.reload_config_while_running()

        # CCGX_data is filled by MQTT topic callbacks (CcgxIngestion via MqttBridge).
        # Fields appear when first seen and can be deleted if a device leaves the bus.
        local_values = {}
        self.data_mapper.read_values_to_local_dict(self.CCGX_data, local_values)

        # External deactivate flags affect limits (protector + multis_switch)
        if self.ess_external_input.get('deactivate_charge', {}).get('activated'):
            local_values['deactivate_charge_limit'] = 0.0
        if self.ess_external_input.get('deactivate_discharge', {}).get('activated'):
            local_values['deactivate_discharge_limit'] = 0.0

        self.state_machine.update(local_values)

        # "Static" ESS Mode 2 settings
        self.victron_output.set_ccgx_value(
            set_val_name_str='MaxFeedInPower',
            set_val=self.ess_config_data.get('ess_mode_2_settings', {}).get(
                'max_system_grid_feed_in_power_2706', 0
            ),
            only_set_if_deviation_to_current_setting=True,
        )
        self.victron_output.set_ccgx_value(
            set_val_name_str='OvervoltageFeedIn',
            set_val=self.ess_config_data.get('ess_mode_2_settings', {}).get(
                'feed_excess_dc_coupled_pv_into_grid_2707', 0
            ),
            only_set_if_deviation_to_current_setting=True,
        )
        self.victron_output.set_ccgx_value(
            set_val_name_str='PreventFeedback',
            set_val=self.ess_config_data.get('ess_mode_2_settings', {}).get(
                'feed_excess_ac_coupled_pv_into_grid_2708', 0
            ),
            only_set_if_deviation_to_current_setting=True,
        )

        if local_values.get('all_CCGX_values_available', False):
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

        self.cleanup_after_control_loop()

    def _apply_software_safe_state(self, reason=""):
        """Best-effort: force charge/discharge limits to 0 while MQTT is up."""
        if not self.mqtt_bridge.is_connected:
            self.logger.error(
                "Software safe state requested (" + reason
                + ") but MQTT is down — cannot publish safe limits."
            )
            return
        self.logger.error(
            "Applying software safe state (" + reason
            + "): MaxChargeCurrent=0, MaxDischargePower=0"
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
            self.logger.exception("Failed to publish software safe state limits")

    def cleanup_after_control_loop(self):
        # Compare in-memory state only; avoids reading ess_controller_state every cycle
        if self.ess_controller_state != self._ess_controller_state_snapshot:
            self.config_manager.save_state_if_changed(
                self.ess_controller_state, self._ess_controller_state_snapshot
            )
            self._ess_controller_state_snapshot = copy.deepcopy(self.ess_controller_state)

    def reboot_ess_controller_script(self):
        # TODO
        self.logger.error('Reboot function not yet implemented!')

    def send_keepalive_to_cerbo(self):
        """Timer entry point: publish keepalive only while MQTT is connected."""
        if not self.mqtt_bridge.is_connected:
            return
        self.victron_output.send_keepalive(
            self.ess_config_data.get('vrm_id'),
            self.ess_config_data.get('keepalive_get_all_topics', 0),
        )

    def print_alive_status_to_logger(self):
        connected = "connected" if self.mqtt_bridge.is_connected else "DISCONNECTED"
        self.logger.info(
            "ESS Controller script is up and running! MQTT: " + connected
        )

    def reload_config_while_running(self):
        """Reload ess_config.json and propagate to dependent modules (online tuning)."""
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
        self.logger.debug('ess_config.json reloaded while running.')


if __name__ == "__main__":
    ######## Logger Config ###############
    log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')
    my_handler = RotatingFileHandler(
        'essBATT_controller.log', mode='a', maxBytes=50 * 1024 * 1024,
        backupCount=1, encoding=None, delay=0,
    )
    my_handler.setFormatter(log_formatter)
    my_handler.setLevel(logging.DEBUG)
    app_log = logging.getLogger('root')
    app_log.setLevel(logging.INFO)  # overwritten later by ess_config.json
    app_log.addHandler(my_handler)

    ####### Create ESS Controller Object ###############################
    ess_controller_obj = essBATT_controller(app_log)

    ######### Start the application ############################
    if (ess_controller_obj.ess_config_data_loaded_correctly is True
            and ess_controller_obj.ess_setvalue_list_loaded_correctly is True
            and ess_controller_obj.ess_controller_state_loaded_correctly is True):
        try:
            ess_controller_obj.run()
        finally:
            ess_controller_obj.logger.warning(
                "essBATT controller: Shutdown. Stopping timers and MQTT."
            )
            ess_controller_obj.stop()
    else:
        ess_controller_obj.logger.warning("essBATT controller not running and needs restart!")
        ess_controller_obj.stop()
