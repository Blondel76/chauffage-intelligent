"""Button entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ENTRY_TYPE, ENTRY_TYPE_CENTRAL


SECURITY_REARM_EVENT = "chauffage_intelligent_security_rearm"


async def async_setup_entry(
    hass,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the security reset button."""

    if entry.data.get(ENTRY_TYPE) != ENTRY_TYPE_CENTRAL:
        return

    async_add_entities([SecuriteRearmementButton(entry)])


class SecuriteRearmementButton(ButtonEntity):
    """Button used to rearm the heating security."""

    _attr_icon = "mdi:refresh"
    _attr_has_entity_name = True
    _attr_name = "Réarmement sécurité chauffage"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""
        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_rearmement"
        self.entity_id = "button.rearmement_securite_chauffage"
        self._attr_suggested_object_id = "rearmement_securite_chauffage"

    async def async_press(self) -> None:
        """Request a security rearmement."""
        self.hass.bus.async_fire(
            SECURITY_REARM_EVENT,
            {
                "entry_id": self._entry.entry_id,
            },
        )
