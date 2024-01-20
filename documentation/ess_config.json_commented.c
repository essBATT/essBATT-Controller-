// Optimized for reading in Notepad++ with default setting. Fileending .c because comments have a readable visual format
// Hope this also works on github
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
        "smooth_voltage_based_(dis)charge_limits": 1, // Special behavior for voltage based charge- and discharge limits. Needs more explanation in a seperate documentation. If a voltage based limit is reached it keeps the limit instead of loosing it when the voltage might cross the "limit voltage" again
        "charge_limit_mode": "max_cell_only", // ["max_cell_only", "soc_only", "soc_and_max_cell"] Max cell only uses soc_based_charge_limit_soc_array and soc_based_charge_limit_current_array. soc_only uses soc_based_charge_limit_soc_array and soc_based_charge_limit_current_array. soc_and_max_cell uses both whichever condition is first met.
        "max_cell_voltage_charging": 3.56,
        "max_cell_voltage_charging_resume": 3.5,
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
        "discharge_limit_mode": "soc_and_min_cell",
        "min_cell_voltage_discharging": 3.1,
        "min_cell_voltage_discharging_resume": 3.25,
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
        "emergency_(dis)charge":{
            "use_emergency_(dis)charging": 1,
            "max_cell_voltage_for_emergency_discharge": 3.63,
            "min_cell_voltage_for_emergency_charge": 3.05,
            "emergency_(dis)charge_duration_minutes": 10
        }
    },
    "balancing_settings":{
        "balancing_complete_condition":{
            "min_cell_voltage_threshold": 3.48,
            "max_diff_voltage_between_min_and_max_cell": 0.01
        },
        "auto_balancing_settings":{
            "activate_auto_balancing": 1,
            "weekday": "Sunday",
            "time": "12:00",
            "days_to_next_autobalancing": 28
        }
    },
    "external_control_settings":{
        "allow_external_control_over_mqtt": 1,
        "time_format":"%H:%M",
        "date_format":"%d.%m.%Y",
        "mqtt_external_control_topics":{
            "charge_battery_to_SOC": "iobroker/ESS_External_Control/activate_charge_to_SOC_command",
            "activate_top_balancing_mode": "iobroker/ESS_External_Control/activate_balancing_command",
            "deactivate_discharge": "iobroker/ESS_External_Control/forbid_discharging",
            "deactivate_charge": "iobroker/ESS_External_Control/forbid_charging",
            "reboot_ess_controller": "iobroker/ESS_External_Control/reboot"
        }
    },
    "winter_mode":{
        "use_winter_mode": 1,
        "winter_mode_start_date": "01.11.",
        "winter_mode_end_date": "10.03.",
        "winter_min_SOC": 25,
        "winter_restart_multis_SOC": 70,
        "winter_inactive_charge_min_voltage": 3.17,
        "winter_inactive_charge_time_minutes": 30,
        "auto_balancing_settings":{
            "use_different_winter_settings": 1,
            "weekday": "Sunday",
            "time": "23:58",
            "days_to_next_autobalancing": 28
        }
    }
}
