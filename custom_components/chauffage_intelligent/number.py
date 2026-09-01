"""Number entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
)
from homeassistant.helpers.restore_state import (
    RestoreEntity,
)

from .calculations import calculate_new_coefficient
from .const import DOMAIN, slugify_area


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the coefficient number."""

    area_name = entry.data["area"]
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

    _attr_native_min_value = 10
    _attr_native_max_value = 60
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = ""
    _attr_icon = "mdi:tune"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = (
            f"{entry.entry_id}_coefficient"
        )

        self._attr_name = (
            f"Coefficient "
            f"{area_slug.replace('_', ' ').title()}"
        )

        # Première valeur uniquement.
        # RestoreEntity reprendra ensuite la dernière valeur.
        self._attr_native_value = 25.0

    async def async_added_to_hass(
        self,
    ) -> None:
        """Restore previous value."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is None:
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
        """Set coefficient manually."""

        self._attr_native_value = min(
            max(float(value), 10),
            60,
        )

        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Automatically learn the coefficient."""

        if self._attr_native_value is None:
            return

        ancien = float(
            self._attr_native_value
        )

        nouveau = calculate_new_coefficient(
            self.hass,
            self._entry.data,
            ancien,
        )

        if nouveau is None:
            return

        # Évite les écritures inutiles.
        if abs(nouveau - ancien) < 0.05:
            return

        self._attr_native_value = nouveau

        self.async_write_ha_state()

