"""Number entities for Chauffage Intelligent."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .calculations import (
    calculate_new_coefficient,
    is_heater_open,
    is_room_really_heating,
    is_window_open,
    learning_weight,
)
from .const import (
    CONF_AREA,
    CONF_HEATER_ENTITY,
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
    """Heating coefficient for a room, learned from the derivative.

    Apprentissage à la Jeedom : le poids de chaque nouvelle mesure diminue
    avec le nombre de mesures déjà apprises (rapide au début, de plus en
    plus lent ensuite : le coefficient se fige presque). Une mesure n'est
    retenue que si elle est « propre » : chauffe réelle continue pendant
    toute la mesure (vanne ouverte), fenêtre fermée, pas de boost.
    """

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
        self._samples = 0

        self._attr_unique_id = f"{entry.entry_id}_coefficient"
        self._attr_has_entity_name = True
        self._attr_name = "Coefficient"

        self.entity_id = f"number.coefficient_{area_slug}"
        self._attr_suggested_object_id = f"coefficient_{area_slug}"

        self._attr_native_value = COEFFICIENT_DEFAULT

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Nombre de mesures apprises et poids de la prochaine mesure."""

        return {
            "mesures": self._samples,
            "poids_apprentissage": round(learning_weight(self._samples), 3),
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

            try:
                self._samples = max(0, int(last_state.attributes.get("mesures", 0)))
            except (ValueError, TypeError):
                self._samples = 0

        # On écoute désormais notre propre capteur de dérive interne,
        # plus un capteur externe choisi par l'utilisateur.
        derive_entity_id = f"sensor.derive_{self._area_slug}"

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

    def _learning_allowed(self, config: dict, derive_entity_id: str) -> bool:
        """True si la mesure de dérive est propre et peut être apprise."""

        derive_state = self.hass.states.get(derive_entity_id)

        # Dérive mesurée alors que la pièce chauffait à chaque relevé.
        if (
            derive_state is None
            or derive_state.attributes.get("chauffe_continue") is not True
        ):
            return False

        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        boost = data.get("boost")

        if boost is not None and boost.active:
            return False

        if is_window_open(self.hass, config):
            return False

        if not is_room_really_heating(self.hass, config):
            return False

        return is_heater_open(self.hass, config.get(CONF_HEATER_ENTITY))

    async def _handle_derive_change(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Recalculate the coefficient when the derivative sensor updates."""

        derive_entity_id = f"sensor.derive_{self._area_slug}"
        config = {**self._entry.data, **self._entry.options}

        if not self._learning_allowed(config, derive_entity_id):
            return

        nouveau = calculate_new_coefficient(
            self.hass,
            config,
            self._attr_native_value,
            derive_entity_id,
            learning_weight(self._samples),
        )

        if nouveau is None:
            return

        self._attr_native_value = nouveau
        self._samples += 1
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set the coefficient manually (overrides the learned value)."""

        self._attr_native_value = _clamp(float(value))
        self.async_write_ha_state()
