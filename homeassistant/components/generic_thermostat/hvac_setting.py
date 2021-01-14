from . import PID as pid_controller
import time
import logging


from homeassistant.components.climate.const import (
    # ATTR_HVAC_MODE,
    # ATTR_PRESET_MODE,
    # CURRENT_HVAC_COOL,
    # CURRENT_HVAC_HEAT,
    # CURRENT_HVAC_IDLE,
    # CURRENT_HVAC_OFF,
    HVAC_MODE_COOL,
    HVAC_MODE_HEAT,
    # HVAC_MODE_OFF,
    # PRESET_AWAY,
    # PRESET_NONE,
    # SUPPORT_PRESET_MODE,
    # SUPPORT_TARGET_TEMPERATURE,
)

from homeassistant.const import (
    ATTR_ENTITY_ID,
    # ATTR_TEMPERATURE,
    CONF_ENTITY_ID,
    # CONF_NAME,
    # EVENT_HOMEASSISTANT_START,
    # PRECISION_HALVES,
    # PRECISION_TENTHS,
    # PRECISION_WHOLE,
    # SERVICE_TURN_OFF,
    # SERVICE_TURN_ON,
    # STATE_OFF,
    # STATE_ON,
    # STATE_UNKNOWN,
    # STATE_UNAVAILABLE,
)

CONF_HVAC_MODE_INIT_TEMP = "initial_target_temp"
CONF_HVAC_MODE_MIN_TEMP = "min_temp"
CONF_HVAC_MODE_MAX_TEMP = "max_temp"
CONF_AWAY_TEMP = "away_temp"

# on_off thermostat
CONF_ON_OFF_MODE = "on_off_mode"
CONF_MIN_CYCLE_DURATION = "min_cycle_duration"
CONF_KEEP_ALIVE = "keep_alive"
CONF_HYSTERESIS_TOLERANCE_ON = "hysteresis_tolerance_on"
CONF_HYSTERESIS_TOLERANCE_OFF = "hysteresis_tolerance_off"

# PWM/PID controller
CONF_PWM_MODE = "PWM_mode"
CONF_PID_REFRESH_INTERVAL = "PID_interval"
CONF_DIFFERENCE = "difference"

CONF_KP = "kp"
CONF_KI = "ki"
CONF_KD = "kd"
CONF_PWM = "pwm"

CONF_AUTOTUNE = "autotune"
CONF_AUTOTUNE_CONTROL_TYPE = "autotune_control_type"
CONF_NOISEBAND = "noiseband"
CONF_AUTOTUNE_LOOKBACK = "autotune_lookback"
CONF_AUTOTUNE_STEP_SIZE = "tune_step_size"
# CONF_HEAT_METER = "heat_meter"

# weather compensating mode
CONF_WC_MODE = "WC_mode"
CONF_SENSOR_OUT = "sensor_out"
CONF_KA = "ka"
CONF_KB = "kb"


# Master mode
CONF_MASTER_MODE = "MASTER_mode"
CONF_SATELITES = "satelites"
CONF_GOAL = "goal"

_LOGGER = logging.getLogger(__name__)


