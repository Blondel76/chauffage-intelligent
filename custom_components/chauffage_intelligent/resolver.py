"""Planning resolution for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_MODE_PLANNINGS, DEFAULT_PLANNING


class PlanningResolver:
    """Resolves which planning string is active for a room, based on the central mode."""

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
        self._last_valid_mode: str | None = None

    def get_active_planning(self) -> str:
        """Return the currently active planning string for this room."""

        mode_plannings = self.entry.data.get(CONF_MODE_PLANNINGS, {})

        if self.mode_selector_entity:
            mode_state = self.hass.states.get(self.mode_selector_entity)

            if mode_state is not None:
                planning = mode_plannings.get(mode_state.state)

                if planning:
                    self._last_valid_mode = mode_state.state
                    return planning

        if self._last_valid_mode is not None:
            fallback = mode_plannings.get(self._last_valid_mode)

            if fallback:
                return fallback

        return DEFAULT_PLANNING
