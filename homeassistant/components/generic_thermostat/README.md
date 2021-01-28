[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

# custom_components/smarter_thermostat

by borrowing and continuouing on code from:
- fabian degger (PID thermostat) originator PID control in generic thermostat, repo not present anymore
- aarmijo https://github.com/aarmijo/custom_components.git
- osi https://github.com/osirisinferi/custom_components.git (fetch)
- wout https://github.com/Wout-S/custom_components.git (fetch)
- DB-CL https://github.com/DB-CL/home-assistant/tree/new_generic_thermostat stale pull request with detailed seperation of hvac modes

## on-off and proportional controller thermostat

### Installation:
1. Go to <conf-dir> default /homeassistant/.homeassistant/ (it's where your configuration.yaml is)
2. Create <conf-dir>/custom_components/ directory if it does not already exist
3. Clone this repository content into <conf-dir>/custom_components/
4. Set up the smarter_thermostat and have fun

### Usage:
#### on-off:
the thermostat wll switch on or off dekending the setpoint and specified hysteris

#### proportional mode:
Two control modes are included to control the thermostat:
- PID
- Linear (weather compensating)

proportional controller will be called periodically.
If no pwm interval is defined, it will set the state of "heater" from 0 to "difference" value. Else, it will turn off and on the heater proportionally.

#### master mode:
With this mode a zoned heating system an be created. Each controlled room (satelite) can be linked to master. The setpoint, room temperature and valve position will be read from the satelites and will be averaged by the room area. This mode can be run with the same controls as the proportional mode (PID and Linear mode) and in addition a satelite valve position PID controller is present.

### Autotune:
WARNING: autotune is not tested, only code updates from above repo's are included as those seem to have fixed 'things'. I'm not able to these the autotune due to the slow reacting heating system. USE ON OWN RISK.

You can use the autotune feature to set the PID parameters.
The PID parmaters set by the autotune will overide the original PID values from the config and will be maintained when the restore_state is set the True. Restarting the climate with restore will maintain the autotune PID values. These are not written back to your climate yml file.
To save the parameters read the climate entity attributes, and copy the values to your config.


### Parameters:

* name (Required): Name of thermostat.
* target_sensor (optional): entity_id for a temperature sensor, target_sensor.state must be temperature. Not required when running in master mode: satelites are used.
* initial_hvac_mode (Optional): Set the initial operation mode. Valid values are off or cool or heat. Default is off
* initial preset mode (Optional): Set the default mode. Default is None
* room area (Optional): ratio (room area) for averiging thermostat when used as satelite with other satelites. Default is 0
* restore_from_old_state (Optional): restore certain old configuration and modes after restart. (setpoints, KP,KI,PD values, modes). Default is False
* sensor_stale_duration (Optional): safety to turn switches of when sensor has not updated wthin specified period

hvac mode by:
* heat: | cool: (at least 1 to be included)
with the data (as sub)::
* entity_id (Required): entity_id for heater/cool switch, must be a toggle or proportional device (pwm =0).
* min_temp (Optional): Set minimum set point available (default: 17 (heat) or 20 (cool)).
* max_temp (Optional): Set maximum set point available (default: 24(heat) or 35 (cool)).
* initial_target_temp (Optional): Set initial target temperature. Failure to set this variable will result in target temperature being set to null on startup.(default: 19(heat) or 28 (cool)).
* away_temp (Optional): Set the temperature used by “away_mode”. If this is not specified, away_mode feature will not get activated.

on_off_mode: (Optional) (sub of hvac mode)
with the data (as sub):
* hysteresis_tolerance_on (Optional): temperature offset to switch on. default is 0.5
* hysteresis_tolerance_off (Optional): temperature offset to switch off. default is 0.5
* min_cycle_duration (Optional): Min duration to change switch status. If this is not specified, min_cycle_duration feature will not get activated.
* keep_alive (Optional): Min duration to re-run an update cycle. Min duration to change switch status. If this is not specified, keep_alive feature will not get activated.

proportional_mode: (Optional) (sub of hvac mode)
with the data (as sub):
* pwm (Optional): Set period time for pwm signal in seconds. If it's not set, pwm is sending proportional value to switch. Default = 0
* difference (Optional): Set analog output offset to 0 (default 100). Example: If it's 500 the output Value can be everything between 0 and 500.
* minimal_difference (Optional): Set the minimal difference before activating swtich. To avoid very short off-on-off changes.
* control_interval (Required): interval that controller is updated.

controller modes: (PID, Linear, PID valve)

PID controller (sub of proportional mode)
* PID_mode: (Optional)(as sub of proportional mode)
with the data (as sub):
* kp (Required): Set PID parameter, p control value.
* ki (Required): Set PID parameter, i control value.
* kd (Required): Set PID parameter, d control value.

* autotune (Optional): Choose a string for autotune settings.  If it's not set autotune is disabled.

tuning_rules | Kp_divisor, Ki_divisor, Kd_divisor
------------ | -------------
"ziegler-nichols" | 34, 40, 160
"tyreus-luyben" | 44,  9, 126
"ciancone-marlin" | 66, 88, 162
"pessen-integral" | 28, 50, 133
"some-overshoot" | 60, 40,  60
"no-overshoot" | 100, 40,  60
"brewing" | 2.5, 6, 380

* autotune_control_type (Optional): (default none). Disables the
tuning rules and sets the Ziegler-Nichols control type     according to: https://en.wikipedia.org/wiki/Ziegler%E2%80%93Nichols_method

  Possible values: p, pi, pd, classic_pid, pessen_integral_rule,
                    some_overshoot, no_overshoot

* noiseband (Optional): (default 0.5) Set noiseband (float).Determines by how much the input value must overshoot/undershoot the setpoint before the state changes during autotune.
* autotune_lookback (Optional): (default 60s). The reference period in seconds for local minima/maxima.

#### configuration.yaml
on-off mode - heat only
```
climate:
  - platform: smarter_thermostat
    name: satelite1
    sensor: sensor.fake_sensor_1
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    room_area: 100
    precision: 0.5
    sensor_stale_duration:
      minutes: 20
    restore_from_old_state: False

    heat:
      entity_id: switch.fake_heater_switch
      min_temp: 15
      max_temp: 24
      initial_target_temp: 19
      away_temp: 12
        on_off_mode:
          hysteresis_tolerance_on: 0.5
          hysteresis_tolerance_off: 1
          min_cycle_duration:
            minutes: 5
          keep_alive:
            minutes: 3
```

on-off mode - heat onand cool

```
climate:
  - platform: smarter_thermostat
    name: satelite1
    sensor: sensor.fake_sensor_1
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    room_area: 100
    precision: 0.5
    sensor_stale_duration:
      minutes: 20
    restore_from_old_state: False

    heat:
      entity_id: switch.fake_heater_switch
      min_temp: 15
      max_temp: 24
      initial_target_temp: 19
      away_temp: 12
        on_off_mode:
          hysteresis_tolerance_on: 0.5
          hysteresis_tolerance_off: 1
          min_cycle_duration:
            minutes: 5
          keep_alive:
            minutes: 3
    cool:
      entity_id: switch.fake_cool_switch
      min_temp: 24
      max_temp: 32
      initial_target_temp: 25
      away_temp: 28
        on_off_mode:
          hysteresis_tolerance_on: 0.5
          hysteresis_tolerance_off: 1
          min_cycle_duration:
            minutes: 5
          keep_alive:
            minutes: 3
```

proportional mode

```
climate:
  - platform: smarter_thermostat
    name: satelite1
    sensor: sensor.fake_sensor_1
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    room_area: 100
    precision: 0.5
    sensor_stale_duration:
      minutes: 20
    restore_from_old_state: False

    heat:
      entity_id: switch.fake_heater_switch
      min_temp: 15
      max_temp: 24
      initial_target_temp: 19
      away_temp: 12
      proportional_mode:
        control_interval:
          minutes: 1
        difference: 100
        pwm:
          minutes: 10
        PID_mode:
          kp: 5
          ki: 0.001
          kd: 0
```

Linear mode (weather compensating)

```
      proportional_mode:
        control_interval:
          minutes: 1
        difference: 100
        pwm:
          minutes: 10
        WC_mode:
          sensor_out: sensor.fake_sensor_out
          ka: 1
          kb: -5
```

master - satelite mode

```
  - platform: generic_thermostat
    name: main_valve
    initial_hvac_mode: "off"
    initial_preset_mode: "none"

    precision: 0.5

    heat:
      entity_id: switch.fake_heater_master
      min_temp: 15
      max_temp: 24
      initial_target_temp: 19
      away_temp: 12
      proportional_mode:
        control_interval:
          minutes: 1
        difference: 100
        pwm:
          minutes: 10
        WC_mode:
          sensor_out: sensor.fake_sensor_out
          ka: 1
          kb: -5
        MASTER_mode:
          satelites: [satelite1,]
```
### Help

The python PID module:
[https://github.com/hirschmann/pid-autotune](https://github.com/hirschmann/pid-autotune)

PID controller explained. Would recommoned to read some of it:
[https://controlguru.com/table-of-contents/](https://controlguru.com/table-of-contents/)

PID controller and Ziegler-Nichols method:
[https://electronics.stackexchange.com/questions/118174/pid-controller-and-ziegler-nichols-method-how-to-get-oscillation-period](https://electronics.stackexchange.com/questions/118174/pid-controller-and-ziegler-nichols-method-how-to-get-oscillation-period)

Ziegler–Nichols Tuning:
[https://www.allaboutcircuits.com/projects/embedded-pid-temperature-control-part-6-zieglernichols-tuning/](https://www.allaboutcircuits.com/projects/embedded-pid-temperature-control-part-6-zieglernichols-tuning/)
