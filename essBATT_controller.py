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
import paho.mqtt.client as mqtt
import logging
from logging.handlers import RotatingFileHandler
import time

# Modular imports (Steps 1–7)
import constants
from utils import RepeatedTimer
from config_manager import ConfigManager
from battery_protection import BatteryProtector
from state_machine import StateMachine
from data_mapper import CcgxDataMapper
from setpoint_control import SetpointCalculator
from mqtt_helpers import (
    build_subscription_list,
    build_ccgx_topic_bindings,
    build_external_topic_bindings,
    register_topic_callbacks,
)
from ccgx_ingestion import CcgxIngestion
from victron_output import VictronOutput
from external_control import ExternalControlHandlers


##################### essBATT Controller Class ##############
class essBATT_controller:  
    def __init__(self, logger):
        self.logger = logger
        self.mqtt_client = None
        self.mqtt_connection_ok = False
        self.mqtt_disconnected = True
        self.ess_internal_state = {}
        self.ess_config_data = {}
        self.ess_setvalue_list = {}
        self.ess_controller_state = {}
        self.ess_external_input = {}
        self.ess_config_data_loaded_correctly = False
        self.ess_setvalue_list_loaded_correctly = False
        self.ess_controller_state_loaded_correctly = False
        self.CCGX_data = {'grid':{},'battery':{}, 'solarcharger':{}, 'settings':{}, 'system':{}}

        # Step 2: Use ConfigManager for all config/state handling
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

        # Create temporary (non-persisted) script states early (shared by StateMachine + BatteryProtector)
        self.temporary_script_states = self.config_manager.create_temporary_script_states(self.ess_config_data)

        # Step 3: BatteryProtector for all limit calculations (shares temp state + controller state)
        self.battery_protector = BatteryProtector(
            self.ess_config_data,
            self.logger,
            self.temporary_script_states,
            self.ess_controller_state,
            self.ess_external_input,
        )

        # Step 4: StateMachine
        self.state_machine = StateMachine(
            self.ess_config_data,
            self.logger,
            self.ess_controller_state,
            self.temporary_script_states,
            self.ess_external_input
        )

        # Step 5: Data mapper (CCGX → local_values) and setpoint calculator
        self.data_mapper = CcgxDataMapper(self.logger)
        self.setpoint_calculator = SetpointCalculator(
            self.ess_config_data,
            self.logger,
            self.ess_controller_state,
        )

        # Step 7: MQTT ingestion (CCGX data) and Victron output (setpoints)
        self.ingestion = CcgxIngestion(self.logger, self.CCGX_data)
        self.victron_output = VictronOutput(
            self.logger,
            mqtt_client=None,  # set in run() after client is created
            ccgx_data=self.CCGX_data,
            setvalue_list=self.ess_setvalue_list,
            write_base_path=self.write_base_path,
        )
        # External MQTT control handlers (wired via topic registration table)
        self.external_handlers = ExternalControlHandlers(
            self.logger,
            self.ess_external_input,
            reboot_callback=self.reboot_ess_controller_script,
        )

        self.rt_keep_alive_obj = RepeatedTimer(constants.CERBO_KEEPALIVE_LENGTH, self.send_keepalive_to_cerbo)
        self.rt_ess_control_update_obj = RepeatedTimer(
            self.ess_config_data.get('control_update_rate', 2.0), self.ess_control_cycle_update
        )
        self.rt_print_status_obj = RepeatedTimer(
            self.ess_config_data.get('script_alive_logging_interval', 86400),
            self.print_alive_status_to_logger
        )
    
    # Note: create_temporary_script_states_dict() has been moved to ConfigManager.
    # The call now happens through self.config_manager.create_temporary_script_states(...)
