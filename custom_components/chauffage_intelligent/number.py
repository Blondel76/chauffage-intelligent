"""Number entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Chauffage Intelligent numbers."""

    async_add_entities(
        [
            CoefficientChauffageNumber(entry),
        ]
    )


class CoefficientChauffageNumber(NumberEntity):
    """Heating coefficient."""

    _attr_native_min_value = 10
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_native_unit_of_measurement = None
    _attr_icon = "mdi:tune"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the coefficient."""

        self._entry = entry

        self._attr_unique_id = (
            f"{entry.entry_id}_coefficient"
        )

        self._attr_name = "Coefficient"

        self._value = 25

    @property
    def native_value(self) -> float:
        """Return the current coefficient."""

        return self._value

    async def async_set_native_value(
        self,
        value: float,
    ) -> None:
        """Set the coefficient."""

        self._value = value

        self.async_write_ha_state()
