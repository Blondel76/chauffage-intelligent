"""Sensor entities for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
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
    CONF_AREA,
    CONF_CLIMATE,
    CONF_DERIVE,
    CONF_PLANNING,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    COEFFICIENT_DEFAULT,
    DOMAIN,
    slugify_area,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Chauffage Intelligent sensors."""

    area_name = entry.data[CONF_AREA]
    area_slug = slugify_area(area_name)

    async_add_entities(
        [
            TempsDeChauffeSensor(entry, area_slug),
            DeriveSensor(entry, area_slug),
            HeurePlanningSensor(entry, area_slug),
            HeurePlanningPrecedentSensor(entry, area_slug),
            HeureAnticipeeSensor(entry, area_slug),
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

        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_has_entity_name = True
        self._attr_name = name

        # Impose l'entity_id directement plutôt que de le "suggérer" :
        # HA combine normalement Area + Device + Nom, ce qui causait le
        # doublon. En fixant entity_id ici, on bypass complètement ce
        # mécanisme de composition automatique.
        self.entity_id = f"sensor.{key}_{area_slug}"

        self._attr_suggested_object_id = f"{key}_{area_slug}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

        # Informations de configuration de la pièce.
        # La carte Lovelace pourra les utiliser pour
        # retrouver automatiquement les entités sources.
        self._attr_extra_state_attributes = {
            "chauffage_intelligent": True,
            "piece": entry.data[CONF_AREA],
            "piece_slug": area_slug,
            "temperature_exterieure": entry.data.get(CONF_TEMP_EXT),
            "temperature_interieure": entry.data.get(CONF_TEMP_INT),
            "planning": entry.data.get(CONF_PLANNING),
            "climate": entry.data.get(CONF_CLIMATE),
            "derive_source": entry.data.get(CONF_DERIVE),
        }
        
        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

        # Informations de configuration de la pièce.
        # La carte Lovelace pourra les utiliser pour
        # retrouver automatiquement les entités sources.
        self._attr_extra_state_attributes = {
            "chauffage_intelligent": True,
            "piece": entry.data[CONF_AREA],
            "piece_slug": area_slug,
            "temperature_exterieure": entry.data.get(CONF_TEMP_EXT),
            "temperature_interieure": entry.data.get(CONF_TEMP_INT),
            "planning": entry.data.get(CONF_PLANNING),
            "climate": entry.data.get(CONF_CLIMATE),
            "derive_source": entry.data.get(CONF_DERIVE),
        }

    def _read_coefficient(self) -> float:
        """Read the current coefficient number entity, with fallback."""

        coefficient_entity = f"number.coefficient_{self._area_slug}"
        coefficient_state = self.hass.states.get(coefficient_entity)

        if coefficient_state is not None:
            try:
                return float(coefficient_state.state)
            except (ValueError, TypeError):
                pass

        return COEFFICIENT_DEFAULT


class TempsDeChauffeSensor(ChauffageSensorBase):
    """Heating time sensor."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(entry, area_slug, "temps_de_chauffe", "Temps de chauffe")

    def update(self) -> None:
        """Update heating time."""

        self._attr_native_value = calculate_heating_time(
            self.hass,
            self._entry.data,
            self._read_coefficient(),
        )


class DeriveSensor(ChauffageSensorBase):
    """Derivative sensor."""

    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "°C/min"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(entry, area_slug, "derive", "Dérive")

    def update(self) -> None:
        """Read the configured derivative sensor."""

        entity_id = self._entry.data.get(CONF_DERIVE)

        state = self.hass.states.get(entity_id) if entity_id else None

        if state is None:
            self._attr_native_value = 0
            return

        try:
            self._attr_native_value = float(state.state)
        except (ValueError, TypeError):
            self._attr_native_value = 0


class HeurePlanningSensor(ChauffageSensorBase):
    """Next planning sensor."""

    _attr_icon = "mdi:clock-outline"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(entry, area_slug, "heure_planning", "Heure planning")

    def update(self) -> None:
        """Update."""

        self._attr_native_value = get_next_schedule(self.hass, self._entry.data)


class HeurePlanningPrecedentSensor(ChauffageSensorBase):
    """Previous planning sensor."""

    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(
            entry, area_slug, "heure_planning_precedent", "Heure planning précédent"
        )

    def update(self) -> None:
        """Update."""

        self._attr_native_value = get_previous_schedule(self.hass, self._entry.data)


class HeureAnticipeeSensor(ChauffageSensorBase):
    """Anticipated heating time."""

    _attr_icon = "mdi:clock-start"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(entry, area_slug, "heure_anticipee", "Heure anticipée")

    def update(self) -> None:
        """Update."""

        self._attr_native_value = calculate_anticipated_time(
            self.hass,
            self._entry.data,
            self._read_coefficient(),
        )
