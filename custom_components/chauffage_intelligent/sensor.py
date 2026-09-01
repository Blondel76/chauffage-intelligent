"""Sensors for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CLIMATE,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    DOMAIN,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Chauffage Intelligent sensors."""

    async_add_entities(
        [
            TempsDeChauffeSensor(entry),
        ]
    )


class TempsDeChauffeSensor(SensorEntity):
    """Sensor calculating the estimated heating time."""

    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the sensor."""

        self._entry = entry
        self._attr_unique_id = (
            f"{entry.entry_id}_temps_de_chauffe"
        )

        self._attr_name = "Temps de chauffe"

    @property
    def native_value(self) -> int:
        """Return the estimated heating time."""

        config = self._entry.data

        temp_int = self._get_temperature(
            config.get(CONF_TEMP_INT)
        )

        consigne = self._get_consigne(
            config.get(CONF_CLIMATE)
        )

        temp_ext = self._get_temperature(
            config.get(CONF_TEMP_EXT)
        )

        # Valeurs par défaut si une donnée n'est pas disponible.
        if temp_int is None:
            return 0

        if consigne is None:
            return 0

        if temp_ext is None:
            temp_ext = 10

        # --------------------------------------------------
        # Calcul
        # --------------------------------------------------

        delta = consigne - temp_int

        # Coefficient temporaire.
        # Nous allons créer un vrai "number" plus tard.
        coefficient = 25

        coefficient = min(
            max(coefficient, 10),
            60,
        )

        facteur_ext = 1 + (
            (temp_int - temp_ext) / 50
        )

        facteur_ext = min(
            max(facteur_ext, 0.7),
            1.5,
        )

        if delta > 0.3:
            return round(
                delta
                * coefficient
                * facteur_ext
            )

        return 0

    def _get_temperature(
        self,
        entity_id: str | None,
    ) -> float | None:
        """Read a temperature sensor."""

        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)

        if state is None:
            return None

        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _get_consigne(
        self,
        entity_id: str | None,
    ) -> float | None:
        """Read the target temperature from climate."""

        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)

        if state is None:
            return None

        temperature = state.attributes.get("temperature")

        try:
            return float(temperature)
        except (ValueError, TypeError):
            return None
