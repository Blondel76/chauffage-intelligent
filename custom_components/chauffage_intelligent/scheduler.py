"""Scheduling, window safety, and heater activation for Chauffage Intelligent.

Principe : à chaque réévaluation (chaque minute + à chaque événement utile),
le scheduler calcule le preset que la pièce DOIT avoir maintenant, le publie
dans l'entité `sensor.chauffage_actuel_<pièce>`, puis aligne le thermostat
dessus s'il diffère. Le planning a toujours la main.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.start import async_at_started

from .boiler import update_boiler_state
from .calculations import (
    calculate_heating_time,
    get_float,
    get_next_schedule,
    get_previous_schedule,
)
from .const import (
    COEFFICIENT_DEFAULT,
    CONF_AREA,
    CONF_CLIMATE,
    CONF_DOOR_SENSOR,
    CONF_GROUP_AREAS,
    CONF_GROUP_THRESHOLD,
    CONF_HEATER_ENTITY,
    CONF_HEATING_TYPE,
    CONF_TEMP_INT,
    DEFAULT_GROUP_THRESHOLD,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_GROUP,
    ENTRY_TYPE_ROOM,
    HEATING_TYPE_ELECTRIC,
    HEATING_TYPE_GAS,
    SECURITY_STATE_CRITICAL,
    SIGNAL_CURRENT_PRESET,
    VALVE_CLOSED_TEMP,
    VALVE_OPEN_TEMP,
    slugify_area,
)
from .security import compute_room_security_state

_LOGGER = logging.getLogger(__name__)


def _preset_of(slot: str | None) -> str | None:
    """Return the preset name of a 'HH:MM|preset' slot, or None if invalid."""

    if not slot or "|" not in slot:
        return None

    return slot.split("|", 1)[1].strip() or None


def _get_central_heating_type(hass: HomeAssistant) -> str:
    """Return the house's heating type, defaulting to gas."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry.data.get(CONF_HEATING_TYPE, HEATING_TYPE_GAS)

    return HEATING_TYPE_GAS


def _find_room_entry_by_area(hass: HomeAssistant, area_id: str) -> ConfigEntry | None:
    """Find a room's config entry by its raw area id."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM and entry.data.get(CONF_AREA) == area_id:
            return entry

    return None


def _get_linked_areas(hass: HomeAssistant, area_id: str) -> tuple[list[str], float]:
    """Return (linked area ids, smallest matching threshold) for a given area."""

    linked: set[str] = set()
    threshold: float | None = None

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) != ENTRY_TYPE_GROUP:
            continue

        areas = entry.data.get(CONF_GROUP_AREAS, [])

        if area_id not in areas:
            continue

        linked.update(a for a in areas if a != area_id)

        group_threshold = entry.data.get(CONF_GROUP_THRESHOLD, DEFAULT_GROUP_THRESHOLD)

        if threshold is None or group_threshold < threshold:
            threshold = group_threshold

    return list(linked), threshold if threshold is not None else DEFAULT_GROUP_THRESHOLD


def _any_linked_room_heating(hass: HomeAssistant, linked_area_ids: list[str]) -> bool:
    """Check if any linked room's climate is currently heating."""

    for area_id in linked_area_ids:
        room_entry = _find_room_entry_by_area(hass, area_id)

        if room_entry is None:
            continue

        state = hass.states.get(room_entry.data.get(CONF_CLIMATE))

        if state is not None and state.attributes.get("hvac_action") == "heating":
            return True

    return False


class ChauffageScheduler:
    """Applies planning presets, window safety, and heater activation for one room."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        mode_selector_entity: str | None,
    ) -> None:
        """Initialize."""

        self.hass = hass
        self.entry = entry
        self.mode_selector_entity = mode_selector_entity

        self.area_id = entry.data[CONF_AREA]
        self.area_slug = slugify_area(self.area_id)
        self.climate_entity = entry.data.get(CONF_CLIMATE)
        self.heater_entity = entry.data.get(CONF_HEATER_ENTITY)
        self.door_entity = entry.data.get(CONF_DOOR_SENSOR)
        self.temp_int_entity = entry.data.get(CONF_TEMP_INT)

        self.window_switch_entity = f"switch.fenetre_ouverte_{self.area_slug}"
        self.current_entity = f"sensor.chauffage_actuel_{self.area_slug}"

        self._remove_listeners: list = []
        self._delayed_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

        # Créneau (date/heure) pour lequel l'anticipation a démarré. Une fois
        # posée, elle est conservée jusqu'à l'heure du planning : le temps de
        # chauffe, qui dépend de la consigne du thermostat, ne peut plus la
        # remettre en cause (évite les va-et-vient de preset).
        self._anticipated_target: datetime | None = None

        # Dernier preset en échec, pour ne logger l'erreur qu'une fois.
        self._failed_preset: str | None = None

    # ------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------

    def start(self) -> None:
        """Start listening for time, window, climate and mode changes."""

        self._restore_anticipation()

        self._remove_listeners.append(
            async_track_time_change(self.hass, self._handle_minute_tick, second=0)
        )

        window_entity = self.door_entity or self.window_switch_entity

        self._remove_listeners.append(
            async_track_state_change_event(
                self.hass, [window_entity], self._handle_window_change
            )
        )

        if self.climate_entity:
            self._remove_listeners.append(
                async_track_state_change_event(
                    self.hass, [self.climate_entity], self._handle_climate_change
                )
            )

        if self.mode_selector_entity:
            self._remove_listeners.append(
                async_track_state_change_event(
                    self.hass, [self.mode_selector_entity], self._handle_mode_change
                )
            )

        # Au démarrage : réévaluer une fois HA entièrement démarré
        # (thermostats chargés). Si HA tourne déjà (rechargement), c'est
        # immédiat.
        self._remove_listeners.append(
            async_at_started(self.hass, self._handle_ha_started)
        )

        self.hass.async_create_task(self._update_heater())

    def stop(self) -> None:
        """Stop all listeners."""

        for remove in self._remove_listeners:
            if remove is not None:
                remove()

        self._remove_listeners.clear()

        if self._delayed_task is not None:
            self._delayed_task.cancel()
            self._delayed_task = None

    def _restore_anticipation(self) -> None:
        """Retrouve une anticipation en cours (mémorisée dans l'entité) après redémarrage."""

        state = self.hass.states.get(self.current_entity)

        if state is None:
            return

        raw = state.attributes.get("cible")

        if not raw:
            return

        try:
            target = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return

        if target > datetime.now():
            self._anticipated_target = target

    @callback
    def _handle_ha_started(self, hass: HomeAssistant) -> None:
        """HA est entièrement démarré : réévaluer après un court délai."""

        self._schedule_delayed_reconcile()

    def
