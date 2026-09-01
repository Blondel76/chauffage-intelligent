"""Number entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the coefficient number."""

    async_add_entities(
        [
            CoefficientNumber(entry),
        ]
    )


class CoefficientNumber(NumberEntity):
    """Heating coefficient for a room."""

    _attr_native_min_value = 10
    _attr_native_max_value = 60
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = ""
    _attr_icon = "mdi:tune"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the coefficient."""

        self._entry = entry

        self._attr_unique_id = (
            f"{entry.entry_id}_coefficient"
        )

        self._attr_name = "Coefficient"

        # Valeur initiale
        self._attr_native_value = 25.0

    async def async_set_native_value(
        self,
        value: float,
    ) -> None:
        """Set the coefficient manually."""

        self._attr_native_value = min(
            max(float(value), 10),
            60,
        )

        self.async_write_ha_state()
