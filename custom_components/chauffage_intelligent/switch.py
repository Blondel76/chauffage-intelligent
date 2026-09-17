"""Switch entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_AREA,
    CONF_CLIMATE,
    CONF_DOOR_SENSOR,
    CONF_HEATER_ENTITY,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_ROOM,
    slugify_area,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the master heating switch (central), or the window override switch (room)."""

    if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
        async_add_entities([ChauffageGeneralSwitch(entry)])
        return

    if entry.data.get(CONF_DOOR_SENSOR):
        return

    area_name = entry.data[CONF_AREA]
    area_slug = slugify_area(area_name)

    async_add_entities([WindowOverrideSwitch(entry, area_slug)])


class ChauffageGeneralSwitch(RestoreEntity, SwitchEntity):
    """Master switch: turns every room's climate heat mode on or off."""

    _attr_icon = "mdi:radiator"
    _attr_has_entity_name = True
    _attr_name = "Chauffage général"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""

        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_chauffage_general"
        self.entity_id = "switch.chauffage_general"
        self._attr_suggested_object_id = "chauffage_general"

        self._attr_is_on = True

    async def async_added_to_hass(self) -> None:
        """Restore the previous state and apply it immediately."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on: set every room's climate to heat mode."""

        self._attr_is_on = True
        self.async_write_ha_state()
        await self._apply_to_all_rooms("heat")

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off: set every room's climate to off."""

        self._attr_is_on = False
        self.async_write_ha_state()
        await self._apply_to_all_rooms("off")

    async def _apply_to_all_rooms(self, hvac_mode: str) -> None:
        """Apply the given hvac_mode to every room's climate entity (and its valve, if any)."""

        for room_entry in self.hass.config_entries.async_entries(DOMAIN):
            if room_entry.data.get(ENTRY_TYPE) != ENTRY_TYPE_ROOM:
                continue

            climate_entity = room_entry.data.get(CONF_CLIMATE)

            if not climate_entity:
                continue

            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": climate_entity, "hvac_mode": hvac_mode},
            )

            # Si la vanne (chauffage gaz) a été coupée manuellement, la
            # remettre en cohérence : sinon set_temperature (29/7°C) via
            # le scheduler n'a aucun effet tant qu'elle reste en "off".
            heater_entity = room_entry.data.get(CONF_HEATER_ENTITY)

            if heater_entity and heater_entity.startswith("climate."):
                await self.hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {"entity_id": heater_entity, "hvac_mode": hvac_mode},
                )


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
