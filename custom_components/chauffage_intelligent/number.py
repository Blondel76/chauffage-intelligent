"""Number entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .calculations import calculate_new_coefficient
from .const import (
    CONF_AREA,
    CONF_DERIVE,
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
    """Heating coefficient for a room, self-adjusting from the derivative."""

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
        self._remove_listener = None

        self._attr_unique_id = f"{entry.entry_id}_coefficient"
        self._attr_name = f"Coefficient {area_slug.replace('_', ' ').title()}"
        self._attr_native_value = COEFFICIENT_DEFAULT

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

    async def async_added_to_hass(self) -> None:
        """Restore the previous coefficient and start listening for learning."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is None:
            self._attr_native_value = COEFFICIENT_DEFAULT
        else:
            try:
                self._attr_native_value = _clamp(float(last_state.state))
            except (ValueError, TypeError):
                self._attr_native_value = COEFFICIENT_DEFAULT

        derive_entity_id = self._entry.data.get(CONF_DERIVE)

        if derive_entity_id:
            self._remove_listener = async_track_state_change_event(
                self.hass,
                [derive_entity_id],
                self._handle_derive_change,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up the listener."""

        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    async def _handle_derive_change(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Recalculate the coefficient when the derivative sensor updates."""

        nouveau = calculate_new_coefficient(
            self.hass,
            self._entry.data,
            self._attr_native_value,
        )

        if nouveau is None:
            return

        self._attr_native_value = nouveau
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set the coefficient manually (overrides the learned value)."""

        self._attr_native_value = _clamp(float(value))
        self.async_write_ha_state()
