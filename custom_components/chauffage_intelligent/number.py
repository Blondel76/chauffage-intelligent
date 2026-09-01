"""Number entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity


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


class CoefficientNumber(RestoreEntity, NumberEntity):
    """Heating coefficient for a room."""

    _attr_native_min_value = 10
    _attr_native_max_value = 60
    _attr_native_step = 0.1
    _attr_icon = "mdi:tune"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the coefficient."""

        self._entry = entry

        self._attr_unique_id = (
            f"{entry.entry_id}_coefficient"
        )

        self._attr_name = "Coefficient"

        # Valeur utilisée uniquement lors de la toute première création.
        self._attr_native_value = 25.0

    async def async_added_to_hass(self) -> None:
        """Restore the previous coefficient after a restart."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is None:
            # Première installation : on garde 25.
            self._attr_native_value = 25.0
            return

        try:
            value = float(last_state.state)

            self._attr_native_value = min(
                max(value, 10),
                60,
            )

        except (ValueError, TypeError):
            self._attr_native_value = 25.0

    async def async_set_native_value(
        self,
        value: float,
    ) -> None:
        """Set the coefficient manually or automatically."""

        self._attr_native_value = min(
            max(float(value), 10),
            60,
        )

        self.async_write_ha_state()

