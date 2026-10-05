"""Binary sensors (pièce froide / pièce chaude) for Chauffage Intelligent."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .calculations import (
    get_central_boiler_entity,
    is_room_cold,
    is_room_hot,
)
from .const import (
    CONF_AREA,
    CONF_CLIMATE,
    CONF_TEMP_INT,
    DOMAIN,
    slugify_area,
)

REFRESH_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the cold / hot binary sensors of a room."""

    area_slug = slugify_area(entry.data[CONF_AREA])

    async_add_entities(
        [
            PieceFroideBinarySensor(entry, area_slug),
            PieceChaudeBinarySensor(entry, area_slug),
        ]
    )


class _RoomThermalBinarySensor(BinarySensorEntity):
    """Base : état recalculé dès qu'une entité concernée change."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    _key = ""
    _label = ""

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_name = self._label

        self.entity_id = f"binary_sensor.{self._key}_{area_slug}"
        self._attr_suggested_object_id = f"{self._key}_{area_slug}"

        self._attr_is_on = False

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

    def _config(self) -> dict:
        """Config courante (relue à chaque calcul : les écarts sont modifiables)."""

        return {**self._entry.data, **self._entry.options}

    def _compute(self) -> bool:
        """À surcharger."""

        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        """Listen to the relevant entities."""

        await super().async_added_to_hass()

        config = self._config()

        watched = {
            config.get(CONF_TEMP_INT),
            config.get(CONF_CLIMATE),
            get_central_boiler_entity(self.hass),
        }
        watched.discard(None)

        if watched:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, list(watched), self._handle_entity_change
                )
            )

        # Filet de sécurité (changement de chaudière centrale, etc.)
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._handle_periodic, REFRESH_INTERVAL
            )
        )

        # Modification des écarts via l'interface (options flow)
        self.async_on_remove(self._entry.add_update_listener(self._handle_entry_update))

        self._attr_is_on = self._compute()

    @callback
    def _refresh(self) -> None:
        """Recalculate and publish the state."""

        self._attr_is_on = self._compute()
        self.async_write_ha_state()

    @callback
    def _handle_entity_change(self, event: Event) -> None:
        self._refresh()

    @callback
    def _handle_periodic(self, _now=None) -> None:
        self._refresh()

    async def _handle_entry_update(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        self._refresh()


class PieceFroideBinarySensor(_RoomThermalBinarySensor):
    """Vrai si température <= consigne - écart pièce froide."""

    _key = "piece_froide"
    _label = "Pièce froide"
    _attr_icon = "mdi:snowflake-alert"

    def _compute(self) -> bool:
        return is_room_cold(self.hass, self._config())


class PieceChaudeBinarySensor(_RoomThermalBinarySensor):
    """Vrai si température > consigne + écart pièce chaude, en chauffe."""

    _key = "piece_chaude"
    _label = "Pièce chaude"
    _attr_icon = "mdi:fire-alert"

    def _compute(self) -> bool:
        return is_room_hot(self.hass, self._config())