class HVAC_Setting:
    def __init__(self, mode, conf):
        _LOGGER.debug("Confige hvac settings for mode : %s", mode)

        self.mode = mode
        self._hvac_settings = conf

        self.target_temperature = self._hvac_settings[CONF_HVAC_MODE_INIT_TEMP]
        self.min_target_temp = self._hvac_settings[CONF_HVAC_MODE_MIN_TEMP]
        self.max_target_temp = self._hvac_settings[CONF_HVAC_MODE_MAX_TEMP]
        self.away_temp = self._hvac_settings[CONF_AWAY_TEMP]

        self.entity_id = self._hvac_settings[CONF_ENTITY_ID]

        self._pwm = []
        self._on_off = []
        self._wc = []
        self._master = []
        self._pwm = self._hvac_settings.get(CONF_PWM_MODE)
        self._on_off = self._hvac_settings.get(CONF_ON_OFF_MODE)
        self._wc = self._hvac_settings.get(CONF_WC_MODE)
        self._master = self._hvac_settings.get(CONF_MASTER_MODE)

        self.pidAutotune = None
        self.pidController = None
        self.current_temperature = None
        self.control_output = {}
        self._autotune_state = False

        self.outdoor_temperature = None

        if self.is_hvac_on_off_mode:
            _LOGGER.debug("HVAC mode 'on_off' active")
            self.start_on_off()
        if self.is_master_mode:
            _LOGGER.debug("HVAC mode 'master' active")
            self.start_master()
        if self.is_hvac_pwm_mode:
            _LOGGER.debug("HVAC mode 'pwm' active")
            self.start_pid()
            self.control_output["pwm"] = 0
        if self.is_hvac_wc_mode:
            _LOGGER.debug("HVAC mode 'weather control' active")
            self.control_output["wc"] = 0

    def start_on_off(self):
        """set basic settings for hysteris mode"""
        _LOGGER.debug("Init on_off settings for mode : %s", self.mode)
        try:
            self._pwm[CONF_KEEP_ALIVE]
        except:
            self._pwm[CONF_KEEP_ALIVE] = None

    def start_master(self):
        """Init the master mode"""
        self._satelites = {}
        self._master_setpoint = 0

    def start_pid(self):
        """Init the PID controller"""
        _LOGGER.debug("Init pwm settings for mode : %s", self.mode)
        self._autotune_state = False
        self.pidAutotune = None

        difference = self._pwm[CONF_DIFFERENCE]
        kp, ki, kd = self.get_pid_param
        min_cycle_duration = self._pwm[CONF_PID_REFRESH_INTERVAL]

        self.pidController = pid_controller.PIDController(
            min_cycle_duration.seconds,
            kp,
            ki,
            kd,
            0,
            difference,
            time.time,
        )

        self.control_output["pid"] = 0

    def start_autotune(self, target_temp):
        """Init the autotune"""
        _LOGGER.debug("Init autotune settings for mode : %s", self.mode)
        self.target_temperature = target_temp
        self._autotune_state = True
        self.pidController = None

        min_cycle_duration = self._pwm[CONF_PID_REFRESH_INTERVAL]
        step_size = self._pwm[CONF_AUTOTUNE_STEP_SIZE]
        noiseband = self._pwm[CONF_NOISEBAND]
        autotune_lookback = self._pwm[CONF_AUTOTUNE_LOOKBACK]
        min_level, max_level = self.get_pid_limits
        self.pidAutotune = pid_controller.PIDAutotune(
            self.target_temperature,
            step_size,
            min_cycle_duration.seconds,
            autotune_lookback.seconds,
            min_level,
            max_level,
            noiseband,
            time.time,
        )
        _LOGGER.warning(
            "Autotune will run with the current Setpoint Value you set. "
            "Changes, submited after, doesn't have any effect until it's finished."
        )

    def pid_reset_time(self):
        """Reset the current time for PID to avoid overflow of the intergral part
        when switching between hvac modes"""
        autotune = self._pwm[CONF_AUTOTUNE]
        if autotune != "none" and self.pidAutotune:
            self.pidAutotune.reset_time()
        elif self.pidController:
            self.pidController.reset_time()

    def calculate(self, force=None):
        """Calculate the current control value for all activated modes"""
        if self.is_master_mode:
            """override the setpoint and current temperature by satelites when in master mode"""
            self.current_temperature = self.master_current_temp()
            self.target_temperature = self.master_setpoint()

        if self.is_hvac_pwm_mode:
            self.run_pid(force)
        if self.is_hvac_wc_mode:
            self.run_wc()

    def run_wc(self):
        """calcuate weather compension mode"""
        KA, KB = self.get_wc_param
        temp_diff = self.target_temperature - self.outdoor_temperature
        self.control_output["wc"] = temp_diff * KA + KB

    def run_pid(self, force=False):
        """calcuate the PID for current timestep"""
        autotune = self._pwm[CONF_AUTOTUNE]
        if self._autotune_state:
            _LOGGER.debug("Autotune mode")
            autotune_control_type = self._pwm[CONF_AUTOTUNE_CONTROL_TYPE]

            cycle_time = self._pwm[CONF_PID_REFRESH_INTERVAL]
            self._pwm[CONF_DIFFERENCE]
            min_level, max_level = self.get_pid_limits

            if self.pidAutotune.run(self.current_temperature):
                if autotune_control_type == "none":
                    params = self.pidAutotune.get_pid_parameters(autotune, True)
                else:
                    params = self.pidAutotune.get_pid_parameters(
                        autotune, False, autotune_control_type
                    )

                kp = params.Kp
                ki = params.Ki
                kd = params.Kd
                self.set_pid_param(kp, ki, kd)

                _LOGGER.warning(
                    "Set Kp, Ki, Kd. "
                    "Smart thermostat now runs on autotune PID Controller: %s,  %s,  %s",
                    kp,
                    ki,
                    kd,
                )

                self.pidController = pid_controller.PIDController(
                    cycle_time.seconds,
                    kp,
                    ki,
                    kd,
                    min_level,
                    max_level,
                    time.time,
                )
                self._autotune_state = False

            self.control_output["pid"] = self.pidAutotune.output
        else:
            self.control_output["pid"] = self.pidController.calc(
                self.current_temperature,
                self.target_temperature,
                force,
            )

        if self.mode == HVAC_MODE_COOL:
            self.control_output["pid"] *= -1

    @property
    def get_pid_limits(self):
        """Bandwitdh for control value"""
        difference = self._pwm[CONF_DIFFERENCE]
        if self.mode == HVAC_MODE_HEAT:
            min_level = 0
            max_level = difference
        elif self.mode == HVAC_MODE_COOL:
            min_level = -difference
            max_level = 0

        return [min_level, max_level]

    @property
    def get_pid_param(self):
        """Return the pid parameters of the thermostat."""
        kp = self._pwm[CONF_KP]
        ki = self._pwm[CONF_KI]
        kd = self._pwm[CONF_KD]
        return (kp, ki, kd)

    # @property
    # def check_heat_meter(self):
    #     # check if heater is specified
    #     try:
    #         self._pwm[CONF_HEAT_METER]
    #     except:
    #         self._pwm[CONF_HEAT_METER] = None

    @property
    def get_wc_param(self):
        """Return the wc parameters of the thermostat."""
        if self.is_hvac_wc_mode:
            ka = self._wc[CONF_KA]
            kb = self._wc[CONF_KB]
            return (ka, kb)
        else:
            return (None, None)

    def update_temperatures(self, current_temp, setpoint, outdoor_temp):
        """ set latest temps with call of function as data is not transferred when thermostat is off"""
        self.current_temperature = current_temp
        self.target_temperature = setpoint
        self.outdoor_temperature = outdoor_temp

    def set_pid_param(self, kp, ki, kd):
        """Set PID parameters."""
        self._pwm[CONF_KP] = kp
        self._pwm[CONF_KI] = ki
        self._pwm[CONF_KD] = kd
        # self._async_control_heating()
        # yield from self.async_update_ha_state()
        # await self.async_write_ha_state()

    @property
    def get_control_output(self):
        """Return the pid control output of the thermostat."""
        return sum(list(self.control_output.values()))

    @property
    def is_pwm_autotune(self):
        """Return if pid autotune is included."""
        autotune = self._pwm[CONF_AUTOTUNE]
        if autotune != "none":
            return True
        else:
            return False

    @property
    def is_satelite_allowed(self):
        """Return if pid autotune is running."""
        if self._pwm or self._wc and not self._master:
            return True
        else:
            return False

    @property
    def is_pwm_autotune_active(self):
        """Return if pid autotune is running."""
        if self._autotune_state:
            return True
        else:
            return False

    @property
    def is_hvac_pwm_mode(self):
        """return the control mode"""
        if self._pwm:
            return True
        else:
            return False

    @property
    def is_hvac_on_off_mode(self):
        """return the control mode"""
        if self._on_off:
            return True
        else:
            return False

    @property
    def is_hvac_wc_mode(self):
        """return the control mode"""
        if self._wc:
            return True
        else:
            return False

    @property
    def is_master_mode(self):
        """return the control mode"""
        if self._master:
            return True
        else:
            return False

    @property
    def is_hvac_switch_on_off(self):
        """check if on-off mode is active"""
        if self.is_hvac_on_off_mode or self.is_hvac_pwm_mode and self.get_pwm_mode == 0:
            return False
        else:
            return True

    @property
    def is_hvac_switch_modulating(self):
        """check if switch is switched on-off or proportional output is set"""
        if self.is_hvac_on_off_mode or self.is_hvac_pwm_mode and self.get_pwm_mode == 0:
            return True
        else:
            return False

    @property
    def get_variable_attr(self):
        """return attributes for climate entity"""
        tmp_dict = {}
        tmp_dict["target"] = self.get_target_temp
        tmp_dict["satelite_allowed"] = self.is_satelite_allowed

        if self.is_hvac_pwm_mode:
            tmp_dict["PID_values"] = self.get_pid_param
        if self.is_hvac_wc_mode:
            tmp_dict["ab_values"] = self.get_wc_param
        if not self.is_hvac_on_off_mode:
            tmp_dict["valve_pos"] = self.get_control_output
        if self.is_master_mode:
            tmp_dict["satelites"] = self.get_satelites

        return tmp_dict

    @property
    def get_away_temp(self):
        """return away temp for current hvac mode"""
        return self.away_temp

    @property
    def get_pwm_mode(self):
        """return pwm interval time"""
        if self.is_hvac_pwm_mode:
            return self._pwm[CONF_PWM]
        elif self.is_hvac_wc_mode:
            return self._wc[CONF_PWM]
        else:
            return None

    @property
    def get_difference(self):
        """get deadband range"""
        if self.is_hvac_pwm_mode:
            return self._pwm[CONF_DIFFERENCE]
        else:
            return None

    @property
    def get_hvac_switch(self):
        """return the switch entity"""
        return self.entity_id

    @property
    def get_wc_sensor(self):
        """return the sensor entity"""
        if self.is_hvac_wc_mode:
            return self._wc[CONF_SENSOR_OUT]
        else:
            return None

    @property
    def get_satelites(self):
        """return the satelite thermostats"""
        if self.is_master_mode:
            return self._master[CONF_SATELITES]
        else:
            return None

    @property
    def get_keep_alive(self):
        """return interval for recalcuate (control value)"""
        if self.is_hvac_on_off_mode:
            return self._on_off[CONF_KEEP_ALIVE]
        else:
            return self._pwm[CONF_PID_REFRESH_INTERVAL]

    # @property
    # def get_heat_meter(self):
    #     return self._pwm[CONF_HEAT_METER]

    @property
    def get_min_cycle(self):
        """minimum duration before recalcute"""
        if self.is_hvac_on_off_mode:
            return self._on_off[CONF_MIN_CYCLE_DURATION]
        elif self.is_hvac_pwm_mode:
            return self._pwm[CONF_PID_REFRESH_INTERVAL]

    @property
    def get_target_temp(self):
        """return target temperature"""
        return self.target_temperature

    def set_target_temperature(self, target_temp):
        """set new target temperature"""
        self.target_temperature = target_temp

    def set_current_temperature(self, current_temp):
        """set new current temperature"""
        self.current_temperature = current_temp

    def set_outdoor_temperature(self, current_temp):
        """set outdoor temperature"""
        self.outdoor_temperature = current_temp

    @property
    def get_target_temp_limits(self):
        """get range of allowed setpoint range"""
        return [self.min_target_temp, self.max_target_temp]

    @property
    def get_hysteris(self):
        """get bandwidth for on-off mode"""
        tolerance_on = self._on_off[CONF_HYSTERESIS_TOLERANCE_ON]
        tolerance_off = self._on_off[CONF_HYSTERESIS_TOLERANCE_OFF]

        return [tolerance_on, tolerance_off]

    def update_satelite(self, name, mode, setpoint, current, area, valve):
        """set new state of a satelite"""
        self._satelites[name] = {
            "mode": mode,
            "setpoint": setpoint,
            "current": current,
            "area": area,
            "valve_pos": valve,
        }

    def master_setpoint(self):
        """set setpoint based on satelites"""
        sum_area = 0
        sum_product = 0

        for _, data in self._satelites.items():
            if data["mode"] == self.mode:
                sum_area += data["area"]
                sum_product += data["area"] * data["setpoint"]
        if sum_area:
            self._master_setpoint = sum_product / sum_area
        else:
            self._master_setpoint = None
        return self._master_setpoint

    def master_current_temp(self):
        """set current temperature by satelites"""
        sum_area = 0
        sum_product = 0

        for _, data in self._satelites.items():
            if data["mode"] == self.mode:
                sum_area += data["area"]
                sum_product += data["area"] * data["current"]
        if sum_area:
            self._master_current = sum_product / sum_area
        else:
            self._master_current = None

        return self._master_current

    def master_valve_position(self):
        """get maximal valve opening"""
        valve_pos = 0

        for _, data in self._satelites.items():
            if data["mode"] == self.mode:
                valve_pos = max(valve_pos, data["valve_pos"])
        self._master_max_valve_pos = valve_pos
