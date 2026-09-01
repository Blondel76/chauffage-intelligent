"""Sensor entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .calculations import (
    calculate_anticipated_time,
    calculate_heating_time,
    get_next_schedule,
    get_previous_schedule,
)
from .const import (
    CONF_DERIVE,
    DOMAIN,
    slugify_area,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Chauffage Intelligent sensors."""

    area_name = entry.data["area"]
    area_slug = slugify_area(area_name)

    async_add_entities(
        [
            TempsDeChauffeSensor(
                entry,
                area_slug,
            ),
            DeriveSensor(
                entry,
                area_slug,
            ),
            HeurePlanningSensor(
                entry,
                area_slug,
            ),
            HeurePlanningPrecedentSensor(
                entry,
                area_slug,
            ),
            HeureAnticipeeSensor(
                entry,
                area_slug,
            ),
        ]
    )


class ChauffageSensorBase(SensorEntity):
    """Base sensor."""

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
        key: str,
        name: str,
    ) -> None:
        """Initialize."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = (
            f"{entry.entry_id}_{key}"
        )

        self._attr_name = (
            f"{name} "
            f"{area_slug.replace('_', ' ').title()}"
        )

        # Informations de configuration de la pièce.
        # La carte Lovelace pourra les utiliser pour
        # retrouver automatiquement les entités sources.
        self._attr_extra_state_attributes = {
            "chauffage_intelligent": True,
            "piece": entry.data["area"],
            "piece_slug": area_slug,
            "temperature_exterieure": entry.data.get(
                "Température extérieur"
            ),
            "temperature_interieure": entry.data.get(
                "Température intérieur"
            ),
            "planning": entry.data.get(
                "Planning de la pièce utilisé"
            ),
            "climate": entry.data.get(
                "Thermostat de la pièce"
            ),
            "derive_source": entry.data.get(
                "derive capteur de température"
            ),
        }


class TempsDeChauffeSensor(
    ChauffageSensorBase
):
    """Heating time sensor."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "temps_de_chauffe",
            "Temps de chauffe",
        )

    def update(self) -> None:
        """Update heating time."""

        coefficient_entity = (
            f"number.coefficient_{self._area_slug}"
        )

        coefficient_state = self.hass.states.get(
            coefficient_entity
        )

        coefficient = 25.0

        if coefficient_state is not None:
            try:
                coefficient = float(
                    coefficient_state.state
                )
            except (ValueError, TypeError):
                pass

        self._attr_native_value = (
            calculate_heating_time(
                self.hass,
                self._entry.data,
                coefficient,
            )
        )


class DeriveSensor(
    ChauffageSensorBase
):
    """Derivative sensor."""

    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "°C/min"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "derive",
            "Dérive",
        )

    def update(self) -> None:
        """Read the configured derivative sensor."""

        entity_id = self._entry.data.get(
            CONF_DERIVE
        )

        state = self.hass.states.get(
            entity_id
        )

        if state is None:
            self._attr_native_value = 0
            return

        try:
            self._attr_native_value = float(
                state.state
            )
        except (ValueError, TypeError):
            self._attr_native_value = 0


class HeurePlanningSensor(
    ChauffageSensorBase
):
    """Next planning sensor."""

    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "heure_planning",
            "Heure planning",
        )

    def update(self) -> None:
        """Update."""

        self._attr_native_value = (
            get_next_schedule(
                self.hass,
                self._entry.data,
            )
        )


class HeurePlanningPrecedentSensor(
    ChauffageSensorBase
):
    """Previous planning sensor."""

    _attr_icon = "mdi:clock-check-outline"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "heure_planning_precedent",
            "Heure planning précédent",
        )

    def update(self) -> None:
        """Update."""

        self._attr_native_value = (
            get_previous_schedule(
                self.hass,
                self._entry.data,
            )
        )


class HeureAnticipeeSensor(
    ChauffageSensorBase
):
    """Anticipated heating time."""

    _attr_icon = "mdi:clock-start"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "heure_anticipee",
            "Heure anticipée",
        )

    def update(self) -> None:
        """Update."""

        coefficient_entity = (
            f"number.coefficient_{self._area_slug}"
        )

        coefficient_state = self.hass.states.get(
            coefficient_entity
        )

        coefficient = 25.0

        if coefficient_state is not None:
            try:
                coefficient = float(
                    coefficient_state.state
                )
            except (ValueError, TypeError):
                pass

        self._attr_native_value = (
            calculate_anticipated_time(
                self.hass,
                self._entry.data,
                coefficient,
            )
        )
