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

from . import hvac_setting

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
DEFAULT_AUTOTUNE_CONTROL_TYPE = "none"
DEFAULT_STEP_SIZE = "10"
DEFAULT_NOISEBAND = 0.5
DEFAULT_HEAT_METER = "none"


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
# CONF_HVAC_SETTINGS = "_hvac_def"

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


PRESET_AUTOTUNE = "PID_autotune"

# valve control (pid/pwm)
SERVICE_SET_VALUE = "set_value"
ATTR_VALUE = "value"
PLATFORM_INPUT_NUMBER = "input_number"

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE

SUPPORTED_HVAC_MODES = [HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_OFF]
SUPPORTED_PRESET_MODES = [PRESET_NONE, PRESET_AWAY, PRESET_AUTOTUNE]


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
                            vol.Optional(CONF_AUTOTUNE_LOOKBACK): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(
                                CONF_AUTOTUNE_STEP_SIZE, default=DEFAULT_STEP_SIZE
                            ): vol.Coerce(float),
                            vol.Optional(
                                CONF_NOISEBAND, default=DEFAULT_NOISEBAND
                            ): vol.Coerce(float),
                            vol.Optional(CONF_HEAT_METER): cv.entity_id,
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
                                CONF_AUTOTUNE_STEP_SIZE, default=DEFAULT_STEP_SIZE
                            ): vol.Coerce(float),
                            vol.Optional(CONF_AUTOTUNE_LOOKBACK): vol.All(
                                cv.time_period, cv.positive_timedelta
                            ),
                            vol.Optional(
                                CONF_NOISEBAND, default=DEFAULT_NOISEBAND
                            ): vol.Coerce(float),
                            vol.Optional(CONF_HEAT_METER): cv.entity_id,
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

    hvac_def = {}
    enabled_hvac_modes = []

    # Append the enabled hvac modes to the list
    if heat_conf:
        enabled_hvac_modes.append(HVAC_MODE_HEAT)
        hvac_def["heat"] = hvac_setting.HVAC_Setting(HVAC_MODE_HEAT, heat_conf)
    if cool_conf:
        enabled_hvac_modes.append(HVAC_MODE_COOL)
        hvac_def["cool"] = hvac_setting.HVAC_Setting(HVAC_MODE_COOL, cool_conf)

    async_add_entities(
        [
            GenericThermostat(
                name,
                unit,
                precision,
                sensor_entity_id,
                hvac_def,
                enabled_hvac_modes,
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
        hvac_def,
        enabled_hvac_modes,
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
        self._hvac_def = hvac_def

        self._hvac_mode = initial_hvac_mode
        self._preset_mode = initial_preset_mode
        self._enabled_hvac_mode = enabled_hvac_modes
        self._enable_old_state = enable_old_state
        self._sensor_stale_duration = sensor_stale_duration

        self._emergency_stop = False
        self._current_temperature = None
        self._current_mode = "off"
        self._old_mode = "off"
        self._hvac_on = None

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

        if self._sensor_stale_duration:
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_check_sensor_not_responding,
                    self._sensor_stale_duration,
                )
            )

        entity_list = []
        for _, mode_def in self._hvac_def.items():
            entity_list.append(mode_def.get_hvac_switch)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                entity_list,
                self._async_switch_device_changed,
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
                # self._hvac_mode = old_hvac_mode
                await self.async_set_hvac_mode(old_hvac_mode)

                if "hvac_def" in old_state.attributes:
                    old_def = old_state.attributes["hvac_def"]

                    for key, data in old_def.items():
                        if key in list(self._hvac_def.keys()):
                            self._hvac_def[key].set_target_temperature(data["target"])

                # Restore the target temperature
                if self._hvac_on:
                    min_temp, max_temp = self._hvac_on.get_target_temp_limits
                    if (
                        old_temperature is not None
                        and min_temp <= old_temperature <= max_temp
                    ):
                        self._target_temp = old_temperature

        # Set default state to off
        if not self._hvac_mode:
            self._hvac_mode = HVAC_MODE_OFF
        # await self._async_operate()

        # Ensure we update the current operation after changing the mode
        self.async_write_ha_state()

    @property
    def device_state_attributes(self):
        tmp_dict = {}
        for key, data in self._hvac_def.items():
            tmp_dict[key] = data.get_variable_attr
        return {"hvac_def": tmp_dict}

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

        # stop autotune
        if (
            self._preset_mode == PRESET_AUTOTUNE
            and self._hvac_on.is_pwm_autotune_active
        ):
            self._hvac_on.start_pid()
        # restore preset mode
        self._preset_mode = PRESET_NONE

        self._old_mode = self._current_mode
        if self._hvac_mode == HVAC_MODE_OFF:
            self._current_mode = "off"
        elif self._hvac_mode == HVAC_MODE_HEAT:
            self._current_mode = "heat"
        elif self._hvac_mode == HVAC_MODE_COOL:
            self._current_mode = "cool"

        if self._hvac_mode == HVAC_MODE_OFF:
            self._hvac_on = None
        else:
            self._hvac_on = self._hvac_def[self._current_mode]
            self._target_temp = self._hvac_on.get_target_temp

        # new state thus all switches off
        for key, _ in self._hvac_def.items():
            await self._async_switch_turn_off(hvac_def=key)
        if self._hvac_mode == HVAC_MODE_OFF:
            _LOGGER.debug("HVAC mode is OFF. Turn the devices OFF and exit")
            self.async_write_ha_state()
            return

        # update listener
        self._update_keep_alive()
        if self._hvac_on.is_hvac_pwm_mode:
            self._hvac_on.pid_reset_time
            self.time_changed = time.time()
        await self._async_operate()

        # Ensure we update the current operation after changing the mode
        self.async_write_ha_state()

    def _update_keep_alive(self):
        # remove_listener(self._async_operate)
        if self._hvac_mode != HVAC_MODE_OFF:
            _LOGGER.debug("update 'keep alive' for %s", self._hvac_mode)
            keep_alive = None
            keep_alive = self._hvac_on.get_keep_alive

            if keep_alive:
                self.async_on_remove(
                    async_track_time_interval(
                        self.hass, self._async_operate, keep_alive
                    )
                )

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
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

        if (
            self.preset_mode == PRESET_AWAY
        ):  # when preset mode is away, change the temperature but do not operate
            _LOGGER.debug(
                "Preset mode away when temperature is updated : skipping operate"
            )
            return

        self._hvac_on.set_target_temperature(temperature)
        self._target_temp = temperature

        if not self._hvac_mode == HVAC_MODE_OFF:
            await self._async_operate(force=True)

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

        if not self._hvac_mode == HVAC_MODE_OFF:
            if not self._hvac_on.is_hvac_on_off_mode:
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
            "Switch of %s changed from %s to %s",
            entity_id,
            old_state.state,
            new_state.state,
        )
        if not self._hvac_on and new_state.state == "on":
            # thermostat off thus all switches off

            _LOGGER.warning(
                "No swithces should be on in 'off'mode: switch of %s changed from %s to %s",
                entity_id,
                old_state.state,
                new_state.state,
            )

        if self._hvac_on:
            if entity_id != self._hvac_on.get_hvac_switch:
                _LOGGER.warning(
                    "Wrong switch of %s changed from %s to %s",
                    entity_id,
                    old_state.state,
                    new_state.state,
                )

            heat_meter_entity_id = self._hvac_on.get_heat_meter
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

    async def _async_operate(self, time=None, sensor_changed=False, force=False):
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
            if not self._hvac_on:
                return
            # when mode is on_off
            # on_off is also true when pwm = 0 therefore != _is_pwm_active
            if self._hvac_on.is_hvac_on_off_mode:
                # If the mode is OFF and the device is ON, turn it OFF and exit, else, just exit
                min_cycle_duration = self._hvac_on.get_min_cycle
                tolerance_on, tolerance_off = self._hvac_on.get_hysteris

                # if the call was made by a sensor change, check the min duration
                # in case of keep-alive (time not none) this test is ignored due to sensor_change = false
                if sensor_changed and min_cycle_duration is not None:

                    entity_id = self._hvac_on.get_hvac_switch
                    current_state = STATE_ON if self._is_switch_active() else STATE_OFF

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

                target_temp_min = self._target_temp - tolerance_on
                target_temp_max = self._target_temp + tolerance_off
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

                if current_temp > target_temp_max:
                    await self._async_switch_turn_off(force=force_resend)
                elif current_temp <= target_temp_min:
                    await self._async_switch_turn_on(force=force_resend)

            # when mode is pwm
            elif self._hvac_on.is_hvac_pwm_mode:
                """calculate control output and handle autotune"""

                self._hvac_on.run_pid(
                    self._current_temperature, self.target_temperature, force
                )
                # restore preset mode when autotune is off
                if (
                    self._preset_mode == PRESET_AUTOTUNE
                    and not self._hvac_on.is_pwm_autotune_active
                ):
                    self._preset_mode = PRESET_NONE
                self.control_output = self._hvac_on.get_pid_control_output
                _LOGGER.info("Obtained current control output: %s", self.control_output)
                await self.set_controlvalue()

    async def set_controlvalue(self):
        """Set Outputvalue for heater"""
        force_resend = True
        pwm = self._hvac_on.get_pwm_mode
        difference = self._hvac_on.get_difference
        heat_meter_entity_id = self._hvac_on.get_heat_meter
        _, maxOut = self._hvac_on.get_pid_limits

        if pwm:
            if self.control_output == difference:
                if not self._is_switch_active():
                    await self._async_switch_turn_on(force=force_resend)

                self.time_changed = time.time()
            elif self.control_output > 0:
                await self.pwm_switch(
                    pwm.seconds * self.control_output / maxOut,
                    pwm.seconds * (maxOut - self.control_output) / maxOut,
                    time.time() - self.time_changed,
                )
            else:
                if self._is_switch_active:
                    await self._async_switch_turn_off(force=force_resend)
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
            await self._async_switch_turn_on(force=force_resend)

    async def pwm_switch(self, time_on, time_off, time_passed):
        """turn off and on the heater proportionally to controlvalue."""
        entity_id = self._hvac_on.get_hvac_switch

        if self._is_switch_active():
            if time_on < time_passed:
                _LOGGER.info(
                    "Time exceeds 'on-time' by %s sec: turn off: %s",
                    entity_id,
                    round(time_on - time_passed, 0),
                )

                await self._async_switch_turn_off()
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

                await self._async_switch_turn_on()
                self.time_changed = time.time()
            else:
                _LOGGER.info(
                    "Time until %s turns on: %s sec", entity_id, time_off - time_passed
                )

    async def _async_switch_turn_on(self, force=False):
        """Turn switch toggleable device on."""
        _LOGGER.debug("Turn ON")
        entity_id = self._hvac_on.get_hvac_switch
        if self._hvac_on.is_hvac_switch_on_off:
            if self._is_switch_active() and not force:
                _LOGGER.debug("Switch already ON")
                return
            data = {ATTR_ENTITY_ID: entity_id}
            _LOGGER.debug("Order ON sent to switch device %s", entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_ON, data, context=self._context
            )
        else:
            """valve mode"""
            _LOGGER.info(
                "Change state of heater %s to %s",
                entity_id,
                self.control_output,
            )
            data = {
                ATTR_ENTITY_ID: entity_id,
                ATTR_VALUE: self.control_output,
            }
            await self.hass.services.async_call(
                PLATFORM_INPUT_NUMBER, SERVICE_SET_VALUE, data
            )

    async def _async_switch_turn_off(self, hvac_def=None, force=False):
        """Turn toggleable device off."""
        _LOGGER.debug("Turn OFF called")
        if hvac_def:
            _hvac_def = self._hvac_def[hvac_def]
        else:
            _hvac_def = self._hvac_on
        entity_id = _hvac_def.get_hvac_switch

        if _hvac_def.is_hvac_switch_on_off:
            if not self._is_switch_active(hvac_def=hvac_def) and not force:
                _LOGGER.debug("Switch already OFF")
                return
            data = {ATTR_ENTITY_ID: entity_id}
            _LOGGER.debug("Order OFF sent to switch device %s", entity_id)
            await self.hass.services.async_call(
                HA_DOMAIN, SERVICE_TURN_OFF, data, context=self._context
            )
        else:
            """valve mode"""
            _LOGGER.info(
                "Change state of switch %s to %s",
                entity_id,
                0,
            )
            data = {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 0}
            await self.hass.services.async_call(
                PLATFORM_INPUT_NUMBER, SERVICE_SET_VALUE, data
            )

    async def _activate_emergency_stop(self):
        """Send an emergency OFF order to HVAC devices."""
        _LOGGER.warning("Emergency OFF order send to devices")
        self._emergency_stop = True
        await self._async_switch_turn_off(True)

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

        if self._preset_mode == PRESET_AWAY:
            self._target_temp = self._hvac_on.get_away_temp
        else:
            self._target_temp = self._hvac_on.get_target_temp

        if self._preset_mode == PRESET_AUTOTUNE:
            self._hvac_on.start_autotune(self._target_temp)
        elif self._hvac_on.is_hvac_pwm_mode and self._hvac_on.is_pwm_autotune_active:
            self._hvac_on.start_pid()

        await self._async_operate(force=True)
        self.async_write_ha_state()

    def _is_switch_active(self, hvac_def=None):
        """If the toggleable switch device is currently active."""
        if hvac_def:
            _hvac_def = self._hvac_def[hvac_def]
        else:
            _hvac_def = self._hvac_on
        entity_id = _hvac_def.get_hvac_switch

        if _hvac_def.is_hvac_switch_on_off:
            return self.hass.states.is_state(entity_id, STATE_ON)
        else:
            sensor_state = self.hass.states.get(entity_id)
            if sensor_state and sensor_state.state > 0:
                return True
            else:
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
        if not self._hvac_mode == HVAC_MODE_OFF:
            if self.preset_mode == PRESET_AWAY:
                return self._hvac_on.get_away_temp
            if self._hvac_on.min_target_temp:
                return self._hvac_on.min_target_temp

        # Get default temp from super class
        return super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        if not self._hvac_mode == HVAC_MODE_OFF:
            if self.preset_mode == PRESET_AWAY:
                return self._hvac_on.get_away_temp
            if self._hvac_on.max_target_temp:
                return self._hvac_on.max_target_temp

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
        if self._hvac_mode == HVAC_MODE_COOL and self._is_switch_active():
            return CURRENT_HVAC_COOL
        if self._hvac_mode == HVAC_MODE_HEAT and self._is_switch_active():
            return CURRENT_HVAC_HEAT

        return CURRENT_HVAC_IDLE

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        if self._hvac_mode == HVAC_MODE_OFF:
            return None
        return self._target_temp

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

        for _, mode_def in self._hvac_def.items():

            if mode_def.get_away_temp:
                modes = modes + [PRESET_AWAY]
                break

        for _, mode_def in self._hvac_def.items():

            if mode_def.is_pwm_autotune:
                modes = modes + [PRESET_AUTOTUNE]
                break

        return modes
