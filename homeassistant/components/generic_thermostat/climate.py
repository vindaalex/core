"""Generic thermostat.
Incl support for smart (PID) thermostat units.
For more details about this platform, please refer to the documentation at
https: // github.com / fabiannydegger / custom_components / """

# check use of min_cycle_duration vs keep_alive. duplicate and not proper included

import asyncio
import logging
import datetime
from typing import Callable, Dict
import time
from . import PID as pid_controller

import voluptuous as vol

from homeassistant.components.climate import PLATFORM_SCHEMA, ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    CURRENT_HVAC_COOL,
    CURRENT_HVAC_HEAT,
    CURRENT_HVAC_IDLE,
    CURRENT_HVAC_OFF,
    HVAC_MODE_COOL,
    HVAC_MODE_HEAT,
    HVAC_MODE_OFF,
    PRESET_AWAY,
    PRESET_NONE,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_ENTITY_ID,
    CONF_NAME,
    EVENT_HOMEASSISTANT_START,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
)
from homeassistant.core import DOMAIN as HA_DOMAIN, CoreState, callback
from homeassistant.helpers import condition
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

# from homeassistant.helpers.update_coordinator.DataUpdateCoordinator import (
#     async_remove_listener,
# )
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.restore_state import RestoreEntity

from . import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Generic Thermostat"
DEFAULT_TARGET_TEMP_HEAT = 19.0
DEFAULT_TARGET_TEMP_COOL = 28.0
DEFAULT_MAX_TEMP_HEAT = 24
DEFAULT_MIN_TEMP_HEAT = 17
DEFAULT_MAX_TEMP_COOL = 35
DEFAULT_MIN_TEMP_COOL = 20

DEFAULT_INITIAL_HVAC_MODE = HVAC_MODE_OFF
DEFAULT_INITIAL_PRESET_MODE = PRESET_NONE

DEFAULT_OLD_STATE = False

# on_off mode
DEFAULT_HYSTERESIS_TOLERANCE = 0.5

# PWM/PID controller
DEFAULT_DIFFERENCE = 100
DEFAULT_PWM = 0

DEFAULT_AUTOTUNE = "none"
DEFAULT_NOISEBAND = 0.5
DEFAULT_HEAT_METER = "none"
DEFAULT_AUTOTUNE_CONTROL_TYPE = "none"

CONF_SENSOR = "sensor"
CONF_INITIAL_HVAC_MODE = "initial_hvac_mode"
CONF_INITIAL_PRESET_MODE = "initial_preset_mode"

CONF_HVAC_MODE_MIN_TEMP = "min_temp"
CONF_HVAC_MODE_MAX_TEMP = "max_temp"
CONF_HVAC_MODE_INIT_TEMP = "initial_target_temp"
CONF_AWAY_TEMP = "away_temp"
CONF_PRECISION = "precision"

CONF_ENABLE_OLD_STATE = "restore_from_old_state"
CONF_STALE_DURATION = "sensor_stale_duration"

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
CONF_NOISEBAND = "noiseband"
CONF_HEAT_METER = "heat_meter"
CONF_AUTOTUNE_LOOKBACK = "autotune_lookback"
CONF_AUTOTUNE_CONTROL_TYPE = "autotune_control_type"

# valve control (pid/pwm)
SERVICE_SET_VALUE = "set_value"
ATTR_VALUE = "value"
PLATFORM_INPUT_NUMBER = "input_number"

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE

SUPPORTED_HVAC_MODES = [HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_OFF]
SUPPORTED_PRESET_MODES = [PRESET_NONE, PRESET_AWAY]


def validate_initial_preset_mode(*keys: str) -> Callable:
    """If an initial preset mode has been set, check if the values are set in both modes."""

    def validate_by_mode(obj: Dict, preset: str, config_preset: str):
        """Use a helper to validate mode by mode."""
        if HVAC_MODE_HEAT in obj.keys() and config_preset not in obj[HVAC_MODE_HEAT]:
            raise vol.Invalid(
                "The preset {preset} has been set as initial preset but the {config_preset} is not present on {HVAC_MODE_HEAT} mode"
            )
        if HVAC_MODE_COOL in obj.keys() and config_preset not in obj[HVAC_MODE_COOL]:
            raise vol.Invalid(
                "The preset {preset} has been set as initial preset but the {config_preset} is not present on {HVAC_MODE_COOL} mode"
            )

    def validate(obj: Dict) -> Dict:
        """Check this condition."""
        if CONF_INITIAL_PRESET_MODE in obj and obj[CONF_INITIAL_PRESET_MODE] != "none":
            if obj[CONF_INITIAL_PRESET_MODE] == PRESET_AWAY:
                validate_by_mode(obj, PRESET_AWAY, CONF_AWAY_TEMP)
        return obj

    return validate


def validate_initial_hvac_mode(*keys: str) -> Callable:
    """If an initial hvac mode has been set, check if this mode has been configured."""

    def validate(obj: Dict) -> Dict:
        """Check this condition."""
        if (
            CONF_INITIAL_HVAC_MODE in obj
            and obj[CONF_INITIAL_HVAC_MODE] != HVAC_MODE_OFF
            and obj[CONF_INITIAL_HVAC_MODE] not in obj.keys()
        ):
            raise vol.Invalid(
                "You cannot set an initial HVAC mode if you did not configure this mode {obj[CONF_INITIAL_HVAC_MODE]}"
            )
        return obj

    return validate


def check_presets_in_both_modes(*keys: str) -> Callable:
    """If one preset is set on one mode, then this preset is enabled and check it on the other modes."""

    def validate_by_preset(obj: Dict, conf: str):
        """Check this condition."""
        if conf in obj[HVAC_MODE_HEAT] and conf not in obj[HVAC_MODE_COOL]:
            raise vol.Invalid(
                "{preset} is set for {HVAC_MODE_HEAT} but not for {HVAC_MODE_COOL}"
            )
        if conf in obj[HVAC_MODE_COOL] and conf not in obj[HVAC_MODE_HEAT]:
            raise vol.Invalid(
                "{preset} is set for {HVAC_MODE_COOL} but not for {HVAC_MODE_HEAT}"
            )

    def validate(obj: Dict) -> Dict:
        if HVAC_MODE_HEAT in obj.keys() and HVAC_MODE_COOL in obj.keys():
            validate_by_preset(obj, CONF_AWAY_TEMP)
        return obj

    return validate


