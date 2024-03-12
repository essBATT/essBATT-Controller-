// Optimized for reading in Notepad++ with default setting. Fileending .c because comments have a readable visual format
// Hope this also works on github

// The values in this example config work for LiFePo4 cells.
// ATTENTION: Make sure you understand each setting and adapt it to your system!!!
{
    "config_version": 1.0, // DO NOT CHANGE! This is the version of the format of this config file.
    "ess_mode": "ess_mode_2_normal_operation", // NOT IN USE - intended to be used in later development
    "vrm_id": "YOUR VRM ID", // Put you Victron VRM ID here. You can find it in your Victron VRM online portal.
    "check_ess_config_changes_while_running": 0, // [values: 0,1] If set to 1 essBATT controller will read this config file each cycle so that you can try out different parameters while the system is running. NOTE: Not all parameters are updated each cycle (ess_mode_2_settings or battery_settings parameters are updated). 
    "keepalive_get_all_topics": 0, // [values: 0,1] Default 0 (recommended for continuous script operation) means, the Victron system only sends the MQTT topics required by essBATT. If set to 1 the Victron system sends out all available topics over MQTT. This is good to investigate all possible data fields, but has very high network load which should be avoided. 
    "control_update_rate": 2.0, // [seconds] Update interval of the essBATT controller
    "debug_level": "INFO", //[INFO, DEBUG] In "info" mode only the most important information is printed to the log file. In "debug" mode all information about essBATT operation is printed to the log file.
    "mqtt_username": "YOUR MQTT SERVER USERNAME", // Put the username of your MQTT server here in quotes
    "mqtt_password": "YOUR MQTT SERVER PASSWORD", // Put the password of your MQTT server here e.g. "password123"
    "mqtt_server_COM_port": 1883, // Give the COM port of your MQTT server
    "script_alive_logging_interval": 86400, // [seconds] Every "script_alive_logging_interval" seconds a message is printed to the log file so that you know that essBATT is still running
    // ess_mode_2_settings contain all the values available in Victron ESS Mode 2 PLUS "max_battery_discharge_current". "max_battery_discharge_current" is calculated in essBATT analog to "max_battery_charge_current_2705". The number in the parameter names like "_2705" are the CCGX TCP registers.
    "ess_mode_2_settings":{        
        "grid_power_setpoint_2700": 1, // See CCGX TCP register explantation. See https://www.victronenergy.com/support-and-downloads/technical-information
        "max_power_fed_to_loads_2704": 5000, // See CCGX TCP register explantation. See https://www.victronenergy.com/support-and-downloads/technical-information
        "max_battery_discharge_current": 33, // Not available as a register but works analog to "max_battery_charge_current_2705"
        "max_battery_charge_current_2705": 33, // See CCGX TCP register explantation. See https://www.victronenergy.com/support-and-downloads/technical-information
        "max_system_grid_feed_in_power_2706": 0, // See CCGX TCP register explantation. See https://www.victronenergy.com/support-and-downloads/technical-information
        "feed_excess_dc_coupled_pv_into_grid_2707": 0, // See CCGX TCP register explantation. See https://www.victronenergy.com/support-and-downloads/technical-information
        "feed_excess_ac_coupled_pv_into_grid_2708": 0 // See CCGX TCP register explantation. See https://www.victronenergy.com/support-and-downloads/technical-information
    },
    "battery_settings":{
        "compensate_current_limit_violations": 0, // DO NOT USE - experimental (sometimes Victron violates charge or discharge limits and current flows to/from the battery even when set to zero)
        "smooth_voltage_based_(dis)charge_limits": 1, // Special behavior for voltage based charge- and discharge limits. Needs more explanation in a seperate documentation. Short: If a voltage based limit is reached it keeps the limit instead of loosing it when the voltage might cross the "limit voltage" again
        "charge_limit_mode": "max_cell_only", // ["max_cell_only", "soc_only", "soc_and_max_cell"] Max cell only uses soc_based_charge_limit_soc_array and soc_based_charge_limit_current_array. soc_only uses soc_based_charge_limit_soc_array and soc_based_charge_limit_current_array. soc_and_max_cell uses both whichever condition is first met.
        "max_cell_voltage_charging": 3.56, // [volt] If the cell with the maximum voltage reaches the "max_cell_voltage_charging" threshold charging is stopped (charge current=0A).   
        "max_cell_voltage_charging_resume": 3.5, // [volt] If the cell with the maximum voltage falls below "max_cell_voltage_charging_resume" AND previously was above "max_cell_voltage_charging" (charge current = 0A), charging starts again with the set charge current limit
        // Use "soc_based_charge_limit_soc_array" and "soc_based_charge_limit_current_array" to limit the charge current based on the "State Of Charge" (0-100%) of the battery pack.
        // Both arrays need to have the same number of elements, because to each number in the SOC array there needs to be a corresponding charge current in the current_array. In this example: if the battery is equal or above 80% SOC the charge current is limited to 25A. After getting
        // over 90% SOC the charge current is limited to 10. 
        // ATTENTION: Make sure the numbers you put here make sense because there is NO plausibility check!
        // ATTENTION: Make sure the SOC values are in ascending order. e.g. (80,90,95) is good and (90,80,95) will lead to unexpected behavior.
        // NOTE: You can put as many entrys/number pairs as you like into these arrays.
        // NOTE: If these limits are applied is defined by the field "charge_limit_mode"
        "soc_based_charge_limit_soc_array":[
            80,
            90,
            95
        ],
        "soc_based_charge_limit_current_array":[
            25,
            10,
            5
        ],
        // Same explanation as with the SOC based charge limit apply here.
        // These limits are looking at the cell with the maximum voltage and set charge current limits accordingly. If your current sensor is not very good or for other reasons you do not have a good SOC estimation it makes sense to use these limits to protect your cells.
        "max_cell_based_charge_limit_voltage_array":[
            3.42,
            3.44,
            3.47,
            3.49,
            3.5
        ],
        "max_cell_based_charge_limit_current_array":[
            50,
            20,
            10,
            5,
            1.8
        ],
        // Same as with charging only inverted. Should be self explanatory.
        "discharge_limit_mode": "soc_and_min_cell", // ["min_cell_only", "soc_only", "soc_and_min_cell"] Same as with "charge"
        "min_cell_voltage_discharging": 3.1, // [volt] If the cell with the minimum voltage is equal or below the "min_cell_voltage_discharging" threshold discharging is stopped (discharge current=0A).   
        "min_cell_voltage_discharging_resume": 3.25, // [volt] If the cell with the minimum voltage rises above "min_cell_voltage_discharging_resume" AND previously was below "min_cell_voltage_discharging" (discharge current = 0A), discharging starts again with the set discharge current limit
        // ATTENTION: SOC and MIN CELL VOLTAGE array numbers (soc_based_discharge_limit_soc_array and min_cell_based_discharge_limit_voltage_array) need to be DESCENDING to not get unexpected behavior!
        "soc_based_discharge_limit_soc_array":[
            20,
            15,
            11
        ],
        "soc_based_discharge_limit_current_array":[
            18,
            8,
            2
        ],
        "min_cell_based_discharge_limit_voltage_array":[
            3.16,
            3.15,
            3.11,
            3.1
        ],
        "min_cell_based_discharge_limit_current_array":[
            50,
            20,
            8,
            0
        ],
        // Emergency charging and discharging is used to protect the cells to never get below or above the maximum cell limits (e.g. 2.5V/3.65V for LiFePo4).
        // If a cell gets close to these maximum values for whatever reason charging/discharging is applied to bring the cell back into a more save state
        "emergency_(dis)charge":{
            "use_emergency_(dis)charging": 1, // [values: 0,1] Use or do not use this feature
            "max_cell_voltage_for_emergency_discharge": 3.63, // [volt] If the cell with the maximum voltage gets to this value the multis will discharge the battery for "emergency_(dis)charge_duration_minutes" minutes.
            "min_cell_voltage_for_emergency_charge": 3.05, // [volt] If the cell with the minimum voltage gets equal or below this value the multis will charge the battery for "emergency_(dis)charge_duration_minutes" minutes.
            "emergency_(dis)charge_duration_minutes": 10 // [minutes] Number of minutes the charging takes place. (Dis-)charge current is set (fix value) to 10A.
        }
    },
    // These settings are used if you either activated balancing over MQTT (see external_control_settings) OR if the script activated "auto balancing" (see auto_balancing_settings in normal and winter_mode). 
    "balancing_settings":{
        // The balancing state is switched to normal operation if all of the condition below are fullfilled (connected with a logical AND)
        "balancing_complete_condition":{
            "min_cell_voltage_threshold": 3.48, // [volt] If the cell with the minimum voltage gets above this value the first part of the condition is fullfilled
            "max_diff_voltage_between_min_and_max_cell": 0.01 // [volt] If the difference between the cell with the minimum and the maximum voltage is equal or below this value the second part of the condition is fullfilled
        },
        "auto_balancing_settings":{
            "activate_auto_balancing": 1, // [values: 0,1] Activate or deactivate auto balancing here
            "weekday": "Sunday", // [https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes] Choose the weekday when autobalancing shall take place. 
            "time": "12:00", //[24h time format] Auto balancing will start at this time on the set "weekday"
            "days_to_next_autobalancing": 28 // [days] Put the minimum number of days between each auto balancing here
        }
    },
    "external_control_settings":{
        "allow_external_control_over_mqtt": 1, // [values: 0,1] If activated you can send MQTT commands to essBATT to charge, discharge or balance your battery pack. You can also deactivate charging and discharging seperatly or reboot the essBATT controller
        "time_format":"%H:%M", // [https://strftime.org/] To use time based activation of "charge_battery_to_SOC" and "activate_top_balancing_mode" you can specify a time format with the linked syntax. The default time_format given here is used by the time picker in iobroker ?? GUI.
        "date_format":"%d.%m.%Y", // [https://strftime.org/] To use time based activation of "charge_battery_to_SOC" and "activate_top_balancing_mode" you can specify a date format with the linked syntax. The default date_format given here is used by the date picker in iobroker ?? GUI.
        "mqtt_external_control_topics":{
            "charge_battery_to_SOC": "EXAMPLE: iobroker/ESS_External_Control/activate_charge_to_SOC_command", // put the MQTT topic path where you send the commands here. See extended documentation for the format of the data you need to send
            "activate_top_balancing_mode": "EXAMPLE: iobroker/ESS_External_Control/activate_balancing_command",  // put the MQTT topic path where you send the commands here. See extended documentation for the format of the data you need to send
            "deactivate_discharge": "EXAMPLE: iobroker/ESS_External_Control/forbid_discharging", // put the MQTT topic path where you send the commands here. See extended documentation for the format of the data you need to send
            "deactivate_charge": "EXAMPLE: iobroker/ESS_External_Control/forbid_charging", // put the MQTT topic path where you send the commands here. See extended documentation for the format of the data you need to send
            "reboot_ess_controller": "EXAMPLE: iobroker/ESS_External_Control/reboot" // put the MQTT topic path where you send the commands here. See extended documentation for the format of the data you need to send
        }
    },
    "winter_mode":{
        "use_winter_mode": 1, // [values: 0,1] Activate or deactivate winter mode behavior
        "winter_mode_start_date": "01.11.", // [DD.MM.] Set the date when essBATT controller should go into winter mode
        "winter_mode_end_date": "10.03.", // [DD.MM.] Set the date when essBATT controller should go into normal mode
        "winter_min_SOC": 25, // [0-100%] If the battery SOC is equal or below this value, the chargers and inverters of all attached Multis are switched off to save energy.
        "winter_restart_multis_SOC": 70, // [0-100% but higher than winter_min_SOC] If the Multis are switched off due to winter mode (<winter_min_SOC) the battery needs to be charged above this value (e.g. by the solarchargers which could take multiple days in winter to charge the battery above this value) to restart the Multis.
        "winter_inactive_charge_min_voltage": 3.17, // [volt or "none" to deactivate] If the Multis are switched off due to winter mode and something is further draining the battery and the voltage falls below this value, the batteries are charged automatically for winter_inactive_charge_time_minutes minutes. This is done to increase the lifespan of the batteries. If you put "none" as the value this feature is not used
        "winter_inactive_charge_time_minutes": 30, // [minutes] Number of minutes the "Multis are inactive in winter" state is interupted to charge the batteries a bit. Fixed the charge current to a maximum of 20A but is of course limited by other settings.
        // Same behavior as with the "normal" autobalancing. Possibility to adapt the autobalancing in winter. E.g. with an hourly electricity price tariff it is better to balance on sunday noon (because it is usually cheap then) in summer, but in Winter midnight is usually cheaper. 
        "auto_balancing_settings":{
            "use_different_winter_settings": 1, 
            "weekday": "Sunday",
            "time": "23:58",
            "days_to_next_autobalancing": 28
        }
    }
}