###############################################################################################################################################################################################################################
###############################################################################################################################################################################################################################
###############################################################################################################################################################################################################################       
    def run(self):        
        # Configuration of the MQTT Client object
        self.mqtt_client = mqtt.Client()
        self.victron_output.set_mqtt_client(self.mqtt_client)
        self.mqtt_client.username_pw_set(username=self.ess_config_data['mqtt_username'], password=self.ess_config_data['mqtt_password'])
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_subscribe = self.on_subscribe
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.on_disconnect = self.on_disconnect
                
        try:
            self.mqtt_client.connect('localhost', port=self.ess_config_data['mqtt_server_COM_port'], keepalive=constants.MQTT_SERVER_TIMEOUT_TIMESPAN, bind_address="")
            self.logger.info("essBATT controller: Try to connect to MQTT Server: localhost on port " + str(self.ess_config_data['mqtt_server_COM_port']) + " with timeout of " + str(constants.MQTT_SERVER_TIMEOUT_TIMESPAN) + "s")
            self.mqtt_client.loop_start()
            while not self.mqtt_connection_ok: #wait in loop until connected by on_connect callback function
                self.logger.info("Waiting for MQTT server connection...")
                time.sleep(1)
            # Subscribe to all needed <service_types> of the Venus OS system and all their messages.
            # Subscribtion to selected service_types is done to reduce the "on_message callback" load in case of ess_config "keepalive_get_all_topics":1
            # Selection of messages is done in the send_keepalive_to_cerbo() function
            base_path_str = "N/" + self.ess_config_data['vrm_id']
            subscription_list_tmp = self.create_subscribtion_list(base_path_str)
            (result, mid) = self.mqtt_client.subscribe(subscription_list_tmp)
            self.logger.info("MQTT subscribtion function return value: " + str(result))
            # Topic → handler registration table (CCGX + external control)
            bindings = build_ccgx_topic_bindings(base_path_str, self.ingestion)
            bindings.extend(build_external_topic_bindings(self.ess_config_data, self.external_handlers))
            register_topic_callbacks(self.mqtt_client, bindings)
            # Send a keepalive directly after connected to MQTT server
            self.send_keepalive_to_cerbo()
            
            ###### MAIN LOOP - keep process alive while timers and MQTT run in background threads ##########################
            while self.mqtt_connection_ok is True:
                # ess_control_cycle_update() runs via RepeatedTimer; avoid busy-wait (pass would use ~100% CPU)
                time.sleep(1)
        except OSError as e:
            self.logger.error('MQTT connection failed (network/OS): ' + str(e))
            if self.mqtt_client is not None:
                self.mqtt_client.loop_stop()
            self.mqtt_connection_ok = False
        except Exception:
            self.logger.exception('Unexpected error during MQTT setup')
            if self.mqtt_client is not None:
                self.mqtt_client.loop_stop()
            self.mqtt_connection_ok = False
                
    def ess_control_cycle_update(self):
        # Only update if MQTT connection is active 
        if(self.mqtt_connection_ok is True):   
            # If feature is activated, read the ess_config.json file in each run - helpful if you try out different values while the script is running
            if(self.ess_config_data.get('check_ess_config_changes_while_running', 0) == 1):
                self.reload_config_while_running()

            ############# Read data from Victron System ###################################
            # This is done with the MQTT callback functions. The "up to date" data is stored in self.CCGX_data dictionary. Data fields in self.CCGX_data
            # are created when the first message for a data field is received and it can be DELETED if the device that delivers this data is removed from the bus (e.g. solarcharger at night).
            # Always make sure that the data field exists before using it!
                    
            # Reading most often needed values to local variables for convenience (while checking if they are available)
            local_values = {}
            self.data_mapper.read_values_to_local_dict(self.CCGX_data, local_values)

            # Ported: external deactivate flags affect limits (used by protector + multis_switch)
            if self.ess_external_input.get('deactivate_charge', {}).get('activated'):
                local_values['deactivate_charge_limit'] = 0.0
            if self.ess_external_input.get('deactivate_discharge', {}).get('activated'):
                local_values['deactivate_discharge_limit'] = 0.0
                    
            ############# State machine (Step 4) ##########################################
            try:
                self.state_machine.update(local_values)
            except Exception:
                self.logger.exception('Unhandled Exception!')
                raise

            ############ Calculate and write output values ################################
            # "Static" settings
            self.victron_output.set_ccgx_value(set_val_name_str='MaxFeedInPower', set_val=self.ess_config_data.get('ess_mode_2_settings', {}).get('max_system_grid_feed_in_power_2706', 0), only_set_if_deviation_to_current_setting=True)
            self.victron_output.set_ccgx_value(set_val_name_str='OvervoltageFeedIn', set_val=self.ess_config_data.get('ess_mode_2_settings', {}).get('feed_excess_dc_coupled_pv_into_grid_2707', 0), only_set_if_deviation_to_current_setting=True)
            self.victron_output.set_ccgx_value(set_val_name_str='PreventFeedback', set_val=self.ess_config_data.get('ess_mode_2_settings', {}).get('feed_excess_ac_coupled_pv_into_grid_2708', 0), only_set_if_deviation_to_current_setting=True)
            
            # Only continue if all input values are available
            if local_values.get('all_CCGX_values_available', False):
                # Battery protection limits are now handled by BatteryProtector (Step 3)
                try:
                    self.battery_protector.calculate_dis_charge_limits(local_values)
                except Exception:
                    self.logger.exception('Unhandled Exception in battery protection!')
                    raise
                # To save some power we want to switch off only the charger, the inverter or the complete multi if it is possible/makes sense
                # e.g. in winter mode if the battery SOC goes below a threshold we can deactivate the inverter AND charger and activate them again e.g. when balancing occurs etc.
                try:
                    self.state_machine.multis_switch_handling(local_values)
                    # Apply the switch if the state machine set a position
                    if 'multis_switch_position' in local_values:
                        self.victron_output.set_multis_switch_mode(
                            switch_position=local_values['multis_switch_position']
                        )
                except Exception:
                    self.logger.exception('Unhandled Exception!')
                    raise
                
                # AcPowerSetPoint (Step 5: SetpointCalculator)
                try:
                    self.setpoint_calculator.calculate_ac_power_setpoint(local_values)
                except Exception:
                    self.logger.exception('Unhandled Exception!')
                    raise
                              
                # Publish only if the current settings value does not match the setpoint and if the setpoint was already transmitted over MQTT to save network bandwidth                      
                # Values that change more often
                self.victron_output.set_ccgx_value(set_val_name_str='AcPowerSetPoint', set_val=local_values['AcPowerSetPoint'], only_set_if_deviation_to_current_setting=True)
                self.victron_output.set_ccgx_value(set_val_name_str='MaxChargeCurrent', set_val=local_values['charge_current_limit_final'], only_set_if_deviation_to_current_setting=True)
                self.victron_output.set_ccgx_value(set_val_name_str='MaxDischargePower', set_val=local_values['discharge_power_limit_final'], only_set_if_deviation_to_current_setting=True)
                
            # All tasks done at the end of each control loop run
            self.cleanup_after_control_loop()

        
            
