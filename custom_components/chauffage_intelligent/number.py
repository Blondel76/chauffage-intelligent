"""Number entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_AREA,
    COEFFICIENT_DEFAULT,
    COEFFICIENT_MAX,
    COEFFICIENT_MIN,
    DOMAIN,
    slugify_area,
)


def _clamp(value: float) -> float:
    """Clamp a coefficient value to the allowed range."""
    return min(max(value, COEFFICIENT_MIN), COEFFICIENT_MAX)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the coefficient number."""

    area_name = entry.data[CONF_AREA]
    area_slug = slugify_area(area_name)

    async_add_entities(
        [
            CoefficientNumber(
                entry,
                area_slug,
            )
        ]
    )


class CoefficientNumber(
    RestoreEntity,
    NumberEntity,
):
    """Heating coefficient for a room."""

    _attr_native_min_value = COEFFICIENT_MIN
    _attr_native_max_value = COEFFICIENT_MAX
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = None
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:tune"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize the coefficient."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = f"{entry.entry_id}_coefficient"
        self._attr_name = f"Coefficient {area_slug.replace('_', ' ').title()}"
        self._attr_native_value = COEFFICIENT_DEFAULT

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

    async def async_added_to_hass(self) -> None:
        """Restore the previous coefficient."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is None:
            self._attr_native_value = COEFFICIENT_DEFAULT
            return

        try:
            self._attr_native_value = _clamp(float(last_state.state))
        except (ValueError, TypeError):
            self._attr_native_value = COEFFICIENT_DEFAULT

    async def async_set_native_value(self, value: float) -> None:
        """Set the coefficient manually."""

        self._attr_native_value = _clamp(float(value))
        self.async_write_ha_state()
