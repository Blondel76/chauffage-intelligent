"""Button entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the security reset button, only for the central entry."""

    if entry.data.get("entry_type") != "central":
        return

    async_add_entities([SecuriteReearmementButton(entry)])


class SecuriteReearmementButton(ButtonEntity):
    """Reset button for the heating security status.

    Currently a stub: no rule engine exists yet to latch a 'rouge' state,
    so pressing this does nothing observable. It will be wired once the
    security rules are built.
    """

    _attr_icon = "mdi:refresh"
    _attr_has_entity_name = True
    _attr_name = "Réarmement sécurité chauffage"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""

        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_reearmement"
        self.entity_id = "button.reearmement_securite_chauffage"
        self._attr_suggested_object_id = "reearmement_securite_chauffage"

    async def async_press(self) -> None:
        """Handle the button press (no-op for now)."""

        return