###############################################################################################################################################################################################################################
###############################################################################################################################################################################################################################
###############################################################################################################################################################################################################################
    
    # multis_switch_handling has been moved into StateMachine (Step 4).
    # The controller now calls self.state_machine.multis_switch_handling(local_values)
    # and applies the result if a position was set.
     
    def cleanup_after_control_loop(self):
        # Compare in-memory state only; avoids reading ess_controller_state from disk every control cycle
        if self.ess_controller_state != self._ess_controller_state_snapshot:
            self.config_manager.save_state_if_changed(
                self.ess_controller_state, self._ess_controller_state_snapshot
            )
            self._ess_controller_state_snapshot = copy.deepcopy(self.ess_controller_state)
            
    # All state machine logic has been moved to StateMachine (Step 4).
    # See state_machine.py. The controller now calls:
    #   self.state_machine.update(local_values)
    #
    # Old methods (statemachine_update, do_state_update, activate_..., reset_*, etc.)
    # have been removed from this file.
    
            
    def create_subscribtion_list(self, base_path_str):
        """Build MQTT subscription list (delegates to mqtt_helpers)."""
        return build_subscription_list(base_path_str, self.ess_config_data)

    def reboot_ess_controller_script(self):
        # TODO
        self.logger.error('Reboot function not yet implemented!')

    def send_keepalive_to_cerbo(self):
        """Timer entry point: publish keepalive only while MQTT is connected."""
        if not self.mqtt_connection_ok:
            return
        self.victron_output.send_keepalive(
            self.ess_config_data.get('vrm_id'),
            self.ess_config_data.get('keepalive_get_all_topics', 0),
        )

    def print_alive_status_to_logger(self):
        self.logger.info('ESS Controller script is up and running!')

    def reload_config_while_running(self):
        """Reload ess_config.json and propagate to dependent modules (online tuning)."""
        new_config = self.config_manager.load_config()
        if not self.config_manager.config_data_loaded_correctly:
            self.logger.error('Online config reload failed; keeping previous config.')
            return
        self.ess_config_data = new_config
        # Keep module config references in sync
        self.battery_protector.update_config(new_config)
        self.state_machine.config = new_config
        self.setpoint_calculator.update_config(new_config)
        self.logger.setLevel(
            constants.LOGLEVEL_NAME_TO_NUMBER[self.ess_config_data.get('debug_level', 'INFO')]
        )
        if hasattr(self, 'rt_ess_control_update_obj') and self.rt_ess_control_update_obj is not None:
            self.rt_ess_control_update_obj.interval = self.ess_config_data.get('control_update_rate', 2.0)
        self.logger.debug('ess_config.json reloaded while running.')

    ##################### MQTT connection lifecycle callbacks ##############
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.logger.info("Success: Connected to MQTT Server with result code " + str(rc))
            self.mqtt_connection_ok = True
            self.mqtt_disconnected = False
        else:
            self.logger.error("Failed to connected to MQTT Server with result code " + str(rc))
            self.mqtt_connection_ok = False

    def on_disconnect(self, client, userdata, rc):
        self.logger.warning("MQTT server disconnected. Reason: " + str(rc))
        self.mqtt_connection_ok = False
        self.mqtt_disconnected = True

    def on_message(self, client, userdata, msg):
        self.logger.debug("New unused (!!!) MQTT message: " + msg.topic + " " + str(msg.payload))

    def on_subscribe(self, client, userdata, mid, granted_qos):
        self.logger.debug("MQTT on_subscribe function called!")


