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

CONF_HEAT_METER = "heat_meter"

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
        self._pwm = self._hvac_settings.get(CONF_PWM_MODE)
        self._on_off = self._hvac_settings.get(CONF_ON_OFF_MODE)

        self.pidAutotune = None
        self.pidController = None
        self.current_temperature = None
        self._autotune_state = False

        if self.is_hvac_pwm_mode:
            self.check_heat_meter
            self.start_pid()
        if self.is_hvac_on_off_mode:
            self.start_on_off()

    def start_on_off(self):
        _LOGGER.debug("Init on_off settings for mode : %s", self.mode)
        try:
            self._pwm[CONF_KEEP_ALIVE]
        except:
            self._pwm[CONF_KEEP_ALIVE] = None

    @property
    def check_heat_meter(self):
        try:
            self._pwm[CONF_HEAT_METER]
        except:
            self._pwm[CONF_HEAT_METER] = None

    def start_pid(self):
        _LOGGER.debug("Init pwm settings for mode : %s", self.mode)
        self._autotune_state = False
        self.pidAutotune = None

        difference = self._pwm[CONF_DIFFERENCE]
        kp, ki, kd = self.get_pid_param
        min_cycle_duration = self._pwm[CONF_PID_REFRESH_INTERVAL]

        self.pidController = pid_controller.PIDController(
            min_cycle_duration.seconds, kp, ki, kd, 0, difference, time.time
        )

        self.control_output = 0

    def start_autotune(self, target_temp):
        _LOGGER.debug("Init autotune settings for mode : %s", self.mode)
        self.target_temperature = target_temp
        self._autotune_state = True
        self.pidController = None

        difference = self._pwm[CONF_DIFFERENCE]
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

        self.control_output = 0

    def pid_reset_time(self):
        autotune = self._pwm[CONF_AUTOTUNE]
        if autotune != "none" and self.pidAutotune:
            self.pidAutotune.reset_time()
        elif self.pidController:
            self.pidController.reset_time()

    def run_pid(self, current, target, force=False):
        autotune = self._pwm[CONF_AUTOTUNE]
        self.current_temperature = current
        self.target_temperature = target

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

            self.control_output = self.pidAutotune.output
        else:
            self.control_output = self.pidController.calc(
                self.current_temperature, self.target_temperature, force
            )
        if self.mode == HVAC_MODE_COOL:
            self.control_output *= -1

    @property
    def get_pid_limits(self):
        difference = self._pwm[CONF_DIFFERENCE]
        if self.mode == HVAC_MODE_HEAT:
            min_level = 0
            max_level = difference
        elif self.mode == HVAC_MODE_COOL:
            min_level = -difference
            max_level = 0

        return [min_level, max_level]

    @property
    def get_variable_attr(self):
        tmp_dict = {}
        tmp_dict["target"] = self.get_target_temp

        return tmp_dict

    @property
    def get_pid_param(self):
        """Return the pid parameters of the thermostat."""
        kp = self._pwm[CONF_KP]
        ki = self._pwm[CONF_KI]
        kd = self._pwm[CONF_KD]
        return (kp, ki, kd)

    def set_pid_param(self, kp, ki, kd):
        """Set PID parameters."""
        self._pwm[CONF_KP] = kp
        self._pwm[CONF_KI] = ki
        self._pwm[CONF_KD] = kd
        # self._async_control_heating()
        # yield from self.async_update_ha_state()
        # await self.async_write_ha_state()

    @property
    def get_pid_control_output(self):
        """Return the pid control output of the thermostat."""
        return self.control_output

    @property
    def is_pwm_autotune(self):
        """Return if pid autotune is included."""
        autotune = self._pwm[CONF_AUTOTUNE]
        if autotune != "none":
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
        elif self._on_off:
            return False

    @property
    def is_hvac_on_off_mode(self):
        """return the control mode"""
        if self._pwm:
            return False
        elif self._on_off:
            return True

    @property
    def is_hvac_switch_on_off(self):
        if self.is_hvac_on_off_mode or self.is_hvac_pwm_mode and self.get_pwm_mode == 0:
            return False
        else:
            return True

    @property
    def is_hvac_switch_modulating(self):
        if self.is_hvac_on_off_mode or self.is_hvac_pwm_mode and self.get_pwm_mode == 0:
            return True
        else:
            return False

    @property
    def get_away_temp(self):
        return self.away_temp

    @property
    def get_pwm_mode(self):
        if self.is_hvac_pwm_mode:
            return self._pwm[CONF_PWM]
        else:
            return None

    @property
    def get_difference(self):
        if self.is_hvac_pwm_mode:
            return self._pwm[CONF_DIFFERENCE]
        else:
            return None

    @property
    def get_hvac_switch(self):
        """return the switch entity"""
        return self.entity_id

    @property
    def get_keep_alive(self):
        if self.is_hvac_on_off_mode:
            return self._on_off[CONF_KEEP_ALIVE]
        else:
            return self._pwm[CONF_PID_REFRESH_INTERVAL]

    @property
    def get_heat_meter(self):
        return self._pwm[CONF_HEAT_METER]

    @property
    def get_min_cycle(self):
        if self.is_hvac_on_off_mode:
            return self._on_off[CONF_MIN_CYCLE_DURATION]
        elif self.is_hvac_pwm_mode:
            return self._pwm[CONF_PID_REFRESH_INTERVAL]

    @property
    def get_target_temp(self):
        return self.target_temperature

    def set_target_temperature(self, target_temp):
        self.target_temperature = target_temp

    def set_current_temperature(self, current_temp):
        self.current_temperature = current_temp

    @property
    def get_target_temp_limits(self):
        return [self.min_target_temp, self.max_target_temp]

    @property
    def get_hysteris(self):
        tolerance_on = self._on_off[CONF_HYSTERESIS_TOLERANCE_ON]
        tolerance_off = self._on_off[CONF_HYSTERESIS_TOLERANCE_OFF]

        return [tolerance_on, tolerance_off]
