"""Sensors for Chauffage Intelligent."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_CLIMATE,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
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
            DeriveSensor(entry),
        ]
    )


class TempsDeChauffeSensor(SensorEntity):
    """Estimated heating time."""

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

        temp_ext = self._get_temperature(
            config.get(CONF_TEMP_EXT)
        )

        consigne = self._get_consigne(
            config.get(CONF_CLIMATE)
        )

        if temp_int is None or consigne is None:
            return 0

        if temp_ext is None:
            temp_ext = 10

        coefficient = self._get_coefficient()

        delta = consigne - temp_int

        facteur_ext = 1 + (
            (temp_int - temp_ext) / 50
        )

        facteur_ext = min(
            max(facteur_ext, 0.7),
            1.5,
        )

        if delta > 0.3:
            return round(
                delta * coefficient * facteur_ext
            )

        return 0

    def _get_coefficient(self) -> float:
        """Read the coefficient entity."""

        entity_id = (
            f"number.chauffage_intelligent_"
            f"{self._slugify(self._entry.title)}_coefficient"
        )

        state = self.hass.states.get(entity_id)

        if state is None:
            return 25

        try:
            return float(state.state)
        except (ValueError, TypeError):
            return 25

    @staticmethod
    def _slugify(value: str) -> str:
        """Create a simple entity-id compatible name."""

        import unicodedata

        value = unicodedata.normalize(
            "NFKD",
            value,
        ).encode(
            "ascii",
            "ignore",
        ).decode(
            "ascii",
        )

        return (
            value.lower()
            .replace(" ", "_")
            .replace("-", "_")
        )

    def _get_temperature(
        self,
        entity_id: str | None,
    ) -> float | None:
        """Read a temperature entity."""

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
        """Read the target temperature from the climate."""

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


class DeriveSensor(RestoreEntity, SensorEntity):
    """Heating temperature rise per minute."""

    _attr_native_unit_of_measurement = "°C/min"
    _attr_icon = "mdi:chart-line"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the sensor."""

        self._entry = entry

        self._attr_unique_id = (
            f"{entry.entry_id}_derive"
        )

        self._attr_name = "Dérive"

        self._last_temperature = None
        self._last_time = None
        self._derive = 0.0

    async def async_added_to_hass(self) -> None:
        """Restore the previous value after restart."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            try:
                self._derive = float(last_state.state)
            except (ValueError, TypeError):
                self._derive = 0.0

    @property
    def native_value(self) -> float:
        """Return the current temperature rise per minute."""

        return round(self._derive, 4)

    async def async_update(self) -> None:
        """Update the temperature rise calculation."""

        config = self._entry.data

        temp_entity = config.get(CONF_TEMP_INT)
        climate_entity = config.get(CONF_CLIMATE)

        if not temp_entity or not climate_entity:
            return

        temperature_state = self.hass.states.get(temp_entity)
        climate_state = self.hass.states.get(climate_entity)

        if temperature_state is None or climate_state is None:
            return

        try:
            temperature = float(temperature_state.state)
        except (ValueError, TypeError):
            return

        # On ne calcule la dérive que lorsque le chauffage
        # indique réellement qu'il est en train de chauffer.
        hvac_action = climate_state.attributes.get("hvac_action")

        if hvac_action != "heating":
            self._last_temperature = None
            self._last_time = None
            return

        now = datetime.now()

        # Première mesure pendant la chauffe.
        if self._last_temperature is None:
            self._last_temperature = temperature
            self._last_time = now
            return

        elapsed_minutes = (
            now - self._last_time
        ).total_seconds() / 60

        # Sécurité : il faut suffisamment de temps entre
        # deux mesures pour avoir une dérive exploitable.
        if elapsed_minutes < 1:
            return

        delta_temperature = (
            temperature - self._last_temperature
        )

        derive = (
            delta_temperature / elapsed_minutes
        )

        # On ne conserve que les valeurs positives.
        # Si la température baisse pendant la chauffe,
        # on considère qu'il n'y a pas de montée en température.
        if derive > 0:
            self._derive = round(derive, 4)

        self._last_temperature = temperature
        self._last_time = now

        self.async_write_ha_state()