if __name__ == "__main__":
    ######## Logger Config ###############
    log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')
    my_handler = RotatingFileHandler('essBATT_controller.log', mode='a', maxBytes=50*1024*1024, backupCount=1, encoding=None, delay=0)
    my_handler.setFormatter(log_formatter)
    my_handler.setLevel(logging.DEBUG)
    app_log = logging.getLogger('root')
    app_log.setLevel(logging.INFO) # Default is "info" but this setting is later overwritten by ess_config.json setting
    app_log.addHandler(my_handler)
    
    ####### Create ESS Controller Object ###############################
    ess_controller_obj = essBATT_controller(app_log)

    ######### Start the application ############################
    if(ess_controller_obj.ess_config_data_loaded_correctly is True
       and ess_controller_obj.ess_setvalue_list_loaded_correctly is True
       and ess_controller_obj.ess_controller_state_loaded_correctly is True):
        try:
            ess_controller_obj.run()
        finally:
            ess_controller_obj.logger.warning("essBATT controller: Shutdown. Control loop exited and needs restart.")
            for timer_attr in ('rt_keep_alive_obj', 'rt_ess_control_update_obj', 'rt_print_status_obj'):
                timer = getattr(ess_controller_obj, timer_attr, None)
                if timer is not None:
                    timer.stop()
            if ess_controller_obj.mqtt_client is not None:
                ess_controller_obj.mqtt_client.loop_stop()
    else:
        ess_controller_obj.logger.warning("essBATT controller not running and needs restart!")
        for timer_attr in ('rt_keep_alive_obj', 'rt_ess_control_update_obj', 'rt_print_status_obj'):
            timer = getattr(ess_controller_obj, timer_attr, None)
            if timer is not None:
                timer.stop()