"""Scheduling and window/door safety logic for Chauffage Intelligent."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)

from .const import CONF_AREA, CONF_CLIMATE, CONF_DOOR_SENSOR, slugify_area


class ChauffageScheduler:
    """Applies planning presets and window/door safety cutoff for one room."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""

        self.hass = hass
        self.entry = entry
        self.area_slug = slugify_area(entry.data[CONF_AREA])
        self.climate_entity = entry.data.get(CONF_CLIMATE)
        self.door_entity = entry.data.get(CONF_DOOR_SENSOR)
        self.window_switch_entity = f"switch.fenetre_ouverte_{self.area_slug}"

        self._remove_listeners: list = []

    def start(self) -> None:
        """Start listening for time changes and window/door state changes."""

        self._remove_listeners.append(
            async_track_time_change(self.hass, self._handle_minute_tick, second=0)
        )

        window_entity = self.door_entity or self.window_switch_entity

        self._remove_listeners.append(
            async_track_state_change_event(
                self.hass, [window_entity], self._handle_window_change
            )
        )

        self.hass.async_create_task(self._apply_current_slot())

    def stop(self) -> None:
        """Stop all listeners."""

        for remove in self._remove_listeners:
            remove()

        self._remove_listeners.clear()

    async def _handle_minute_tick(self, now) -> None:
        """Apply the preset 1 minute before the anticipated heating start."""

        if not self.climate_entity:
            return

        anticipee_entity = f"sensor.heure_anticipee_{self.area_slug}"
        anticipee_state = self.hass.states.get(anticipee_entity)

        if anticipee_state is None or anticipee_state.state in ("unknown", "unavailable"):
            return

        try:
            hh, mm = map(int, anticipee_state.state.split(":"))
        except ValueError:
            return

        cible = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        application = cible - timedelta(minutes=1)

        if now.strftime("%H:%M") != application.strftime("%H:%M"):
            return

        await self._apply_current_slot()

    async def _apply_current_slot(self) -> None:
        """Read the current planning slot and apply its preset."""

        if not self.climate_entity:
            return

        planning_entity = f"sensor.heure_planning_{self.area_slug}"
        planning_state = self.hass.states.get(planning_entity)

        if planning_state is None or "|" not in planning_state.state:
            return

        preset = planning_state.state.split("|")[1].strip()

        if not preset:
            return

        await self.hass.services.async_call(
            "climate",
            "set_preset_mode",
            {"entity_id": self.climate_entity, "preset_mode": preset},
        )

    async def _handle_window_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Cut or restore heating when the window/door state changes."""

        if not self.climate_entity:
            return

        new_state = event.data.get("new_state")

        if new_state is None:
            return

        is_open = new_state.state == "on"

        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": self.climate_entity, "hvac_mode": "off" if is_open else "heat"},
        )
