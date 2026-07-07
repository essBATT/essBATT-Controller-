# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity)

"""
State Machine for essBATT Controller (Step 4)

This module encapsulates all logic related to:
- State transitions (normal <-> charge_to_SOC <-> balancing)
- External MQTT commands
- Scheduled and auto-balancing
- Winter mode
- Emergency charge/discharge
"""

from datetime import datetime, timedelta
import numbers


class StateMachine:
    def __init__(self, config, logger, controller_state, temporary_script_states, external_input):
        self.config = config
        self.logger = logger
        self.ess_controller_state = controller_state
        self.temporary_script_states = temporary_script_states
        self.ess_external_input = external_input

    def update(self, local_values):
        """Main state machine update (replaces old statemachine_update)."""
        self._handle_external_input(local_values)
        self._handle_scheduled_from_config(local_values)
        self._check_transition_back_to_normal(local_values)

    # ------------------------------------------------------------------
    # External input handling
    # ------------------------------------------------------------------
    def _handle_external_input(self, local_values):
        if self.config['external_control_settings']['allow_external_control_over_mqtt'] != 1:
            return

        latest_ts = None
        target_state = "none"

        balancing = self.ess_external_input.get('balancing', {})
        charge = self.ess_external_input.get('charge_to_SOC', {})

        if balancing.get('receive_time') and charge.get('receive_time'):
            if balancing['receive_time'] > charge['receive_time']:
                latest_ts = balancing['receive_time']
                target_state = "balancing"
            else:
                latest_ts = charge['receive_time']
                target_state = "charge_to_SOC"
        elif charge.get('receive_time'):
            latest_ts = charge['receive_time']
            target_state = "charge_to_SOC"
        elif balancing.get('receive_time'):
            latest_ts = balancing['receive_time']
            target_state = "balancing"
        else:
            self.logger.debug('Checked external input: Neither "balancing" nor "charge_to_SOC" external command received yet.')
            return

        if latest_ts and self.ess_external_input.get('new_data_received'):
            self.copy_external_data_to_internal_state_dict(target_state, local_values)

        current_time = datetime.now(tz=None)

        if 'external_receive_info' in local_values:
            info = local_values['external_receive_info']
            if info.get('activated') is True:
                if (self.ess_controller_state[info['target_state']]['activation_time'] == 'none' and
                        self.ess_controller_state[info['target_state']]['scheduled_start_time'] == 'none'):
                    self.do_state_update(info['target_state'])
            if info.get('activated') is False:
                self.do_state_update('normal_operation')

        for target in ['balancing', 'charge_to_SOC']:
            if (self.ess_controller_state[target]['activation_time'] == 'none' and
                    self.ess_controller_state[target]['scheduled_start_time'] != 'none'):
                sched = self.get_scheduled_starttime_datetime_obj(target)
                if sched:
                    if sched > current_time:
                        self.logger.debug(f'Switch to "{target}" waiting for scheduled time')
                    else:
                        self.do_state_update(target)
                        self.logger.info(f'Switching to "{target}" because scheduled time reached')

    # ------------------------------------------------------------------
    # Config-based scheduling
    # ------------------------------------------------------------------
    def _handle_scheduled_from_config(self, local_values):
        self._handle_auto_balancing()
        self._handle_winter_mode(local_values)
        self._handle_emergency(local_values)

    def _handle_auto_balancing(self):
        if self.config['balancing_settings']['auto_balancing_settings']['activate_auto_balancing'] != 1:
            return
        if self.ess_controller_state['current_state'] == 'balancing':
            return

        if (self.ess_controller_state['winter_mode'] == 'activated' and
                self.config['winter_mode']['use_winter_mode'] == 1):
            cfg = self.config['winter_mode']['auto_balancing_settings']
        else:
            cfg = self.config['balancing_settings']['auto_balancing_settings']

        time_string = f"{cfg['weekday']} {cfg['time']}"
        target_weekday = cfg['weekday']
        target_diff = cfg['days_to_next_autobalancing']

        try:
            act_time = datetime.strptime(time_string, '%A %H:%M')
            now = datetime.now(tz=None)
            if self.ess_controller_state['time_of_last_completed_balancing'] != "none":
                last = datetime.strptime(self.ess_controller_state['time_of_last_completed_balancing'],
                                         '%d-%b-%Y (%H:%M:%S.%f)')
                days = round((now - last).total_seconds() / (3600 * 24))
                if (days >= target_diff and now.strftime('%A') == target_weekday and
                        now.time() > act_time.time()):
                    self.logger.info('Autobalancing condition met!')
                    self.do_state_update('balancing')
            else:
                if (now.strftime('%A') == target_weekday and now.time() > act_time.time()):
                    self.logger.info('First activation autobalancing condition met!')
                    self.do_state_update('balancing')
        except ValueError as e:
            self.logger.error(f'Auto balancing weekday/time invalid: {e}')

    def _handle_winter_mode(self, local_values):
        if self.config['winter_mode']['use_winter_mode'] != 1:
            return

        now = datetime.now(tz=None)
        year = now.strftime('%Y')
        next_year = str(int(year) + 1)

        try:
            start = datetime.strptime(self.config['winter_mode']['winter_mode_start_date'] + year, '%d.%m.%Y')
            end_this = datetime.strptime(self.config['winter_mode']['winter_mode_end_date'] + year, '%d.%m.%Y')
            end_next = datetime.strptime(self.config['winter_mode']['winter_mode_end_date'] + next_year, '%d.%m.%Y')
        except ValueError as e:
            self.logger.error(f'Winter mode dates invalid: {e}')
            return

        if end_this > start:
            final_end = end_this
        elif now < end_this:
            final_end = end_this
        else:
            final_end = end_next

        if (now < start < final_end) or (final_start := start) and (now > final_end and final_end < start):
            if self.ess_controller_state['winter_mode'] != 'not_activated':
                self.logger.info('Winter is gone. Go into summer mode!')
                self.ess_controller_state['winter_mode'] = 'not_activated'
                self.reset_winter_mode_states()
        elif (start < now < final_end) or (final_end < start and now < final_end):
            if self.ess_controller_state['winter_mode'] != 'activated':
                self.logger.info('Winter is coming! Go into winter mode!')
                self.ess_controller_state['winter_mode'] = 'activated'

        if 'battery_soc' in local_values:
            if (self.ess_controller_state['winter_mode'] == 'activated' and
                    local_values['battery_soc'] <= self.config['winter_mode']['winter_min_SOC'] and
                    self.ess_controller_state.get('winter_SOC_discharge_limit') != "activated"):
                self.ess_controller_state['winter_SOC_discharge_limit'] = "activated"
                self.temporary_script_states['winter_mode_multis_switch_off_time'] = now
                self.logger.info(f'Winter SOC discharge limit ACTIVATED (<= {self.config["winter_mode"]["winter_min_SOC"]})')

            if (self.ess_controller_state['winter_mode'] == 'activated' and
                    local_values['battery_soc'] >= self.config['winter_mode']['winter_restart_multis_SOC'] and
                    self.ess_controller_state.get('winter_SOC_discharge_limit') == "activated"):
                self.ess_controller_state['winter_SOC_discharge_limit'] = "not_activated"
                self.temporary_script_states['winter_mode_multis_switch_off_time'] = None
                self.logger.info('Winter SOC discharge limit DEACTIVATED')

    def _handle_emergency(self, local_values):
        if self.config['battery_settings']['emergency_(dis)charge']['use_emergency_(dis)charging'] != 1:
            return
        if not local_values.get('all_CCGX_values_available'):
            return

        now = datetime.now(tz=None)
        cfg = self.config['battery_settings']['emergency_(dis)charge']

        if local_values.get('battery_min_cell_voltage') <= cfg['min_cell_voltage_for_emergency_charge']:
            if self.temporary_script_states.get('emergency_(dis)charge_begin_time') is None:
                self.temporary_script_states['emergency_(dis)charge_begin_time'] = now
                self.activate_charge_to_SOC_from_script(80, 10, 'charge')
                self.logger.info(f'"STARTING" emergency charging (min cell {local_values["battery_min_cell_voltage"]})')

        if local_values.get('battery_max_cell_voltage') >= cfg['max_cell_voltage_for_emergency_discharge']:
            if self.temporary_script_states.get('emergency_(dis)charge_begin_time') is None:
                self.temporary_script_states['emergency_(dis)charge_begin_time'] = now
                self.activate_charge_to_SOC_from_script(10, 10, 'discharge')
                self.logger.info(f'"STARTING" emergency discharging (max cell {local_values["battery_max_cell_voltage"]})')

        begin = self.temporary_script_states.get('emergency_(dis)charge_begin_time')
        if begin:
            if (now - begin).total_seconds() > cfg['emergency_(dis)charge_duration_minutes'] * 60:
                self.temporary_script_states['emergency_(dis)charge_begin_time'] = None
                self.do_state_update('normal_operation')
                self.logger.info('"ENDING" emergency (dis)charge')

    def _check_transition_back_to_normal(self, local_values):
        if not local_values.get('all_CCGX_values_available'):
            return
        current = self.ess_controller_state['current_state']
        if current == 'balancing':
            min_v = local_values['battery_min_cell_voltage']
            max_v = local_values['battery_max_cell_voltage']
            cond = self.config['balancing_settings']['balancing_complete_condition']
            if (min_v >= cond['min_cell_voltage_threshold'] and
                    abs(min_v - max_v) < cond['max_diff_voltage_between_min_and_max_cell']):
                self.logger.info('Balancing complete condition met!')
                self.do_state_update('normal_operation')
                self.ess_controller_state['time_of_last_completed_balancing'] = datetime.now(tz=None).strftime("%d-%b-%Y (%H:%M:%S.%f)")

        elif current == 'charge_to_SOC':
            direction = self.ess_controller_state['charge_to_SOC']['requested_current_direction']
            target = self.ess_controller_state['charge_to_SOC']['target_SOC']
            soc = local_values['battery_soc']
            if (direction == 'discharge' and soc <= target) or \
               (direction == 'charge' and soc >= target) or \
               direction == 'SOC_reached':
                self.do_state_update('normal_operation')

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def activate_charge_to_SOC_from_script(self, target_soc, max_current="none", current_direction='charge'):
        if target_soc > 99.9: target_soc = 99.9
        if target_soc < 0.1: target_soc = 0.1
        self.ess_controller_state['charge_to_SOC']['target_SOC'] = target_soc
        self.ess_controller_state['charge_to_SOC']['max_current'] = max_current
        self.ess_controller_state['charge_to_SOC']['requested_current_direction'] = current_direction
        self.do_state_update('charge_to_SOC')

    def do_state_update(self, target_state_str):
        now_string = datetime.now(tz=None).strftime("%d-%b-%Y (%H:%M:%S.%f)")
        self.logger.info(f'State change from {self.ess_controller_state["current_state"]} to "{target_state_str.upper()}"!')
        self.ess_controller_state['current_state'] = target_state_str
        self.ess_controller_state['time_of_last_change'] = now_string
        if target_state_str in ('balancing', 'charge_to_SOC'):
            self.ess_controller_state[target_state_str]['activation_time'] = now_string
        if target_state_str == 'normal_operation':
            self.reset_single_state_data('balancing')
            self.reset_single_state_data('charge_to_SOC')

    def reset_winter_mode_states(self):
        self.temporary_script_states['winter_mode_multis_switch_off_time'] = None
        self.temporary_script_states['winter_mode_inactive_charge_begin_time'] = None
        self.temporary_script_states['winter_mode_charge_begin_time'] = None

    def reset_single_state_data(self, reset_state):
        if reset_state == "balancing":
            self.ess_controller_state['balancing']['activation_time'] = 'none'
            self.ess_controller_state['balancing']['max_current'] = 'none'
            self.ess_controller_state['balancing']['scheduled_start_time'] = 'none'
        elif reset_state == "charge_to_SOC":
            self.ess_controller_state['charge_to_SOC']['activation_time'] = 'none'
            self.ess_controller_state['charge_to_SOC']['target_SOC'] = 'none'
            self.ess_controller_state['charge_to_SOC']['max_current'] = 'none'
            self.ess_controller_state['charge_to_SOC']['scheduled_start_time'] = 'none'
            self.ess_controller_state['charge_to_SOC']['requested_current_direction'] = 'none'

    def get_scheduled_starttime_datetime_obj(self, mode_string):
        fmt = self.config['external_control_settings']['date_format'] + ' ' + \
              self.config['external_control_settings']['time_format']
        try:
            return datetime.strptime(self.ess_controller_state[mode_string]['scheduled_start_time'], fmt)
        except (ValueError, TypeError, KeyError):
            return None

    def copy_external_data_to_internal_state_dict(self, target_state_str, local_values):
        local_values['external_receive_info'] = {}
        if target_state_str in ("balancing", "charge_to_SOC"):
            if (target_state_str == "balancing" and self.ess_external_input.get('balancing', {}).get('activated') == '1'):
                self.reset_single_state_data('charge_to_SOC')
                if 'current_limit_input' in self.ess_external_input.get('balancing', {}):
                    self.ess_controller_state['balancing']['max_current'] = self.ess_external_input['balancing']['current_limit_input']
                self.update_state_scheduledtime_from_external_input(target_state_str)
                local_values['external_receive_info']['target_state'] = target_state_str
                local_values['external_receive_info']['activated'] = True
                self.ess_external_input['new_data_received'] = False
                self.ess_external_input['balancing'] = {}
            elif (target_state_str == "charge_to_SOC" and self.ess_external_input.get('charge_to_SOC', {}).get('activated') == '1'):
                self.reset_single_state_data('balancing')
                ext = self.ess_external_input.get('charge_to_SOC', {})
                if 'target_SOC' in ext:
                    if 'battery_soc' not in local_values:
                        self.logger.warning('charge_to_SOC command ignored: battery SOC not available yet.')
                        return
                    if ext['target_SOC'] > local_values['battery_soc']:
                        self.ess_controller_state['charge_to_SOC']['requested_current_direction'] = 'charge'
                    elif ext['target_SOC'] == local_values['battery_soc']:
                        self.ess_controller_state['charge_to_SOC']['requested_current_direction'] = 'SOC_reached'
                    else:
                        self.ess_controller_state['charge_to_SOC']['requested_current_direction'] = 'discharge'
                    self.ess_controller_state['charge_to_SOC']['target_SOC'] = ext['target_SOC']
                if 'current_limit_input' in ext:
                    self.ess_controller_state['charge_to_SOC']['max_current'] = ext['current_limit_input']
                self.update_state_scheduledtime_from_external_input(target_state_str)
                local_values['external_receive_info']['target_state'] = target_state_str
                local_values['external_receive_info']['activated'] = True
                self.ess_external_input['new_data_received'] = False
                self.ess_external_input['charge_to_SOC'] = {}

            elif (target_state_str == "balancing" and self.ess_external_input.get('balancing', {}).get('activated') == '0'):
                self.reset_single_state_data('balancing')
                local_values['external_receive_info']['target_state'] = target_state_str
                local_values['external_receive_info']['activated'] = False
                self.ess_external_input['new_data_received'] = False
                self.ess_external_input['balancing'] = {}
            elif (target_state_str == "charge_to_SOC" and self.ess_external_input.get('charge_to_SOC', {}).get('activated') == '0'):
                self.reset_single_state_data('charge_to_SOC')
                local_values['external_receive_info']['target_state'] = target_state_str
                local_values['external_receive_info']['activated'] = False
                self.ess_external_input['new_data_received'] = False
                self.ess_external_input['charge_to_SOC'] = {}

    def update_state_scheduledtime_from_external_input(self, mode_string):
        date_in = self.ess_external_input.get(mode_string, {}).get('date_input', '-')
        time_in = self.ess_external_input.get(mode_string, {}).get('time_input', '-')
        sched = self.datetime_obj_from_input_timestamp(time_in, date_in)
        if isinstance(sched, datetime):
            fmt = self.config['external_control_settings']['date_format'] + ' ' + \
                  self.config['external_control_settings']['time_format']
            self.ess_controller_state[mode_string]['scheduled_start_time'] = sched.strftime(fmt)
        else:
            self.ess_controller_state[mode_string]['scheduled_start_time'] = 'none'

    def datetime_obj_from_input_timestamp(self, timestring, datestring):
        if timestring == "-" and datestring == "-":
            return None
        if timestring == "-":
            timestring = "00:00"
        if datestring == "-":
            today = datetime.now(tz=None).strftime(self.config['external_control_settings']['date_format'])
            fmt = self.config['external_control_settings']['date_format'] + ' ' + \
                  self.config['external_control_settings']['time_format']
            test = datetime.strptime(today + ' ' + timestring, fmt)
            if test < datetime.now(tz=None):
                from datetime import timedelta
                datestring = (datetime.now(tz=None) + timedelta(days=1)).strftime(
                    self.config['external_control_settings']['date_format'])
            else:
                datestring = today
        fmt = self.config['external_control_settings']['date_format'] + ' ' + \
              self.config['external_control_settings']['time_format']
        try:
            return datetime.strptime(datestring + ' ' + timestring, fmt)
        except ValueError:
            return -1

    # ------------------------------------------------------------------
    # Multis switch handling (moved from controller; sets result in local_values)
    # ------------------------------------------------------------------
    def multis_switch_handling(self, local_values):
        """Determine the desired multis/vebus switch position (1=Charger only, 2=Inverter only, 3=On, 4=Off).

        Instead of directly calling set, we place 'multis_switch_position' into local_values
        so the controller can apply it. Logic matches original behavior.
        """
        # Default to full On
        highest_priority_switch_val = 3
        current_time = datetime.now(tz=None)

        charge_final = local_values.get('charge_current_limit_final', 50.0)
        discharge_power_final = local_values.get('discharge_power_limit_final', 999999)
        solar_sum = local_values.get('solarcharger_power_sum', 0)
        discharge_regular = local_values.get('discharge_current_limit_regular', 50.0)

        # if the current charge limit is zero than we do not need the charger
        if charge_final <= 0.00001:
            # deactivate charger (inverter only)
            highest_priority_switch_val = 2
            self.logger.debug('DEACTIVATE charger because of charge limit final being 0.')

        if discharge_power_final <= (solar_sum * 1.2):
            # deactivate inverter (charger only)
            if highest_priority_switch_val == 3:
                highest_priority_switch_val = 1
                self.logger.debug('DEACTIVATE inverter because of discharge limit final being below solarcharger power sum + 20%: ' + str((solar_sum * 1.2)))
            elif highest_priority_switch_val == 2:
                highest_priority_switch_val = 4
                self.logger.debug('DEACTIVATE inverter AND charger because of charge- and discharge limit final being 0.')
            else:
                self.logger.error('Should not occur!')

        # Low SOC debounce using discharge_current_limit_regular
        debounce_key = 'multi_switch_min_soc_debounce_time'
        if discharge_regular <= 0.0001 and self.temporary_script_states.get(debounce_key) is None:
            self.temporary_script_states[debounce_key] = current_time
            self.logger.debug('Low SOC event debounce timer set!')
        if discharge_regular > 0.0001 and self.temporary_script_states.get(debounce_key) is not None:
            self.temporary_script_states[debounce_key] = None
            self.logger.debug('Low SOC event debounce timer reset!')
        if (discharge_regular <= 0.0001 and
                self.temporary_script_states.get(debounce_key) is not None):
            diff_time = current_time - self.temporary_script_states[debounce_key]
            self.logger.debug('Low SOC event debounce timer difference (threshold 300): ' + str(diff_time.total_seconds()))
            if diff_time.total_seconds() > 300:
                highest_priority_switch_val = 4
                self.logger.debug('Stable low SOC after debounce time detected!')

        # Winter mode force off (both)
        if (self.ess_controller_state.get('winter_mode') == 'activated' and
                self.ess_controller_state.get('winter_SOC_discharge_limit') == "activated"):
            highest_priority_switch_val = 4
            self.logger.debug('Winter mode inverter and charger switch off done!')

        # Charge_to_SOC / balancing override (higher priority)
        current_state = self.ess_controller_state.get('current_state', 'normal_operation')
        if current_state == 'charge_to_SOC':
            direction = self.ess_controller_state.get('charge_to_SOC', {}).get('requested_current_direction', 'charge')
            if direction == 'discharge':
                highest_priority_switch_val = 3  # On (see original comment about Inverter-only not working reliably)
                self.logger.debug('Inverter only multis switch setting due to "charge to SOC state" discharging.')
            elif direction == 'charge':
                highest_priority_switch_val = 1
                self.logger.debug('Charger only multis switch setting due to "charge to SOC state" charging.')
        elif current_state == 'balancing':
            highest_priority_switch_val = 1
            self.logger.debug('Charger only multis switch setting due to "balancing state" charging.')

        local_values['multis_switch_position'] = highest_priority_switch_val
