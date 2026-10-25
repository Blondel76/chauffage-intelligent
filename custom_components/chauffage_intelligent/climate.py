"""Thermostat intégré de Chauffage Intelligent (une entité climate par pièce).

Le thermostat lit la température intérieure choisie dans la config de la
pièce, applique la température du preset en cours (réglée par pièce) et
décide « chauffe » ou « repos » avec une hystérésis. Il ne pilote rien
lui-même : le scheduler ouvre/ferme la vanne ou l'interrupteur à partir de
son `hvac_action`, comme avec un thermostat externe.

Régulation (reprise du principe du thermostat générique de Home Assistant) :
- repos -> chauffe quand température <= consigne - tolérance basse ;
- chauffe -> repos quand température >= consigne + tolérance haute ;
- une durée minimale après un arrêt empêche un redémarrage trop rapide
  (protège la vanne et le relais). Elle ne retarde JAMAIS un arrêt : la
  chauffe s'arrête dès que la consigne est atteinte.

Le planning change le preset (set_preset_mode) : la consigne manuelle
éventuelle est alors abandonnée. Le mode, le preset et la consigne manuelle
sont restaurés au redémarrage de HA.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .calculations import _numeric_state, get_preset_temperature, planning_presets
from .const import (
    CONF_AREA,
    CONF_COLD_TOLERANCE,
    CONF_HOT_TOLERANCE,
    CONF_MIN_CYCLE,
    CONF_TEMP_INT,
    DEFAULT_COLD_TOLERANCE,
    DEFAULT_HOT_TOLERANCE,
    DEFAULT_MIN_CYCLE,
    DOMAIN,
    THERMOSTAT_MAX_TEMP,
    THERMOSTAT_MIN_TEMP,
    THERMOSTAT_REFRESH_SECONDS,
    climate_entity_id,
    slugify_area,
)

_LOGGER = logging.getLogger(__name__)


def _setting(config: dict, key: str, default: float) -> float:
    """Lit un réglage numérique positif ou nul, avec valeur par défaut."""

    try:
        value = float(config.get(key, default))
    except (ValueError, TypeError):
        return default

    return value if value >= 0 else default


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the room thermostat."""

    area_slug = slugify_area(entry.data[CONF_AREA])

    async_add_entities([RoomThermostat(entry, area_slug)])


class RoomThermostat(RestoreEntity, ClimateEntity):
    """Thermostat virtuel d'une pièce (presets par pièce, hystérésis)."""

    _attr_has_entity_name = True
    _attr_name = "Thermostat"
    _attr_should_poll = False
    _attr_icon = "mdi:thermostat"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = THERMOSTAT_MIN_TEMP
    _attr_max_temp = THERMOSTAT_MAX_TEMP
    _attr_target_temperature_step = 0.5
    _attr_precision = 0.1

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = f"{entry.entry_id}_thermostat"
        self.entity_id = climate_entity_id(area_slug)
        self._attr_suggested_object_id = f"thermostat_{area_slug}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

        self._hvac_mode: HVACMode = HVACMode.HEAT
        self._preset_mode: str | None = None
        self._manual_target: float | None = None
        self._heating = False
        self._last_change: datetime | None = None

    # ------------------------------------------------------------
    # Config
    # ------------------------------------------------------------

    def _config(self) -> dict:
        """Config courante de la pièce (relue à chaque calcul)."""

        return {**self._entry.data, **self._entry.options}

    # ------------------------------------------------------------
    # État exposé
    # ------------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        return HVACAction.HEATING if self._heating else HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        return _numeric_state(self.hass, self._config().get(CONF_TEMP_INT))

    @property
    def target_temperature(self) -> float:
        if self._manual_target is not None:
            return self._manual_target

        return get_preset_temperature(self._config(), self._preset_mode)

    @property
    def preset_mode(self) -> str | None:
        return self._preset_mode

    @property
    def preset_modes(self) -> list[str]:
        presets = planning_presets(self._config())

        if self._preset_mode and self._preset_mode not in presets:
            presets = [*presets, self._preset_mode]

        return presets

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "consigne_manuelle": self._manual_target,
            "temperature_preset": get_preset_temperature(
                self._config(), self._preset_mode
            ),
        }

    # ------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Restore the previous state and start regulating."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            if last_state.state in (HVACMode.HEAT, HVACMode.OFF):
                self._hvac_mode = HVACMode(last_state.state)

            preset = last_state.attributes.get("preset_mode")

            if isinstance(preset, str) and preset:
                self._preset_mode = preset

            manual = last_state.attributes.get("consigne_manuelle")

            if manual is not None:
                try:
                    self._manual_target = float(manual)
                except (ValueError, TypeError):
                    self._manual_target = None

        config = self._config()

        if self._preset_mode is None:
            presets = planning_presets(config)
            self._preset_mode = presets[0] if presets else None

        temp_entity = config.get(CONF_TEMP_INT)

        if temp_entity:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [temp_entity], self._handle_temp_change
                )
            )

        # Filet de sécurité : fin de la durée minimale entre deux cycles.
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._handle_periodic,
                timedelta(seconds=THERMOSTAT_REFRESH_SECONDS),
            )
        )

        # Modification des presets / tolérances via l'interface (options flow).
        self.async_on_remove(self._entry.add_update_listener(self._handle_entry_update))

        self._evaluate()

    # ------------------------------------------------------------
    # Régulation
    # ------------------------------------------------------------

    @callback
    def _evaluate(self) -> None:
        """Décide chauffe/repos (hystérésis + délai minimal avant un redémarrage)."""

        config = self._config()
        temp = self.current_temperature
        target = self.target_temperature

        if self._hvac_mode == HVACMode.OFF or temp is None or target is None:
            wanted = False
        else:
            cold = _setting(config, CONF_COLD_TOLERANCE, DEFAULT_COLD_TOLERANCE)
            hot = _setting(config, CONF_HOT_TOLERANCE, DEFAULT_HOT_TOLERANCE)

            if self._heating:
                wanted = temp < target + hot
            else:
                wanted = temp <= target - cold

        if wanted == self._heating:
            return

        now = dt_util.utcnow()

        # Seul un DÉMARRAGE est retardé (délai écoulé depuis le dernier
        # arrêt) ; un arrêt est toujours immédiat.
        if wanted and self._last_change is not None:
            min_cycle = timedelta(
                minutes=_setting(config, CONF_MIN_CYCLE, DEFAULT_MIN_CYCLE)
            )

            if now - self._last_change < min_cycle:
                return

        self._heating = wanted
        self._last_change = now

    @callback
    def _refresh(self) -> None:
        self._evaluate()
        self.async_write_ha_state()

    @callback
    def _handle_temp_change(self, event: Event) -> None:
        self._refresh()

    @callback
    def _handle_periodic(self, _now=None) -> None:
        self._refresh()

    async def _handle_entry_update(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        self._refresh()

    # ------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Allume (heat) ou éteint (off) le thermostat."""

        if hvac_mode not in (HVACMode.HEAT, HVACMode.OFF):
            return

        self._hvac_mode = HVACMode(hvac_mode)
        self._refresh()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Applique un preset : sa température devient la consigne."""

        self._preset_mode = preset_mode
        self._manual_target = None
        self._refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Consigne manuelle, valable jusqu'au prochain changement de preset."""

        hvac_mode = kwargs.get("hvac_mode")

        if hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)

        temperature = kwargs.get(ATTR_TEMPERATURE)

        if temperature is None:
            return

        self._manual_target = float(temperature)
        self._refresh()