PLATFORM_SCHEMA = vol.All(
    cv.has_at_least_one_key(HVAC_MODE_HEAT, HVAC_MODE_COOL),
    validate_initial_hvac_mode(),
    check_presets_in_both_modes(),
    validate_initial_preset_mode(),
    PLATFORM_SCHEMA.extend(
        {
            vol.Required(CONF_SENSOR): cv.entity_id,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(
                CONF_INITIAL_HVAC_MODE, default=DEFAULT_INITIAL_HVAC_MODE
            ): vol.In(SUPPORTED_HVAC_MODES),
            vol.Optional(
                CONF_INITIAL_PRESET_MODE, default=DEFAULT_INITIAL_PRESET_MODE
            ): vol.In(SUPPORTED_PRESET_MODES),
            vol.Optional(CONF_PRECISION): vol.In(
                [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]
            ),
            vol.Optional(HVAC_MODE_HEAT): vol.Schema(
                {
                    vol.Required(CONF_ENTITY_ID): cv.entity_id,
                    vol.Required(
                        CONF_HVAC_MODE_MIN_TEMP, default=DEFAULT_MIN_TEMP_HEAT
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_HVAC_MODE_MAX_TEMP, default=DEFAULT_MAX_TEMP_HEAT
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_HVAC_MODE_INIT_TEMP, default=DEFAULT_TARGET_TEMP_HEAT
                    ): vol.Coerce(float),
                    vol.Optional(CONF_AWAY_TEMP): vol.Coerce(float),
                    # on_off
                    vol.Optional(CONF_ON_OFF_MODE): vol.Schema(
                        {
                            vol.Optional(
                                CONF_HYSTERESIS_TOLERANCE_ON,
                                default=DEFAULT_HYSTERESIS_TOLERANCE,
                            ): vol.Coerce(float),
                            vol.Optional(
                                CONF_HYSTERESIS_TOLERANCE_OFF,
                                default=DEFAULT_HYSTERESIS_TOLERANCE,
                            ): vol.Coerce(float),
                            vol.Optional(CONF_MIN_CYCLE_DURATION): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(CONF_KEEP_ALIVE): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                        }
                    ),
                    # PID"
                    vol.Optional(CONF_PWM_MODE): vol.Schema(
                        {
                            vol.Required(CONF_KP): vol.Coerce(float),
                            vol.Required(CONF_KI): vol.Coerce(float),
                            vol.Required(CONF_KD): vol.Coerce(float),
                            vol.Required(CONF_PID_REFRESH_INTERVAL): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(
                                CONF_DIFFERENCE, default=DEFAULT_DIFFERENCE
                            ): vol.Coerce(float),
                            vol.Optional(CONF_PWM, default=DEFAULT_PWM): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(
                                CONF_AUTOTUNE, default=DEFAULT_AUTOTUNE
                            ): cv.string,
                            vol.Optional(
                                CONF_NOISEBAND, default=DEFAULT_NOISEBAND
                            ): vol.Coerce(float),
                            vol.Optional(CONF_HEAT_METER): cv.entity_id,
                            vol.Optional(CONF_AUTOTUNE_LOOKBACK): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(
                                CONF_AUTOTUNE_CONTROL_TYPE,
                                default=DEFAULT_AUTOTUNE_CONTROL_TYPE,
                            ): cv.string,
                        }
                    ),
                }
            ),
            vol.Optional(HVAC_MODE_COOL): vol.Schema(
                {
                    vol.Required(CONF_ENTITY_ID): cv.entity_id,
                    vol.Required(
                        CONF_HVAC_MODE_MIN_TEMP, default=DEFAULT_MIN_TEMP_COOL
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_HVAC_MODE_MAX_TEMP, default=DEFAULT_MAX_TEMP_COOL
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_HVAC_MODE_INIT_TEMP, default=DEFAULT_TARGET_TEMP_COOL
                    ): vol.Coerce(float),
                    vol.Optional(CONF_AWAY_TEMP): vol.Coerce(float),
                    # on_off
                    vol.Optional(CONF_ON_OFF_MODE): vol.Schema(
                        {
                            vol.Optional(
                                CONF_HYSTERESIS_TOLERANCE_ON,
                                default=DEFAULT_HYSTERESIS_TOLERANCE,
                            ): vol.Coerce(float),
                            vol.Optional(
                                CONF_HYSTERESIS_TOLERANCE_OFF,
                                default=DEFAULT_HYSTERESIS_TOLERANCE,
                            ): vol.Coerce(float),
                            vol.Optional(CONF_MIN_CYCLE_DURATION): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(CONF_KEEP_ALIVE): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                        }
                    ),
                    # PID
                    vol.Optional(CONF_PWM_MODE): vol.Schema(
                        {
                            vol.Required(CONF_KP): vol.Coerce(float),
                            vol.Required(CONF_KI): vol.Coerce(float),
                            vol.Required(CONF_KD): vol.Coerce(float),
                            vol.Required(CONF_PID_REFRESH_INTERVAL): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(
                                CONF_DIFFERENCE, default=DEFAULT_DIFFERENCE
                            ): vol.Coerce(float),
                            vol.Optional(CONF_PWM, default=DEFAULT_PWM): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(
                                CONF_AUTOTUNE, default=DEFAULT_AUTOTUNE
                            ): cv.string,
                            vol.Optional(
                                CONF_NOISEBAND, default=DEFAULT_NOISEBAND
                            ): vol.Coerce(float),
                            vol.Optional(CONF_HEAT_METER): cv.entity_id,
                            vol.Optional(CONF_AUTOTUNE_LOOKBACK): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(
                                CONF_AUTOTUNE_CONTROL_TYPE,
                                default=DEFAULT_AUTOTUNE_CONTROL_TYPE,
                            ): cv.string,
                        }
                    ),
                }
            ),
            vol.Optional(CONF_STALE_DURATION): vol.All(
                cv.time_period, cv.positive_timedelta
            ),
            vol.Optional(CONF_ENABLE_OLD_STATE, default=DEFAULT_OLD_STATE): cv.boolean,
        }
    ),
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the generic thermostat platform."""

    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)

    name = config.get(CONF_NAME)
    sensor_entity_id = config.get(CONF_SENSOR)
    initial_hvac_mode = config.get(CONF_INITIAL_HVAC_MODE)
    precision = config.get(CONF_PRECISION)
    unit = hass.config.units.temperature_unit
    initial_preset_mode = config.get(CONF_INITIAL_PRESET_MODE)

    sensor_stale_duration = config.get(CONF_STALE_DURATION)
    enable_old_state = config.get(CONF_ENABLE_OLD_STATE)
    heat_conf = config.get(HVAC_MODE_HEAT)
    cool_conf = config.get(HVAC_MODE_COOL)

    heat_pwm = None
    cool_pwm = None
    heat_on_off = None
    cool_on_off = None

    if heat_conf:
        heat_pwm = heat_conf.get(CONF_PWM_MODE)
        heat_on_off = heat_conf.get(CONF_ON_OFF_MODE)
    if cool_conf:
        cool_pwm = cool_conf.get(CONF_PWM_MODE)
        cool_on_off = cool_conf.get(CONF_ON_OFF_MODE)

    enabled_hvac_modes = []
    enabled_pwm_modes = []
    enabled_on_off_modes = []

    # Append the enabled hvac modes to the list
    if heat_conf:
        enabled_hvac_modes.append(HVAC_MODE_HEAT)
        if heat_on_off:
            enabled_on_off_modes.append(HVAC_MODE_HEAT)
        if heat_pwm:
            enabled_pwm_modes.append(HVAC_MODE_HEAT)

    if cool_conf:
        enabled_hvac_modes.append(HVAC_MODE_COOL)
        if cool_on_off:
            enabled_on_off_modes.append(HVAC_MODE_COOL)
        if cool_pwm:
            enabled_pwm_modes.append(HVAC_MODE_COOL)

    async_add_entities(
        [
            GenericThermostat(
                name,
                unit,
                precision,
                sensor_entity_id,
                heat_conf,
                cool_conf,
                heat_pwm,
                cool_pwm,
                heat_on_off,
                cool_on_off,
                enabled_hvac_modes,
                enabled_pwm_modes,
                enabled_on_off_modes,
                initial_hvac_mode,
                initial_preset_mode,
                enable_old_state,
                sensor_stale_duration,
            )
        ]
    )


class GenericThermostat(ClimateEntity, RestoreEntity):
    """Representation of a Generic Thermostat device."""

    def __init__(
        self,
        name,
        unit,
        precision,
        sensor_entity_id,
        heat_conf,
        cool_conf,
        heat_pwm,
        cool_pwm,
        heat_on_off,
        cool_on_off,
        enabled_hvac_modes,
        enabled_pwm_modes,
        enabled_on_off_modes,
        initial_hvac_mode,
        initial_preset_mode,
        enable_old_state,
        sensor_stale_duration,
    ):
        """Initialize the thermostat."""
        self._name = name
        self._sensor_entity_id = sensor_entity_id
        self._temp_precision = precision
        self._unit = unit
        self._hvac_mode = initial_hvac_mode
        self._preset_mode = initial_preset_mode
        self._enabled_hvac_mode = enabled_hvac_modes
        self._enabled_pwm_mode = enabled_pwm_modes
        self._enabled_on_off_mode = enabled_on_off_modes
        self._enable_old_state = enable_old_state
        self._sensor_stale_duration = sensor_stale_duration
        self._emergency_stop = False

        if self._is_heat_enabled:
            self._heat_conf = heat_conf
            self._target_temp_heat = self._heat_conf[CONF_HVAC_MODE_INIT_TEMP]
            self._heater_entity_id = self._heat_conf[CONF_ENTITY_ID]
            if self._is_on_off_heat_enabled:
                self._heat_on_off = heat_on_off
            else:
                self._heat_on_off = None
            if self._is_pwm_heat_enabled:
                self._heat_pwm = heat_pwm
                self.heat_pidController = None
                self.heat_pidAutotune = None
            else:
                self._heat_pwm = None
            _LOGGER.debug(
                "Heat mode enabled; target_temp_heat: %s, entity_id: %s",
                self._target_temp_heat,
                self._heater_entity_id,
            )
        else:
            self._heat_conf = None
            self._target_temp_heat = None
            self._heater_entity_id = None

        if self._is_cool_enabled:
            self._cool_conf = cool_conf
            self._target_temp_cool = self._cool_conf[CONF_HVAC_MODE_INIT_TEMP]
            self._ac_entity_id = self._cool_conf[CONF_ENTITY_ID]
            if self._is_on_off_cool_enabled:
                self._cool_on_off = cool_on_off
            else:
                self._cool_on_off = None
            if self._is_pwm_cool_enabled:
                self._cool_pwm = cool_pwm
                self.cool_pidController = None
                self.cool_pidAutotune = None
            else:
                self._cool_pwm = None
            _LOGGER.debug(
                "Cool mode enabled; _target_temp_cool: %s, entity_id: %s",
                self._target_temp_cool,
                self._ac_entity_id,
            )
        else:
            self._cool_conf = None
            self._target_temp_cool = None
            self._ac_entity_id = None

        self._current_temperature = None
        self._temp_lock = asyncio.Lock()

    async def async_added_to_hass(self):
        """Run when entity about to be added.

        Attach the listeners.
        """
        await super().async_added_to_hass()

        # Add listeners to track changes from the sensor and the heater's switch
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._sensor_entity_id],
                self._async_sensor_temperature_changed,
            )
        )
        if self._is_heat_enabled:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._heater_entity_id],
                    self._async_switch_device_changed,
                )
            )
        if self._is_cool_enabled:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [self._ac_entity_id], self._async_switch_device_changed
                )
            )

        if self._sensor_stale_duration:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_check_sensor_not_responding,
                    self._sensor_stale_duration,
                )
            )

        @callback
        def _async_startup(*_):
            """Init on startup."""
            sensor_state = self.hass.states.get(self._sensor_entity_id)
            if sensor_state and sensor_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
            ):
                self._async_update_current_temp(sensor_state)
                self.async_write_ha_state()

        if self.hass.state == CoreState.running:
            _async_startup()
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

        # Check if we have an old state, if so, restore it
        old_state = await self.async_get_last_state()

        if self._enable_old_state and old_state is not None:
            _LOGGER.debug("Old state stored : %s", old_state)
            old_preset_mode = old_state.attributes.get(ATTR_PRESET_MODE)
            old_hvac_mode = old_state.state
            old_temperature = old_state.attributes.get(ATTR_TEMPERATURE)
            _LOGGER.debug(
                "Old state preset mode %s, hvac mode %s, temperature %s",
                old_preset_mode,
                old_hvac_mode,
                old_temperature,
            )

            # old_heat_conf = old_state.attributes.get(HVAC_MODE_HEAT)
            # old_cool_on_off_conf = old_heat_conf.get(CONF_ON_OFF_MODE)
            # old_cool_pwm_conf = old_heat_conf.get(CONF_PWM_MODE)

            # old_cool_conf = old_state.attributes.get(HVAC_MODE_COOL)
            # old_cool_on_off_conf = old_cool_conf.get(CONF_ON_OFF_MODE)
            # old_cool_pwm_conf = old_cool_conf.get(CONF_PWM_MODE)

            if old_preset_mode is not None and old_preset_mode in self.preset_modes:
                self._preset_mode = old_preset_mode

            if old_hvac_mode is not None and old_hvac_mode in self.hvac_modes:
                self._hvac_mode = old_hvac_mode

                # Restore the target temperature
                if old_hvac_mode == HVAC_MODE_COOL:
                    min_temp = self._cool_conf[CONF_HVAC_MODE_MIN_TEMP]
                    max_temp = self._cool_conf[CONF_HVAC_MODE_MAX_TEMP]
                    if (
                        old_temperature is not None
                        and min_temp <= old_temperature <= max_temp
                    ):
                        self._target_temp_cool = old_temperature
                elif old_hvac_mode == HVAC_MODE_HEAT:
                    min_temp = self._heat_conf[CONF_HVAC_MODE_MIN_TEMP]
                    max_temp = self._heat_conf[CONF_HVAC_MODE_MAX_TEMP]
                    if (
                        old_temperature is not None
                        and min_temp <= old_temperature <= max_temp
                    ):
                        self._target_temp_heat = old_temperature
        # Set default state to off
        if not self._hvac_mode:
            self._hvac_mode = HVAC_MODE_OFF
        # await self._async_operate()

    async def async_set_hvac_mode(self, hvac_mode):
        """Set hvac mode."""
        # No changes have been made
        if self._hvac_mode == hvac_mode:
            return
        if hvac_mode not in self.hvac_modes:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return
        _LOGGER.debug("HVAC mode changed to %s", hvac_mode)
        self._hvac_mode = hvac_mode

        # switches off
        if self._hvac_mode == HVAC_MODE_OFF:
            _LOGGER.debug("HVAC mode is OFF. Turn the devices OFF and exit")
            if self._is_heat_enabled and self._is_heater_active:
                await self._async_heater_turn_off()
            if self._is_cool_enabled and self._is_ac_active:
                await self._async_ac_turn_off()
            return
        if self._hvac_mode == HVAC_MODE_HEAT and self._is_cool_enabled:
            await self._async_ac_turn_off(force=True)
        elif self._hvac_mode == HVAC_MODE_COOL and self._is_heat_enabled:
            await self._async_heater_turn_off(force=True)

        # update lisntener
        self._update_keep_alive()

        # when mode is pwm
        if self._is_pwm_active:
            self._set_pid_controller()
        await self._async_operate()

        # Ensure we update the current operation after changing the mode
        self.async_write_ha_state()

    def _update_keep_alive(self):
        # remove_listener(self._async_operate)
        if self._hvac_mode != HVAC_MODE_OFF:
            _LOGGER.debug("update 'keep alive' for %s", self._hvac_mode)
            keep_alive = None
            if self._is_pwm_active:
                if self._hvac_mode == HVAC_MODE_HEAT:
                    keep_alive = self._heat_pwm[CONF_PID_REFRESH_INTERVAL]
                else:
                    keep_alive = self._cool_pwm[CONF_PID_REFRESH_INTERVAL]
            else:
                try:
                    if self._hvac_mode == HVAC_MODE_HEAT:
                        keep_alive = self._heat_on_off[CONF_KEEP_ALIVE]
                    else:
                        keep_alive = self._cool_on_off[CONF_KEEP_ALIVE]
                except:
                    _LOGGER.debug("no 'keep alive' for %s", self._hvac_mode)

            if keep_alive:
                self.async_on_remove(
                    async_track_time_interval(
                        self.hass, self._async_operate, keep_alive
                    )
                )

    def _set_pid_controller(self):
        if self._hvac_mode == HVAC_MODE_HEAT:
            autotune = self._heat_pwm[CONF_AUTOTUNE]

            if not self.heat_pidAutotune and not self.heat_pidController:
                difference = self._heat_pwm[CONF_DIFFERENCE]
                kp = self._heat_pwm[CONF_KP]
                ki = self._heat_pwm[CONF_KI]
                kd = self._heat_pwm[CONF_KD]
                min_cycle_duration = self._heat_pwm[CONF_PID_REFRESH_INTERVAL]
                # pwm = self._heat_pwm[CONF_PWM]

                noiseband = self._heat_pwm[CONF_NOISEBAND]
                try:
                    self._heat_pwm[CONF_HEAT_METER]
                except:
                    self._heat_pwm[CONF_HEAT_METER] = None
                autotune_lookback = self._heat_pwm[CONF_AUTOTUNE_LOOKBACK]
                # autotune_control_type = self._heat_pwm[CONF_AUTOTUNE_CONTROL_TYPE]
                if autotune != "none":
                    self.heat_pidAutotune = pid_controller.PIDAutotune(
                        self._target_temp_heat,
                        difference,
                        min_cycle_duration.seconds,
                        autotune_lookback.seconds,
                        0,
                        difference,
                        noiseband,
                        time.time,
                    )
                    _LOGGER.warning(
                        "Autotune will run with the current Setpoint Value you set. "
                        "Changes, submited after, doesn't have any effect until it's finished."
                    )
                else:
                    self.heat_pidController = pid_controller.PIDController(
                        min_cycle_duration.seconds, kp, ki, kd, 0, difference, time.time
                    )
            else:
                if autotune != "none":
                    self.heat_pidAutotune.reset_time()
                else:
                    self.heat_pidController.reset_time()
        elif self._hvac_mode == HVAC_MODE_COOL:
            autotune = self._cool_pwm[CONF_AUTOTUNE]
            if autotune != "none":
                if self.cool_pidAutotune:
                    self.cool_pidAutotune.reset_time()
            else:
                if self.cool_pidController:
                    self.cool_pidController.reset_time()

            if not self.cool_pidAutotune and not self.cool_pidController:
                difference = self._cool_pwm[CONF_DIFFERENCE]
                kp = self._cool_pwm[CONF_KP]
                ki = self._cool_pwm[CONF_KI]
                kd = self._cool_pwm[CONF_KD]
                min_cycle_duration = self._cool_pwm[CONF_PID_REFRESH_INTERVAL]
                # pwm = self._cool_pwm[CONF_PWM]

                noiseband = self._cool_pwm[CONF_NOISEBAND]
                # heat_meter_entity_id = self._cool_pwm[CONF_HEAT_METER]
                autotune_lookback = self._cool_pwm[CONF_AUTOTUNE_LOOKBACK]
                # autotune_control_type = self._cool_pwm[CONF_AUTOTUNE_CONTROL_TYPE]
                if autotune != "none":
                    self.cool_pidAutotune = pid_controller.PIDAutotune(
                        self._target_temp_heat,
                        difference,
                        min_cycle_duration.seconds,
                        autotune_lookback.seconds,
                        -difference,
                        0,
                        noiseband,
                        time.time,
                    )
                    _LOGGER.warning(
                        "Autotune will run with the current Setpoint Value you set. "
                        "Changes, submited after, doesn't have any effect until it's finished."
                    )
                else:
                    self.cool_pidController = pid_controller.PIDController(
                        min_cycle_duration.seconds,
                        kp,
                        ki,
                        kd,
                        -difference,
                        0,
                        time.time,
                    )
            else:
                if autotune != "none":
                    self.cool_pidAutotune.reset_time()
                else:
                    self.cool_pidController.reset_time()

        self.time_changed = time.time()
        # self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)

        if hvac_mode is None:
            hvac_mode = self._hvac_mode
        elif hvac_mode not in self.hvac_modes:
            _LOGGER.warning(
                "Try to update temperature to %s for mode %s but this mode is not enabled",
                temperature,
                hvac_mode,
            )
            return

        if hvac_mode is None or hvac_mode == HVAC_MODE_OFF:
            _LOGGER.warning("You cannot update temperature for OFF mode")
            return

        _LOGGER.debug("Temperature updated to %s for mode %s", temperature, hvac_mode)

        if hvac_mode == HVAC_MODE_COOL:
            self._target_temp_cool = temperature
        if hvac_mode == HVAC_MODE_HEAT:
            self._target_temp_heat = temperature

        if (
            self.preset_mode == PRESET_AWAY
        ):  # when preset mode is away, change the temperature but do not operate
            _LOGGER.debug(
                "Preset mode away when temperature is updated : skipping operate"
            )
            return

        await self._async_operate()
        self.async_write_ha_state()

    async def _async_sensor_temperature_changed(self, event):
        """Handle temperature changes."""
        new_state = event.data.get("new_state")
        _LOGGER.debug("Sensor temperature updated to %s", new_state.state)
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            await self._activate_emergency_stop()
            return

        self._async_update_current_temp(new_state)
        # if pid/pwm mode is active: do not call operate but let pid/pwm cycle handle it
        if not self._is_pwm_active:
            await self._async_operate(sensor_changed=True)
        self.async_write_ha_state()

    async def _async_check_sensor_not_responding(self, now=None):
        """Check if the sensor has emitted a value during the allowed stale period."""

        sensor_state = self.hass.states.get(self._sensor_entity_id)

        if (
            datetime.datetime.now(datetime.timezone.utc) - sensor_state.last_updated
            > self._sensor_stale_duration
        ):
            _LOGGER.debug("Time is %s, last changed is %s, stale duration is %s")
            _LOGGER.warning("Sensor is stalled, call the emergency stop")
            await self._activate_emergency_stop()

        return

    @callback
    def _async_switch_device_changed(self, event):
        """Handle device switch state changes."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        entity_id = event.data.get("entity_id")
        _LOGGER.debug(
            "Switch of %s changed from %s to %s", entity_id, old_state, new_state
        )
        if self._is_pwm_active:
            if self._hvac_mode == HVAC_MODE_HEAT:
                heat_meter_entity_id = self._heat_pwm[CONF_HEAT_METER]
            elif self._hvac_mode == HVAC_MODE_COOL:
                heat_meter_entity_id = self._cool_pwm[CONF_HEAT_METER]

            if heat_meter_entity_id:
                meter_attributes = {
                    "friendly_name": "Valve position",
                    "icon": "mdi:radiator",
                    "unit_of_measurement": "%",
                }
                if new_state is None:
                    self.hass.states.async_set(
                        heat_meter_entity_id, 0, meter_attributes
                    )
                else:
                    self.hass.states.async_set(
                        heat_meter_entity_id,
                        round(self.control_output, 1),
                        meter_attributes,
                    )
        if new_state is None:
            return
        self.async_write_ha_state()

    @callback
    def _async_update_current_temp(self, state):
        """Update thermostat with latest state from sensor."""
        try:
            _LOGGER.debug("Current temperature updated to %s", float(state.state))
            self._emergency_stop = False
            self._current_temperature = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from sensor: %s", ex)

    async def _async_operate(self, time=None, sensor_changed=False):
        """Check if we need to turn heating on or off."""
        async with self._temp_lock:

            # time is passed by to the callback the async_track_time_interval function , and is set to "now"
            keepalive = time is not None  # boolean

            if self._emergency_stop:
                if keepalive:
                    _LOGGER.debug("Keepalive in emergency stop = resend emergency stop")
                    await self._activate_emergency_stop()
                else:
                    _LOGGER.debug("Cannot operate in emergency stop state")
                return

            if self._current_temperature is None:
                _LOGGER.debug("Current temp is None, cannot compare with target")
                return

            # when mode is on_off
            # on_off is also true when pwm = 0 therefore != _is_pwm_active
            if self._is_on_off_active and not self._is_pwm_active:
                # If the mode is OFF and the device is ON, turn it OFF and exit, else, just exit

                if self._hvac_mode == HVAC_MODE_HEAT:
                    min_cycle_duration = self._heat_on_off[CONF_MIN_CYCLE_DURATION]
                elif self._hvac_mode == HVAC_MODE_COOL:
                    min_cycle_duration = self._cool_on_off[CONF_MIN_CYCLE_DURATION]

                if self._hvac_mode == HVAC_MODE_HEAT:
                    tolerance_on = self._heat_on_off[CONF_HYSTERESIS_TOLERANCE_ON]
                    tolerance_off = self._heat_on_off[CONF_HYSTERESIS_TOLERANCE_OFF]
                elif self._hvac_mode == HVAC_MODE_COOL:
                    tolerance_on = self._cool_on_off[CONF_HYSTERESIS_TOLERANCE_ON]
                    tolerance_off = self._cool_on_off[CONF_HYSTERESIS_TOLERANCE_OFF]

                # if the call was made by a sensor change, check the min duration
                # in case of keep-alive (time not none) this test is ignored due to sensor_change = false
                if sensor_changed and min_cycle_duration is not None:
                    if self._hvac_mode == HVAC_MODE_HEAT:
                        entity_id = self._heater_entity_id
                        current_state = (
                            STATE_ON
                            if self._is_heat_enabled and self._is_heater_active
                            else STATE_OFF
                        )
                    elif self._hvac_mode == HVAC_MODE_COOL:
                        entity_id = self._ac_entity_id
                        current_state = (
                            STATE_ON
                            if self._is_cool_enabled and self._is_ac_active
                            else STATE_OFF
                        )

                    long_enough = condition.state(
                        self.hass, entity_id, current_state, min_cycle_duration
                    )

                    if not long_enough:
                        _LOGGER.debug(
                            "Operate - Min duration not expired, exiting (%s, %s, %s)",
                            min_cycle_duration,
                            current_state,
                            entity_id,
                        )
                        return

                target_temp_min = self.target_temperature  # lower limit
                target_temp_max = self.target_temperature  # upper limit
                if self._hvac_mode == HVAC_MODE_HEAT:
                    target_temp_min = target_temp_min - tolerance_on
                    target_temp_max = target_temp_max + tolerance_off
                else:
                    target_temp_min = target_temp_min - tolerance_off
                    target_temp_max = target_temp_max + tolerance_on

                current_temp = self._current_temperature

                _LOGGER.debug(
                    "Operate - tg_min %s, tg_max %s, current %s, tg %s, ka %s",
                    target_temp_min,
                    target_temp_max,
                    current_temp,
                    self.target_temperature,
                    keepalive,
                )

                # If keep-alive case, we force the order resend (this is the goal of keep alive)
                force_resend = keepalive

                if self._hvac_mode == HVAC_MODE_HEAT:
                    if current_temp > target_temp_max:
                        await self._async_heater_turn_off(force=force_resend)
                    elif current_temp <= target_temp_min:
                        await self._async_heater_turn_on(force=force_resend)
                elif self._hvac_mode == HVAC_MODE_COOL:
                    if current_temp >= target_temp_max:
                        await self._async_ac_turn_on(force=force_resend)
                    elif current_temp < target_temp_min:
                        await self._async_ac_turn_off(force=force_resend)

            # when mode is pwm
            elif self._is_pwm_active:
                """calculate control output and handle autotune"""

                if self._hvac_mode == HVAC_MODE_HEAT:
                    autotune = self._heat_pwm[CONF_AUTOTUNE]

                elif self._hvac_mode == HVAC_MODE_COOL:
                    autotune = self._cool_pwm[CONF_AUTOTUNE]

                if autotune != "none":
                    if self._hvac_mode == HVAC_MODE_HEAT:
                        pidAutotune = self.heat_pidAutotune
                        autotune_control_type = self._heat_pwm[
                            CONF_AUTOTUNE_CONTROL_TYPE
                        ]
                        minOut = 0
                        maxOut = self._heat_pwm[CONF_DIFFERENCE]
                        cycle_time = self._heat_pwm[CONF_PID_REFRESH_INTERVAL]
                    elif self._hvac_mode == HVAC_MODE_COOL:
                        pidAutotune = self.cool_pidAutotune
                        autotune_control_type = self._cool_pwm[
                            CONF_AUTOTUNE_CONTROL_TYPE
                        ]
                        minOut = -self._cool_pwm[CONF_DIFFERENCE]
                        maxOut = 0
                        cycle_time = self._cool_pwm[CONF_PID_REFRESH_INTERVAL]

                    if pidAutotune.run(self._current_temperature):
                        if autotune_control_type == "none":
                            params = pidAutotune.get_pid_parameters(autotune, True)
                        else:
                            params = pidAutotune.get_pid_parameters(
                                autotune, False, autotune_control_type
                            )

                        kp = params.Kp
                        ki = params.Ki
                        kd = params.Kd
                        self.async_set_pid(kp, ki, kd)

                        _LOGGER.warning(
                            "Set Kp, Ki, Kd. "
                            "Smart thermostat now runs on autotune PID Controller: %s,  %s,  %s",
                            kp,
                            ki,
                            kd,
                        )
                        if self._hvac_mode == HVAC_MODE_HEAT:
                            self.heat_pidController = pid_controller.PIDController(
                                cycle_time.seconds,
                                kp,
                                ki,
                                kd,
                                minOut,
                                maxOut,
                                time.time,
                            )
                            self._heat_pwm[CONF_AUTOTUNE] = "none"
                        elif self._hvac_mode == HVAC_MODE_COOL:
                            self.cool_pidController = pid_controller.PIDController(
                                cycle_time.seconds,
                                kp,
                                ki,
                                kd,
                                minOut,
                                maxOut,
                                time.time,
                            )
                            self._cool_pwm[CONF_AUTOTUNE] = "none"
                    if self._hvac_mode == HVAC_MODE_HEAT:
                        self.control_output = pidAutotune.output
                    elif self._hvac_mode == HVAC_MODE_COOL:
                        self.control_output = -pidAutotune.output
                else:
                    if self._hvac_mode == HVAC_MODE_HEAT:
                        self.control_output = self.heat_pidController.calc(
                            self._current_temperature, self.target_temperature
                        )
                    elif self._hvac_mode == HVAC_MODE_COOL:
                        self.control_output = -self.cool_pidController.calc(
                            self._current_temperature, self.target_temperature
                        )
                _LOGGER.info("Obtained current control output: %s", self.control_output)
                await self.set_controlvalue()

    async def set_controlvalue(self):
        """Set Outputvalue for heater"""
        if self._hvac_mode == HVAC_MODE_HEAT:
            difference = self._heat_pwm[CONF_DIFFERENCE]
            pwm = self._heat_pwm[CONF_PWM]
            maxOut = self._heat_pwm[CONF_DIFFERENCE]
            heat_meter_entity_id = self._heat_pwm[CONF_HEAT_METER]
        elif self._hvac_mode == HVAC_MODE_COOL:
            difference = self._cool_pwm[CONF_DIFFERENCE]
            pwm = self._cool_pwm[CONF_PWM]
            maxOut = self._cool_pwm[CONF_DIFFERENCE]
            heat_meter_entity_id = self._cool_pwm[CONF_HEAT_METER]

        # time is passed by to the callback the async_track_time_interval function , and is set to "now"
        force_resend = time is not None

        if pwm:
            if self.control_output == difference:
                if self._hvac_mode == HVAC_MODE_HEAT and not self._is_heater_active:
                    await self._async_heater_turn_on(force=force_resend)
                elif self._hvac_mode == HVAC_MODE_COOL and not self._is_ac_active:
                    await self._async_ac_turn_on(force=force_resend)
                self.time_changed = time.time()
            elif self.control_output > 0:
                await self.pwm_switch(
                    pwm.seconds * self.control_output / maxOut,
                    pwm.seconds * (maxOut - self.control_output) / maxOut,
                    time.time() - self.time_changed,
                )
            else:
                if self._hvac_mode == HVAC_MODE_HEAT and self._is_heater_active:
                    await self._async_heater_turn_off(force=force_resend)
                elif self._hvac_mode == HVAC_MODE_COOL and self._is_ac_active:
                    await self._async_ac_turn_off(force=force_resend)
                    self.time_changed = time.time()
            if heat_meter_entity_id:
                meter_attributes = {
                    "friendly_name": "Valve position",
                    "icon": "mdi:radiator",
                    "unit_of_measurement": "%",
                }
                self.hass.states.async_set(
                    heat_meter_entity_id,
                    round(self.control_output, 1),
                    meter_attributes,
                )
        else:

            # self.hass.states.async_set(self.heater_entity_id, self.control_output)
            if self._hvac_mode == HVAC_MODE_HEAT and not self._is_heater_active:
                await self._async_heater_turn_on(force=force_resend)
            elif self._hvac_mode == HVAC_MODE_COOL and not self._is_ac_active:
                await self._async_ac_turn_on(force=force_resend)

    async def pwm_switch(self, time_on, time_off, time_passed):
        """turn off and on the heater proportionally to controlvalue."""

        if self._hvac_mode == HVAC_MODE_HEAT:
            entity_id = self._heater_entity_id
        elif self._hvac_mode == HVAC_MODE_COOL:
            entity_id = self._ac_entity_id

        if self._is_heater_active or self._is_ac_active:
            if time_on < time_passed:
                _LOGGER.info(
                    "Time exceeds 'on-time' by %s sec: turn off: %s",
                    entity_id,
                    round(time_on - time_passed, 0),
                )
                if self._hvac_mode == HVAC_MODE_HEAT:
                    await self._async_heater_turn_off()
                elif self._hvac_mode == HVAC_MODE_COOL:
                    await self._async_ac_turn_off()
                self.time_changed = time.time()
            else:
                _LOGGER.info(
                    "Time until %s turns off: %s sec", entity_id, time_on - time_passed
                )
        else:
            if time_off < time_passed:
                _LOGGER.info(
                    "Time finshed 'off-time' by %s sec: turn on: %s",
                    entity_id,
                    round(time_passed - time_off, 0),
                )
                if self._hvac_mode == HVAC_MODE_HEAT:
                    await self._async_heater_turn_on()
                elif self._hvac_mode == HVAC_MODE_COOL:
                    await self._async_ac_turn_on()
                self.time_changed = time.time()
            else:
                _LOGGER.info(
                    "Time until %s turns on: %s sec", entity_id, time_off - time_passed
                )

    async def _async_heater_turn_on(self, force=False):
        """Turn heater toggleable device on."""
        _LOGGER.debug("Turn Heater ON")
        if self._is_on_off_active:
            if self._is_heater_active and not force:
                _LOGGER.debug("Heater already ON")
                return
            data = {ATTR_ENTITY_ID: self._heater_entity_id}
            _LOGGER.debug("Order ON sent to heater device %s", self._heater_entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_ON, data, context=self._context
            )
        else:
            """valve mode"""
            _LOGGER.info(
                "Change state of heater %s to %s",
                self._heater_entity_id,
                self.control_output,
            )
            data = {
                ATTR_ENTITY_ID: self._heater_entity_id,
                ATTR_VALUE: self.control_output,
            }
            await self.hass.services.async_call(
                PLATFORM_INPUT_NUMBER, SERVICE_SET_VALUE, data
            )

    async def _async_heater_turn_off(self, force=False):
        """Turn heater toggleable device off."""
        _LOGGER.debug("Turn Heater OFF called")
        if self._is_on_off_active:
            if not self._is_heater_active and not force:
                _LOGGER.debug("Heater already OFF")
                return
            data = {ATTR_ENTITY_ID: self._heater_entity_id}
            _LOGGER.debug("Order OFF sent to heater device %s", self._heater_entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_OFF, data, context=self._context
            )
        else:
            """valve mode"""
            _LOGGER.info(
                "Change state of heater %s to %s",
                self._heater_entity_id,
                self.control_output,
            )
            data = {ATTR_ENTITY_ID: self._heater_entity_id, ATTR_VALUE: 0}
            await self.hass.services.async_call(
                PLATFORM_INPUT_NUMBER, SERVICE_SET_VALUE, data
            )

    async def _async_ac_turn_on(self, force=False):
        """Turn ac toggleable device on."""
        _LOGGER.debug("Turn AC ON")
        if self._is_on_off_active:
            if self._is_ac_active and not force:
                _LOGGER.debug("AC already ON")
                return
            data = {ATTR_ENTITY_ID: self._ac_entity_id}
            _LOGGER.debug("Order ON sent to AC device %s", self._ac_entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_ON, data, context=self._context
            )
        else:
            """valve mode"""
            _LOGGER.info(
                "Change state of AC %s to %s", self._ac_entity_id, self.control_output
            )
            data = {ATTR_ENTITY_ID: self._ac_entity_id, ATTR_VALUE: self.control_output}
            await self.hass.services.async_call(
                PLATFORM_INPUT_NUMBER, SERVICE_SET_VALUE, data
            )

    async def _async_ac_turn_off(self, force=False):
        """Turn ac toggleable device off."""
        _LOGGER.debug("Turn AC OFF")
        if self._is_on_off_active:
            if not self._is_ac_active and not force:
                _LOGGER.debug("AC already OFF")
                return
            data = {ATTR_ENTITY_ID: self._ac_entity_id}
            _LOGGER.debug("Order OFF sent to AC device %s", self._ac_entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_OFF, data, context=self._context
            )
        else:
            """valve mode"""
            _LOGGER.info(
                "Change state of AC %s to %s", self._ac_entity_id, self.control_output
            )
            data = {ATTR_ENTITY_ID: self._ac_entity_id, ATTR_VALUE: 0}
            await self.hass.services.async_call(
                PLATFORM_INPUT_NUMBER, SERVICE_SET_VALUE, data
            )

    async def _activate_emergency_stop(self):
        """Send an emergency OFF order to HVAC devices."""
        _LOGGER.warning("Emergency OFF order send to devices")
        self._emergency_stop = True
        if self._hvac_mode == HVAC_MODE_HEAT:
            await self._async_heater_turn_off(True)
        elif self._hvac_mode == HVAC_MODE_COOL:
            await self._async_ac_turn_off(True)

    async def async_set_preset_mode(self, preset_mode: str):
        """Set new preset mode.

        This method must be run in the event loop and returns a coroutine.
        """
        if preset_mode not in self.preset_modes and preset_mode != PRESET_NONE:
            _LOGGER.error(
                "This preset (%s) is not enabled (see the configuration)", preset_mode
            )
            return

        self._preset_mode = preset_mode
        _LOGGER.debug("Set preset mode to %s", preset_mode)
        await self._async_operate()
        self.async_write_ha_state()

    @property
    def _is_heater_active(self):
        """If the toggleable heater device is currently active."""
        if self._is_heat_enabled:
            if self._is_on_off_heat_enabled or (
                self._is_pwm_heat_enabled and self._heat_pwm[CONF_PWM] != 0
            ):
                return self.hass.states.is_state(self._heater_entity_id, STATE_ON)
            else:
                sensor_state = self.hass.states.get(self._heater_entity_id)
                if sensor_state and sensor_state.state > 0:
                    return True
        else:
            return False

    @property
    def _is_ac_active(self):
        """If the toggleable AC device is currently active."""
        if self._is_cool_enabled:
            if self._is_on_off_cool_enabled or (
                self._is_pwm_cool_enabled and self._cool_pwm[CONF_PWM] != 0
            ):
                return self.hass.states.is_state(self._ac_entity_id, STATE_ON)
            else:
                sensor_state = self.hass.states.get(self._ac_entity_id)
                if sensor_state and sensor_state.state > 0:
                    return True
        else:
            return False

    @property
    def _is_on_off_active(self):
        if self._hvac_mode == HVAC_MODE_HEAT:
            if self._is_on_off_heat_enabled or (
                self._is_pwm_active and self._heat_pwm[CONF_PWM] != 0
            ):
                return True
        elif self._hvac_mode == HVAC_MODE_COOL:
            if self._is_on_off_cool_enabled or (
                self._is_pwm_active and self._cool_pwm[CONF_PWM] != 0
            ):
                return True
        else:
            return False

    @property
    def _is_pwm_active(self):
        # when mode is pwm
        if (self._hvac_mode == HVAC_MODE_HEAT and self._is_pwm_heat_enabled) or (
            self._hvac_mode == HVAC_MODE_COOL and self._is_pwm_cool_enabled
        ):
            return True
        else:
            return False

    @property
    def _is_cool_enabled(self):
        """Is the cool mode enabled."""
        if HVAC_MODE_COOL in self._enabled_hvac_mode:
            return True
        return False

    @property
    def _is_heat_enabled(self):
        """Is the heat mode enabled."""
        if HVAC_MODE_HEAT in self._enabled_hvac_mode:
            return True
        return False

    @property
    def _is_pwm_cool_enabled(self):
        """Is the pwm cool mode enabled."""
        if HVAC_MODE_COOL in self._enabled_pwm_mode:
            return True
        return False

    @property
    def _is_pwm_heat_enabled(self):
        """Is the pwm heat mode enabled."""
        if HVAC_MODE_HEAT in self._enabled_pwm_mode:
            return True
        return False

    @property
    def _is_on_off_cool_enabled(self):
        """Is the on_off cool mode enabled."""
        if HVAC_MODE_COOL in self._enabled_on_off_mode:
            return True
        return False

    @property
    def _is_on_off_heat_enabled(self):
        """Is the on_off heat mode enabled."""
        if HVAC_MODE_HEAT in self._enabled_on_off_mode:
            return True
        return False

    @property
    def supported_features(self):
        """Return the list of supported features."""
        if self.preset_modes == [PRESET_NONE]:
            return SUPPORT_TARGET_TEMPERATURE
        return SUPPORT_PRESET_MODE | SUPPORT_TARGET_TEMPERATURE

    @property
    def precision(self):
        """Return the precision of the system."""
        if self._temp_precision is not None:
            return self._temp_precision
        return super().precision

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        # Since this integration does not yet have a step size parameter
        # we have to re-use the precision as the step size for now.
        return self.precision

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        if self._hvac_mode == HVAC_MODE_HEAT:
            if self.preset_mode == PRESET_AWAY:
                return self._heat_conf[CONF_AWAY_TEMP]
            return self._heat_conf[CONF_HVAC_MODE_MIN_TEMP]
        if self._hvac_mode == HVAC_MODE_COOL:
            if self.preset_mode == PRESET_AWAY:
                return self._cool_conf[CONF_AWAY_TEMP]
            return self._cool_conf[CONF_HVAC_MODE_MIN_TEMP]

        # Get default temp from super class
        return super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        if self._hvac_mode == HVAC_MODE_HEAT:
            if self.preset_mode == PRESET_AWAY:
                return self._heat_conf[CONF_AWAY_TEMP]
            return self._heat_conf[CONF_HVAC_MODE_MAX_TEMP]
        if self._hvac_mode == HVAC_MODE_COOL:
            if self.preset_mode == PRESET_AWAY:
                return self._cool_conf[CONF_AWAY_TEMP]
            return self._cool_conf[CONF_HVAC_MODE_MAX_TEMP]

        # Get default temp from super class
        return super().max_temp

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._name

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._current_temperature

    @property
    def hvac_mode(self):
        """Return current operation."""
        return self._hvac_mode

    @property
    def hvac_action(self):
        """Return the current running hvac operation if supported.

        Need to be one of CURRENT_HVAC_*.
        """
        if self._hvac_mode == HVAC_MODE_OFF:
            return CURRENT_HVAC_OFF
        if (
            self._is_cool_enabled
            and self._hvac_mode == HVAC_MODE_COOL
            and self._is_ac_active
        ):
            return CURRENT_HVAC_COOL
        if (
            self._is_heat_enabled
            and self._hvac_mode == HVAC_MODE_HEAT
            and self._is_heater_active
        ):
            return CURRENT_HVAC_HEAT

        return CURRENT_HVAC_IDLE

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if self._hvac_mode == HVAC_MODE_OFF:
            return None

        if self._preset_mode == PRESET_AWAY:
            return (
                self._cool_conf[CONF_AWAY_TEMP]
                if self._hvac_mode == HVAC_MODE_COOL
                else self._heat_conf[CONF_AWAY_TEMP]
            )

        return (
            self._target_temp_cool
            if self._hvac_mode == HVAC_MODE_COOL
            else self._target_temp_heat
        )

    @property
    def hvac_modes(self):
        """List of available operation modes."""
        return self._enabled_hvac_mode + [HVAC_MODE_OFF]

    @property
    def preset_mode(self):
        """Return the current preset mode, e.g., home, away, temp."""
        return self._preset_mode

    @property
    def preset_modes(self):
        """Return a list of available preset modes."""
        modes = [PRESET_NONE]

        if HVAC_MODE_HEAT in self.hvac_modes and CONF_AWAY_TEMP in self._heat_conf:
            modes = modes + [PRESET_AWAY]
        elif HVAC_MODE_COOL in self.hvac_modes and CONF_AWAY_TEMP in self._cool_conf:
            modes = modes + [PRESET_AWAY]

        return modes

    @property
    def pid_parm(self):
        """Return the pid parameters of the thermostat."""
        if self._hvac_mode == HVAC_MODE_HEAT:
            kp = self._heat_pwm[CONF_KP]
            ki = self._heat_pwm[CONF_KI]
            kd = self._heat_pwm[CONF_KD]
        elif self._hvac_mode == HVAC_MODE_COOL:
            kp = self._cool_pwm[CONF_KP]
            ki = self._cool_pwm[CONF_KI]
            kd = self._cool_pwm[CONF_KD]

        return (kp, ki, kd)

    @property
    def pid_control_output(self):
        """Return the pid control output of the thermostat."""
        return self.control_output

    async def async_set_pid(self, kp, ki, kd):
        """Set PID parameters."""
        if self._hvac_mode == HVAC_MODE_HEAT:
            self._heat_pwm[CONF_KP] = kp
            self._heat_pwm[CONF_KI] = ki
            self._heat_pwm[CONF_KD] = kd
        elif self._hvac_mode == HVAC_MODE_COOL:
            self._cool_pwm[CONF_KP] = kp
            self._cool_pwm[CONF_KI] = ki
            self._cool_pwm[CONF_KD] = kd
        # self._async_control_heating()
        # yield from self.async_update_ha_state()
        await self.async_write_ha_state()