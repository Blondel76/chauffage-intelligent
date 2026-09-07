"""Switch entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_AREA, DOMAIN, slugify_area


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the window override switch."""

    area_name = entry.data[CONF_AREA]
    area_slug = slugify_area(area_name)

    async_add_entities([WindowOverrideSwitch(entry, area_slug)])


class WindowOverrideSwitch(RestoreEntity, SwitchEntity):
    """Manual override used as the window/door state when no sensor is configured."""

    _attr_icon = "mdi:window-open-variant"
    _attr_has_entity_name = True
    _attr_name = "Fenêtre ouverte (manuel)"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = f"{entry.entry_id}_window_override"
        self.entity_id = f"switch.fenetre_ouverte_{area_slug}"
        self._attr_suggested_object_id = f"fenetre_ouverte_{area_slug}"

        self._attr_is_on = False

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

    async def async_added_to_hass(self) -> None:
        """Restore the previous state."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on (simulate an open window)."""

        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off (simulate a closed window)."""

        self._attr_is_on = False
        self.async_write_ha_state()
