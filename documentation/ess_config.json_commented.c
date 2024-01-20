// Optimized for reading in Notepad++ with default setting. Fileending .c because comments have a readable visual format
// Hope this also works on github
{
    "config_version": 1.0,
    "ess_mode": "ess_mode_2_normal_operation",
    "vrm_id": "YOUR VRM ID",
    "check_ess_config_changes_while_running": 0,
    "keepalive_get_all_topics": 0,
    "control_update_rate": 2.0,
    "debug_level": "INFO",
    "mqtt_username": "YOUR MQTT SERVER USERNAME",
    "mqtt_password": "YOUR MQTT SERVER PASSWORD",
    "mqtt_server_COM_port": 1883,
    "script_alive_logging_interval": 86400,
    "ess_mode_2_settings":{
        "grid_power_setpoint_2700": 1,
        "max_power_fed_to_loads_2704": 5000,
        "max_battery_discharge_current": 33,
        "max_battery_charge_current_2705": 33,
        "max_system_grid_feed_in_power_2706": 0,
        "feed_excess_dc_coupled_pv_into_grid_2707": 0,
        "feed_excess_ac_coupled_pv_into_grid_2708": 0
    },
    "battery_settings":{
        "compensate_current_limit_violations": 0,
        "smooth_voltage_based_(dis)charge_limits": 1,
        "charge_limit_mode": "max_cell_only",
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