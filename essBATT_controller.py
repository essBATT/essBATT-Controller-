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
import json
import threading
from datetime import datetime, timedelta
import numbers

# New modular imports (Step 1-4 of modularization)
import constants
from utils import RepeatedTimer
from config_manager import ConfigManager
from battery_protection import BatteryProtector
from state_machine import StateMachine


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

        # Create temporary (non-persisted) script states early (used by StateMachine + BatteryProtector logic)
        self.temporary_script_states = self.config_manager.create_temporary_script_states(self.ess_config_data)

        # Step 3: BatteryProtector for all limit calculations
        self.battery_protector = BatteryProtector(self.ess_config_data, self.logger)

        # Step 4: StateMachine
        self.state_machine = StateMachine(
            self.ess_config_data,
            self.logger,
            self.ess_controller_state,
            self.temporary_script_states,
            self.ess_external_input
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
            self.add_topic_specific_callbacks(base_path_str)
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
            if(self.ess_config_data['check_ess_config_changes_while_running'] == 1):
                self.read_config_json()
                # Some additional settings that otherwise would not change due to online changes (changes while script is running) in ess_config_json.
                self.logger.setLevel(constants.LOGLEVEL_NAME_TO_NUMBER[self.ess_config_data['debug_level']])
                self.rt_ess_control_update_obj.interval = self.ess_config_data['control_update_rate']

            ############# Read data from Victron System ###################################
            # This is done with the MQTT callback functions. The "up to date" data is stored in self.CCGX_data dictionary. Data fields in self.CCGX_data
            # are created when the first message for a data field is received and it can be DELETED if the device that delivers this data is removed from the bus (e.g. solarcharger at night).
            # Always make sure that the data field exists before using it!
                    
            # Reading most often needed values to local variables for convenience (while checking if they are available)
            local_values = {}
            self.read_values_to_local_dict(local_values)

            # Ported: external deactivate flags affect limits (used by protector + multis_switch)
            if self.ess_external_input.get('deactivate_charge', {}).get('activated'):
                local_values['deactivate_charge_limit'] = 0.0
            if self.ess_external_input.get('deactivate_discharge', {}).get('activated'):
                local_values['deactivate_discharge_limit'] = 0.0
                    
            ############# State machine (Step 4) ##########################################
            try:
                self.state_machine.update(local_values)
            except Exception as e:
                self.logger.exception('Unhandled Exception!')
                raise

            ############ Calculate and write output values ################################
            # "Static" settings
            self.set_CCGX_value(set_val_name_str='MaxFeedInPower', set_val=self.ess_config_data.get('ess_mode_2_settings', {}).get('max_system_grid_feed_in_power_2706', 0), only_set_if_deviation_to_current_setting=True)
            self.set_CCGX_value(set_val_name_str='OvervoltageFeedIn', set_val=self.ess_config_data.get('ess_mode_2_settings', {}).get('feed_excess_dc_coupled_pv_into_grid_2707', 0), only_set_if_deviation_to_current_setting=True)
            self.set_CCGX_value(set_val_name_str='PreventFeedback', set_val=self.ess_config_data.get('ess_mode_2_settings', {}).get('feed_excess_ac_coupled_pv_into_grid_2708', 0), only_set_if_deviation_to_current_setting=True)
            
            # Only continue if all input values are available
            if local_values.get('all_CCGX_values_available', False):
                # Battery protection limits are now handled by BatteryProtector (Step 3)
                try:
                    self.battery_protector.calculate_dis_charge_limits(local_values)
                except Exception as e:
                    self.logger.exception('Unhandled Exception in battery protection!')
                    raise
                # To save some power we want to switch off only the charger, the inverter or the complete multi if it is possible/makes sense
                # e.g. in winter mode if the battery SOC goes below a threshold we can deactivate the inverter AND charger and activate them again e.g. when balancing occurs etc.
                try:
                    self.state_machine.multis_switch_handling(local_values)
                    # Apply the switch if the state machine set a position
                    if 'multis_switch_position' in local_values:
                        self.set_multis_switch_mode(switch_position=local_values['multis_switch_position'])
                except Exception as e:
                    self.logger.exception('Unhandled Exception!')
                    raise
                
                # Now we calculate the AcPowerSetPoint - usually this is around 0 for normal operation (default value specified in ess_config.json). This can be changed by internal and external input that requests:
                # 1. Charge/Discharge to SOC x% with maximum current y starting at timestamp z
                # 2. Starting balancing at timestamp z with maximum current y.
                # 3. Scheduled balancing was startet by the script
                # 4. Emergency or winter charging/discharging might be required
                try:
                    self.AcPowerSetPoint_calculation(local_values)
                except Exception as e:
                    self.logger.exception('Unhandled Exception!')
                    raise
                              
                # Publish only if the current settings value does not match the setpoint and if the setpoint was already transmitted over MQTT to save network bandwidth                      
                # Values that change more often
                self.set_CCGX_value(set_val_name_str='AcPowerSetPoint', set_val=local_values['AcPowerSetPoint'], only_set_if_deviation_to_current_setting=True)
                self.set_CCGX_value(set_val_name_str='MaxChargeCurrent', set_val=local_values['charge_current_limit_final'], only_set_if_deviation_to_current_setting=True)
                self.set_CCGX_value(set_val_name_str='MaxDischargePower', set_val=local_values['discharge_power_limit_final'], only_set_if_deviation_to_current_setting=True)
                
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
        # Basic subscriptions
        subscription_list = [(base_path_str + "/battery/#", 1),
                             (base_path_str + "/grid/#", 1),
                             (base_path_str + "/solarcharger/#", 1),
                             (base_path_str + "/system/+/Ac/Consumption/#", 1),
                             (base_path_str + "/settings/#", 1),
                             (base_path_str + "/vebus/+/Mode", 1)]
        # (optional) Subscriptions for external MQTT control    
        if(self.ess_config_data['external_control_settings']['allow_external_control_over_mqtt'] == 1):
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['charge_battery_to_SOC'] != "none"):
                subscription_list.append((self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['charge_battery_to_SOC'], 1))
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['activate_top_balancing_mode'] != "none"):
                subscription_list.append((self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['activate_top_balancing_mode'], 1))
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['deactivate_discharge'] != "none"):
                subscription_list.append((self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['deactivate_discharge'], 1))
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['deactivate_charge'] != "none"):
                subscription_list.append((self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['deactivate_charge'], 1))  
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['reboot_ess_controller'] != "none"):
                subscription_list.append((self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['reboot_ess_controller'], 1))                
        return subscription_list
                                       
    
    def AcPowerSetPoint_calculation(self, local_values):
        local_values['AcPowerSetPoint'] = 0 # default case if something fails
        if(self.ess_controller_state['current_state'] == "normal_operation"):
            # In normal operation the AcPowerSetPoint is the user defined value taken from the ess_config file
            local_values['AcPowerSetPoint'] = int(self.ess_config_data['ess_mode_2_settings']['grid_power_setpoint_2700'])
            self.logger.debug('AcPowerSetPoint from NORMAL OPERATION set to:' + str(local_values['AcPowerSetPoint']))  
        elif(self.ess_controller_state['current_state'] == 'charge_to_SOC'):
            # First check if (dis)charge is limited by time
            
            # Calc AcPowerSetPoint based on the given or set (dis)charge limits. Calculated Setpoint is 10% higher given through the limits, loads and solarpower input because the maximum (dis)charge
            # current should be reached and is of course limited by the separate CCGX limits. To put it differently: the current limit is NOT realized with the AcPowerSetPoint
            # but with the current/power limits. AcPowerSetPoint is only set to 10% above the limit (+loads and solarpower), to have some fallback safety. Didn´t feel right to put an more or less unlimited power value out
            # that might be much above the safety limit for the battery
            if(self.ess_controller_state['charge_to_SOC']['requested_current_direction'] == 'charge'):
                max_charge_power_final = ((local_values['charge_current_limit_final'] * local_values['battery_voltage']) + local_values['loads_total_power'] - local_values['solarcharger_power_sum']) * 1.1
                local_values['AcPowerSetPoint'] = int(max_charge_power_final)        
            elif(self.ess_controller_state['charge_to_SOC']['requested_current_direction'] == 'discharge'):
                local_values['AcPowerSetPoint'] = int((-local_values['discharge_power_limit_final'] - local_values['solarcharger_power_sum'] + local_values['loads_total_power']) * 1.1)
            elif(self.ess_controller_state['charge_to_SOC']['requested_current_direction'] == 'SOC_reached'):
                local_values['AcPowerSetPoint'] = int(self.ess_config_data['ess_mode_2_settings']['grid_power_setpoint_2700'])
            else:
                self.logger.error('Unknwon requested discharge direction: ' + self.ess_controller_state['charge_to_SOC']['requested_current_direction'])
                return
            self.logger.debug('AcPowerSetPoint from CHARGE TO SOC set to:' + str(local_values['AcPowerSetPoint']))
        elif(self.ess_controller_state['current_state'] == "balancing"):
            # Calc AcPowerSetPoint based on the given or set (dis)charge limits and corrected with the power consumed by the loads
            max_charge_power_final = ((local_values['charge_current_limit_final'] * local_values['battery_voltage']) + local_values['loads_total_power'] - local_values['solarcharger_power_sum']) * 1.1
            local_values['AcPowerSetPoint'] = int(max_charge_power_final)  
            self.logger.debug('AcPowerSetPoint from BALANCING set to:' + str(local_values['AcPowerSetPoint']))
            self.logger.debug('AcPowerSetPoint Calculation:' + str(local_values['AcPowerSetPoint']) + '= (Current limit final:' + str(local_values['charge_current_limit_final']) + '* battery voltage: ' + str(local_values['battery_voltage']) + ') + total loads: ' + str(local_values['loads_total_power']) + ' - solarcharger input: ' + str(local_values['solarcharger_power_sum']))
            pass
        else:
            self.logger.error('Unknown "current state". Check ess_controller_state file.')
        pass

    # read_values_to_local_dict() remains in controller for now (will be refactored in later step - it prepares data from MQTT for the state machine and protector).
    def read_values_to_local_dict(self, local_values):
        local_values['all_CCGX_values_available'] = True
        if('grid_power_sum' in self.CCGX_data['grid']):
            local_values['grid_power_sum'] = self.CCGX_data['grid']['grid_power_sum']
        else:
            local_values['all_CCGX_values_available'] = False
        if('soc' in self.CCGX_data['battery']):
            local_values['battery_soc'] = self.CCGX_data['battery']['soc']
        else:
            local_values['all_CCGX_values_available'] = False
        if('max_cell_voltage' in self.CCGX_data['battery']):
            local_values['battery_max_cell_voltage'] = self.CCGX_data['battery']['max_cell_voltage']
        else:
            local_values['all_CCGX_values_available'] = False
        if('min_cell_voltage' in self.CCGX_data['battery']):
            local_values['battery_min_cell_voltage'] = self.CCGX_data['battery']['min_cell_voltage']
        else:
            local_values['all_CCGX_values_available'] = False
        if('current' in self.CCGX_data['battery']):
            local_values['battery_current'] = self.CCGX_data['battery']['current']
        else:
            local_values['all_CCGX_values_available'] = False
        if('power' in self.CCGX_data['battery']):
            local_values['battery_power'] = self.CCGX_data['battery']['power']
        else:
            local_values['all_CCGX_values_available'] = False
        if('voltage' in self.CCGX_data['battery']):
            local_values['battery_voltage'] = self.CCGX_data['battery']['voltage']
        else:
            local_values['all_CCGX_values_available'] = False
        if('L1_loads_power_consumption' in self.CCGX_data['system']):
            local_values['l1_loads_power_consumtpion'] = self.CCGX_data['system']['L1_loads_power_consumption']
        else:
            local_values['all_CCGX_values_available'] = False
        if('L2_loads_power_consumption' in self.CCGX_data['system']):
            local_values['l2_loads_power_consumtpion'] = self.CCGX_data['system']['L2_loads_power_consumption']
        else:
            local_values['all_CCGX_values_available'] = False
        if('L3_loads_power_consumption' in self.CCGX_data['system']):
            local_values['l3_loads_power_consumtpion'] = self.CCGX_data['system']['L3_loads_power_consumption']
        else:
            local_values['all_CCGX_values_available'] = False
                
        local_values['solarcharger_power_sum'] = 0    
        for element in self.CCGX_data['solarcharger']:
            if('Power' in self.CCGX_data['solarcharger'][element]):
                local_values['solarcharger_power_sum'] = local_values['solarcharger_power_sum'] + self.CCGX_data['solarcharger'][element]['Power']
            else:
                local_values['all_CCGX_values_available'] = False
        local_values['solarcharger_current_sum'] = 0    
        for element in self.CCGX_data['solarcharger']:
            if('Current' in self.CCGX_data['solarcharger'][element]):
                local_values['solarcharger_current_sum'] = local_values['solarcharger_current_sum'] + self.CCGX_data['solarcharger'][element]['Current']
            else:
                local_values['all_CCGX_values_available'] = False
        
        if(local_values['all_CCGX_values_available']):        
            # Total loads power consumption
            local_values['loads_total_power'] = local_values['l1_loads_power_consumtpion'] + local_values['l2_loads_power_consumtpion'] + local_values['l3_loads_power_consumtpion']
            self.logger.debug('Loads total power: ' + str(local_values['loads_total_power']) + ', Loads L1 power: ' + str(local_values['l1_loads_power_consumtpion']) + ', Loads L2 power: ' + str(local_values['l2_loads_power_consumtpion']) + ', Loads L3 power: ' + str(local_values['l3_loads_power_consumtpion'])) 
                        
            # Estimation of the power losses from battery/solarcharger to AC loads. It might help the system to better respect the
            # battery discharge/charge limits.
            local_values['losses_dc2ac_est'] = (local_values['grid_power_sum'] - local_values['battery_power'] + local_values['solarcharger_power_sum']) - local_values['loads_total_power']
            self.logger.debug('Estimated losses DC to AC: ' + str(local_values['losses_dc2ac_est']) + 'W')
                    
        self.logger.debug('Solarcharger power sum: ' + str(local_values['solarcharger_power_sum']) + ' Solarcharger current sum: ' + str(local_values['solarcharger_current_sum']))
        if(not local_values['all_CCGX_values_available']):
            self.logger.info('all_CCGX_values_available: "' + str(local_values['all_CCGX_values_available']) + '"')    # TODO: log level back to debug
        
    def set_CCGX_value(self, set_val_name_str=None, set_val=0, only_set_if_deviation_to_current_setting=True):
        """Function description: Sets the corresponding value in CCGX over MQTT.
        Arguments:
        set_val_name_str: [string] Victron name of the parameter found in ess_setvalue_list.json
        set_val: [number] value to send to CCGX
        only_set_if_deviation_to_current_setting: [True/False] If set to True it checks what the current setting in CCGX is and only if the new set value is different it sends the set command. False always sends the command.
        return: 0: everything ok but no value send, 1: everything ok and value send, -1: error while sending"""
        retval = 0
        if(set_val_name_str is not None):
            if(set_val_name_str in self.CCGX_data['settings']):
                if(((only_set_if_deviation_to_current_setting is True) and (set_val != self.CCGX_data['settings'][set_val_name_str]))
                or (only_set_if_deviation_to_current_setting is False)):
                    try:
                        topic_str = self.write_base_path + self.CCGX_data['settings_base_path'] + self.ess_setvalue_list[set_val_name_str]
                        payload_str = json.dumps({"value": set_val})
                        self.mqtt_client.publish(topic=topic_str, payload=payload_str, qos=1, retain=0)
                        self.logger.debug(set_val_name_str + ': Published ' + payload_str + ' on ' + topic_str + '. self.CCGX_data["settings"]["' + set_val_name_str + '"]: ' + str(self.CCGX_data['settings'][set_val_name_str]))
                        retval = 1
                    except (TypeError, ValueError, KeyError) as e:
                        self.logger.error(set_val_name_str + ' setpoint sending failed: ' + str(e))
                        retval = -1
        else: 
            self.logger.error('No set value name given!')
            retval = -1
        return retval
    
    def set_multis_switch_mode(self, switch_position):
        """
        Possible values for "switch_position": 1=Charger Only; 2=Inverter Only; 3=On; 4=Off
        See modbus tcp register list 3.10:
        https://www.victronenergy.com/support-and-downloads/technical-information
        com.victronenergy.vebus	Switch Position	33	uint16	1	0 to 65536	/Mode	yes	1	See Venus-OS manual for limitations, for example when VE.Bus BMS or DMC is installed.
        """
        if('vebus' in self.CCGX_data):
            counter = 0
            current_instance_id = ''
            for key in self.CCGX_data['vebus']:
                current_instance_id = str(key)
                counter = counter + 1
            # If the switch has a different position than the set value switch it to the new value
            current_mode = self.CCGX_data['vebus'][current_instance_id].get('Mode')
            if current_mode is None or current_mode != switch_position:
                topic_str = self.write_base_path + 'vebus/' + current_instance_id + '/Mode'
                payload_str = json.dumps({"value": switch_position})
                self.mqtt_client.publish(topic=topic_str, payload=payload_str, qos=1, retain=0)
                self.logger.info('"Multis SWITCH" switched to ' + constants.MULTIS_SWITCH_NUMBER_STRING_MAPPING[str(switch_position)] + '(value: ' + str(switch_position) + ')')
            if(counter > 1):
                self.logger.error('It seems that there is more than one instance of "vebus" available. This was not considered during development of the script and needs to be investigated!!!')

    def reboot_ess_controller_script(self):
        # TODO
        self.logger.error('Reboot function not yet implemented!')
    
    def send_keepalive_to_cerbo(self):
        # Documentation needed to know which values to send how is here:
        # https://github.com/victronenergy/dbus-mqtt
        # https://www.victronenergy.com/live/ess:ess_mode_2_and_3
        try:
            if self.mqtt_client is None or not self.mqtt_connection_ok:
                return
            # Sends keepalive to Victron OS in a way, that all available topics are returned (good for debugging but high network and system load)
            if(self.ess_config_data['keepalive_get_all_topics'] == 1):
                payload_string = ""
                topic_string = "R/" + self.ess_config_data['vrm_id'] + "/system/0/Serial"
                # Publish Topic
                errcode = self.mqtt_client.publish(topic_string, payload=payload_string, qos=0, retain=False)
                # Logging
                log_string = "Keepalive (all topics) message send! Errorcode: " + str(errcode) + ". Published topic: \'" + topic_string
                self.logger.debug(log_string)
            # Sends keepalive to Vecus OS in a way, that only the required topics are returned
            elif(self.ess_config_data['keepalive_get_all_topics'] == 0):
                topic_string = "R/" + self.ess_config_data['vrm_id'] + "/keepalive"
                topics_list = []                               
                topics_list.append("battery/+/Dc/0/#")
                topics_list.append("battery/+/Soc")
                topics_list.append("battery/+/System/MaxCellVoltage")
                topics_list.append("battery/+/System/MinCellVoltage")
                topics_list.append("grid/+/Ac/Power")
                topics_list.append("grid/+/Ac/L1/Power")
                topics_list.append("grid/+/Ac/L1/Current")
                topics_list.append("grid/+/Ac/L2/Power")
                topics_list.append("grid/+/Ac/L2/Current")
                topics_list.append("grid/+/Ac/L3/Power")
                topics_list.append("grid/+/Ac/L3/Current")
                topics_list.append("system/+/Ac/Consumption/#")
                topics_list.append("solarcharger/+/Yield/Power")
                topics_list.append("solarcharger/+/Dc/0/#")
                topics_list.append("+/+/ProductId")
                topics_list.append("settings/+/Settings/CGwacs/#")
                topics_list.append("settings/+/Settings/SystemSetup/#")
                topics_list.append("vebus/+/Mode")
                payload = json.dumps(topics_list)
                errcode = self.mqtt_client.publish(topic_string, payload)
                self.logger.debug("Keepalive (selected topics) message send! Errorcode: " + str(errcode) + ". Published topic: \'" + topic_string + '. Payload: ' + payload)
            else:
                self.logger.warning('Invalid keepalive_get_all_topics value: ' + str(self.ess_config_data['keepalive_get_all_topics']))
        except (KeyError, TypeError, ValueError) as e:
            self.logger.warning('Failed to publish keepalive message to Cerbo: ' + str(e))
        except Exception:
            self.logger.exception('Unexpected error while publishing keepalive to Cerbo')
            
    def print_alive_status_to_logger(self):
        self.logger.info('ESS Controller script is up and running!')

    # All config and state related methods have been moved to ConfigManager (Step 2).
    # See config_manager.py for load_config(), load_state(), save_state_if_changed(), etc.
    
    def add_topic_specific_callbacks(self, base_path_str):
        # Grid
        topic_str = base_path_str + "/grid/+/Ac/Power"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_grid_power)
        topic_str = base_path_str + "/grid/+/Ac/L1/Power"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_L1_power)
        topic_str = base_path_str + "/grid/+/Ac/L1/Current"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_L1_current)
        topic_str = base_path_str + "/grid/+/Ac/L2/Power"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_L2_power)
        topic_str = base_path_str + "/grid/+/Ac/L2/Current"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_L2_current)
        topic_str = base_path_str + "/grid/+/Ac/L3/Power"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_L3_power)
        topic_str = base_path_str + "/grid/+/Ac/L3/Current"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_L3_current)
        # Battery
        topic_str = base_path_str + "/battery/+/Soc"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_battery_soc)
        topic_str = base_path_str + "/battery/+/System/MaxCellVoltage"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_battery_maxcellvoltage)
        topic_str = base_path_str + "/battery/+/System/MinCellVoltage"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_battery_mincellvoltage)
        topic_str = base_path_str + "/battery/+/Dc/0/Temperature"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_battery_temp)
        topic_str = base_path_str + "/battery/+/Dc/0/Current"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_battery_current)
        topic_str = base_path_str + "/battery/+/Dc/0/Power"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_battery_power)
        topic_str = base_path_str + "/battery/+/Dc/0/Voltage"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_battery_voltage)
        # Solarcharger
        topic_str = base_path_str + "/solarcharger/+/Yield/Power"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_solarcharger_power)
        topic_str = base_path_str + "/solarcharger/+/Dc/0/#"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_solarcharger_dc_values)
        # System
        topic_str = base_path_str + "/system/+/Ac/Consumption/#"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_system_AC_consumption)
        # External Control
        if(self.ess_config_data['external_control_settings']['allow_external_control_over_mqtt'] == 1):
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['charge_battery_to_SOC'] != "none"):
                self.mqtt_client.message_callback_add(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['charge_battery_to_SOC'], self.on_msg_ext_charge_to_SOC)
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['activate_top_balancing_mode'] != "none"):
                self.mqtt_client.message_callback_add(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['activate_top_balancing_mode'], self.on_msg_ext_balancing)
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['deactivate_discharge'] != "none"):
                self.mqtt_client.message_callback_add(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['deactivate_discharge'], self.on_msg_ext_deactivate_discharge)
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['deactivate_charge'] != "none"):
                self.mqtt_client.message_callback_add(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['deactivate_charge'], self.on_msg_ext_deactivate_charge)
            if(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['reboot_ess_controller'] != "none"):
                self.mqtt_client.message_callback_add(self.ess_config_data['external_control_settings']['mqtt_external_control_topics']['reboot_ess_controller'], self.on_msg_ext_reboot_ess_controller)
        # Misc
        topic_str = base_path_str + "/settings/+/Settings/CGwacs/#"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_settings_Cgwacs)
        topic_str = base_path_str + "/settings/+/Settings/SystemSetup/#"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_settings_SystemSetup)
        topic_str = base_path_str + "/vebus/+/Mode"
        self.mqtt_client.message_callback_add(topic_str, self.on_msg_multis_switch_mode)
        
    def _parse_victron_mqtt_value(self, msg):
        """Parse Victron dbus-mqtt JSON payload for settings/system/vebus topics (no device-removed handling)."""
        try:
            payload = json.loads(msg.payload)
        except json.JSONDecodeError:
            self.logger.warning('Invalid JSON on MQTT topic ' + msg.topic + ': ' + str(msg.payload))
            return None
        if 'value' not in payload:
            self.logger.warning('MQTT message without "value" on topic ' + msg.topic)
            return None
        return payload['value']

    def device_removed_from_bus_detected(self, topic):
        split_topic = topic.split('/')
        device_type = split_topic[2]
        device_instance = split_topic[3]
        try:
            # If the device is a solarcharger the internal structure has an entry for each solarcharger.
            if(device_type != 'solarcharger'):
                self.CCGX_data[device_type] = {}
            else:
                self.CCGX_data[device_type][device_instance] = {}
            self.logger.info('Received json string without "value". Probably device removed from bus. Topic: ' + topic)
        except KeyError as e:
            self.logger.error('Deleting device instance due to empty payload failed: ' + str(e))
        
                         
    ##################### MQTT Callback Functions ##############
    # The callback when this client recieves A CONNACK from the broker    
    def on_connect(self, client, userdata, flags, rc):
        if rc==0:
            self.logger.info("Success: Connected to MQTT Server with result code "+str(rc))
            self.mqtt_connection_ok = True
            self.mqtt_disconnected = False
        else:
            self.logger.error("Failed to connected to MQTT Server with result code "+str(rc))
            self.mqtt_connection_ok = False
        
    # The callback when the MQTT broker disconnects
    def on_disconnect(self, client, userdata, rc):
        self.logger.warning("MQTT server disconnected. Reason: "  + str(rc))
        self.mqtt_connection_ok = False
        self.mqtt_disconnected  = True
            
    # The callback for when a PUBLISH message is received from the server.
    def on_message(self, client, userdata, msg):
        self.logger.debug("New unused (!!!) MQTT message: " + msg.topic +" "+ str(msg.payload))
        #self.store_received_mqtt_message(msg.topic, msg.payload)
        
    def on_subscribe(self, client, userdata, mid, granted_qos):  # subscribe to mqtt broker
        self.logger.debug("MQTT on_subscribe function called!")
    ######### Topic specific callbacks ############################
    # Grid
    def on_msg_grid_power(self, client, userdata, msg):
        try:
            self.CCGX_data['grid']['grid_power_sum'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_L1_power(self, client, userdata, msg):
        try:
            self.CCGX_data['grid']['L1_power'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_L1_current(self, client, userdata, msg):
        try:
            self.CCGX_data['grid']['L1_current'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_L2_power(self, client, userdata, msg):
        try:
            self.CCGX_data['grid']['L2_power'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_L2_current(self, client, userdata, msg):
        try:
            self.CCGX_data['grid']['L2_current'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_L3_power(self, client, userdata, msg):
        try:
            self.CCGX_data['grid']['L3_power'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_L3_current(self, client, userdata, msg):
        try:
            self.CCGX_data['grid']['L3_current'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    # Battery
    def on_msg_battery_soc(self, client, userdata, msg):
        try:
            self.CCGX_data['battery']['soc'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_battery_maxcellvoltage(self, client, userdata, msg):
        try:
            self.CCGX_data['battery']['max_cell_voltage'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_battery_mincellvoltage(self, client, userdata, msg):
        try:
            self.CCGX_data['battery']['min_cell_voltage'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_battery_temp(self, client, userdata, msg):
        try:
            self.CCGX_data['battery']['temperature'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_battery_current(self, client, userdata, msg):
        try:
            self.CCGX_data['battery']['current'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_battery_power(self, client, userdata, msg):
        try:
            self.CCGX_data['battery']['power'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
            
    def on_msg_battery_voltage(self, client, userdata, msg):
        try:
            self.CCGX_data['battery']['voltage'] = json.loads(msg.payload)['value']
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
    # Solarcharger
    # With "grid" and "battery" callbacks the callback functions only handle one value. With the solarchargers the message handling is 
    # done a bit different, because the chargers are added and removed dynamically and you could also permantly add/remove a solarcharger and 
    # this script should still work. That´s why these solarcharger callback functions are more complex and involve "topic parsing" and handle multiple values. 
    # As long as a solarcharger is active on the bus values are stored. If it vanishes from the bus the whole data structure for this solarcharger
    # is deleted.
    def on_msg_solarcharger_power(self, client, userdata, msg):
        split_topic = msg.topic.split('/')
        try:
            payload_value = json.loads(msg.payload)['value']
            solar_charger_topic_id_str = split_topic[3]
            # If this solarcharger is not known add it to the dictionary
            if(solar_charger_topic_id_str not in self.CCGX_data['solarcharger']):
                self.CCGX_data['solarcharger'][solar_charger_topic_id_str] = {}
            # Store the power value
            self.CCGX_data['solarcharger'][solar_charger_topic_id_str]['Power'] = payload_value
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
       
    def on_msg_solarcharger_dc_values(self, client, userdata, msg):
        split_topic = msg.topic.split('/')
        try:
            payload_value = json.loads(msg.payload)['value']
            solar_charger_topic_id_str = split_topic[3]
            value_name_str = split_topic[6]
            # If this solarcharger is not known add it to the dictionary
            if(solar_charger_topic_id_str not in self.CCGX_data['solarcharger']):
                self.CCGX_data['solarcharger'][solar_charger_topic_id_str] = {}
            # Store the value in the corresponding data field
            self.CCGX_data['solarcharger'][solar_charger_topic_id_str][value_name_str] = payload_value
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            self.device_removed_from_bus_detected(msg.topic)
    
    # System
    def on_msg_system_AC_consumption(self, client, userdata, msg):
        split_topic = msg.topic.split('/')
        try:
            phase_number = split_topic[6]
            measurement_name = split_topic[7]
        except IndexError:
            self.logger.error('Malformed system consumption topic: ' + msg.topic)
            return
        if(phase_number != 'NumberOfPhases' and measurement_name == 'Power'):
            payload_value = self._parse_victron_mqtt_value(msg)
            if payload_value is not None:
                value_name = phase_number + '_loads_power_consumption'
                self.CCGX_data['system'][value_name] = payload_value
            
    # Misc
    def on_msg_settings_Cgwacs(self, client, userdata, msg):
        split_topic = msg.topic.split('/')
        try:
            value_name = split_topic[6]
            settings_instance = split_topic[3]
        except IndexError:
            self.logger.error('Malformed settings CGwacs topic: ' + msg.topic)
            return
        payload_value = self._parse_victron_mqtt_value(msg)
        if payload_value is None:
            return
        self.CCGX_data['settings'][value_name] = payload_value
        if('settings_base_path' not in self.CCGX_data):
            self.CCGX_data['settings_base_path'] = 'settings/' + settings_instance + '/Settings/'
        
    def on_msg_settings_SystemSetup(self, client, userdata, msg):
        split_topic = msg.topic.split('/')
        try:
            value_name = split_topic[6]
        except IndexError:
            self.logger.error('Malformed settings SystemSetup topic: ' + msg.topic)
            return
        payload_value = self._parse_victron_mqtt_value(msg)
        if payload_value is not None:
            self.CCGX_data['settings'][value_name] = payload_value
            
    def on_msg_multis_switch_mode(self, client, userdata, msg):
        split_topic = msg.topic.split('/')
        try:
            instance_id = split_topic[3]
            value_name = split_topic[4]
        except IndexError:
            self.logger.error('Malformed vebus topic: ' + msg.topic)
            return
        payload_value = self._parse_victron_mqtt_value(msg)
        if payload_value is None:
            return
        if('vebus' not in self.CCGX_data):
            self.CCGX_data['vebus'] = {}
        if(instance_id not in self.CCGX_data['vebus']):
            self.CCGX_data['vebus'][instance_id] = {}
        self.CCGX_data['vebus'][instance_id][value_name] = payload_value

    # External
    def on_msg_ext_charge_to_SOC(self, client, userdata, msg):
        current_limit_used = False
        starttime_used = False
        startdate_used = False
        try:
            payload = msg.payload.decode('utf-8')
            split_payload = payload.split('/')
            activated = split_payload[0]
            target_soc = int(split_payload[1])
            if(split_payload[2] != '-'):
                current_limit = int(split_payload[2])
                current_limit_used = True
            if(split_payload[3] != '-'):
                time_input = split_payload[3]
                starttime_used = True
            if(split_payload[4] != '-'):
                date_input = split_payload[4]
                startdate_used = True
        except UnicodeDecodeError:
            self.logger.error('charge_to_SOC payload is not valid UTF-8')
            return
        except (IndexError, ValueError) as e:
            self.logger.error('charge_to_SOC payload format invalid (expected activated/target_soc/current/time/date): ' + str(e))
            return
        if('charge_to_SOC' not in self.ess_external_input):
            self.ess_external_input['charge_to_SOC'] = {}
        self.ess_external_input['charge_to_SOC']['target_SOC'] = target_soc
        self.ess_external_input['charge_to_SOC']['activated'] = activated
        if(current_limit_used):
            self.ess_external_input['charge_to_SOC']['current_limit_input'] = current_limit
        if(starttime_used):
            self.ess_external_input['charge_to_SOC']['time_input'] = time_input
        if(startdate_used):
            self.ess_external_input['charge_to_SOC']['date_input'] = date_input
        self.ess_external_input['charge_to_SOC']['receive_time'] = datetime.now(tz=None)
        self.ess_external_input['new_data_received'] = True
            
    def on_msg_ext_balancing(self, client, userdata, msg):
        current_limit_used = False
        starttime_used = False
        startdate_used = False
        try:
            payload = msg.payload.decode('utf-8')
            split_payload = payload.split('/')
            activated = split_payload[0]
            if(split_payload[1] != '-'):
                current_limit = int(split_payload[1])
                current_limit_used = True
            if(split_payload[2] != '-'):
                time_input = split_payload[2]
                starttime_used = True
            if(split_payload[3] != '-'):
                date_input = split_payload[3]
                startdate_used = True
        except UnicodeDecodeError:
            self.logger.error('balancing payload is not valid UTF-8')
            return
        except (IndexError, ValueError) as e:
            self.logger.error('balancing payload format invalid (expected activated/current/time/date): ' + str(e))
            return
        if('balancing' not in self.ess_external_input):
            self.ess_external_input['balancing'] = {}
        self.ess_external_input['balancing']['activated'] = activated
        if(current_limit_used):
            self.ess_external_input['balancing']['current_limit_input'] = current_limit
        if(starttime_used):
            self.ess_external_input['balancing']['time_input'] = time_input
        if(startdate_used):
            self.ess_external_input['balancing']['date_input'] = date_input
        self.ess_external_input['balancing']['receive_time'] = datetime.now(tz=None)
        self.ess_external_input['new_data_received'] = True
    
    def on_msg_ext_deactivate_discharge(self, client, userdata, msg):
        try:
            payload = msg.payload.decode('utf-8')
        except UnicodeDecodeError:
            self.logger.error('deactivate_discharge payload is not valid UTF-8')
            return
        if((payload == 'False') or (payload == 'false')):
            activation_state = False
        elif((payload == 'True') or (payload == 'true')):
            activation_state = True
        else:
            self.logger.error('Unknown deactivate_discharge payload: ' + payload)
            return
        if('deactivate_discharge' not in self.ess_external_input):
            self.ess_external_input['deactivate_discharge'] = {}
        self.ess_external_input['deactivate_discharge']['activated'] = activation_state
        self.ess_external_input['deactivate_discharge']['receive_time'] = datetime.now(tz=None)
        if(activation_state):
            self.logger.info('"DISCHARGING" is now "DEACTIVATED"!')
        else:
            self.logger.info('"DISCHARGING" is now "ALLOWED"!')
        
    def on_msg_ext_deactivate_charge(self, client, userdata, msg):
        try:
            payload = msg.payload.decode('utf-8')
        except UnicodeDecodeError:
            self.logger.error('deactivate_charge payload is not valid UTF-8')
            return
        if((payload == 'False') or (payload == 'false')):
            activation_state = False
        elif((payload == 'True') or (payload == 'true')):
            activation_state = True
        else:
            self.logger.error('Unknown deactivate_charge payload: ' + payload)
            return
        if('deactivate_charge' not in self.ess_external_input):
            self.ess_external_input['deactivate_charge'] = {}
        self.ess_external_input['deactivate_charge']['activated'] = activation_state
        self.ess_external_input['deactivate_charge']['receive_time'] = datetime.now(tz=None)
        if(activation_state):
            self.logger.info('"CHARGING" is now "DEACTIVATED"!')
        else:
            self.logger.info('"CHARGING" is now "ALLOWED"!')
        
    def on_msg_ext_reboot_ess_controller(self, client, userdata, msg):
        try:
            payload = msg.payload.decode('utf-8')
        except UnicodeDecodeError:
            self.logger.error('reboot payload is not valid UTF-8')
            return
        if((payload == 'False') or (payload == 'false')):
            reboot = False
        elif((payload == 'True') or (payload == 'true')):
            reboot = True
        else:
            self.logger.error('Unknown reboot payload: ' + payload)
            return
        if(reboot is True):
            self.reboot_ess_controller_script()

    ###############################################################
            
################################################################################################################################################################################

  
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