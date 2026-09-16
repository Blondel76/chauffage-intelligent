"""Sensor entities for Chauffage Intelligent."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .calculations import (
    calculate_anticipated_time,
    calculate_heating_time,
    get_next_schedule,
    get_previous_schedule,
)
from .const import (
    CONF_AREA,
    CONF_CLIMATE,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    COEFFICIENT_DEFAULT,
    DERIVE_INTERVAL_MINUTES,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_ROOM,
    slugify_area,
)
from .security import compute_security_state


# La sécurité doit être recalculée périodiquement, même si Home Assistant
# n'envoie pas d'événement de changement d'état pour le thermostat.
SECURITY_CHECK_INTERVAL = timedelta(seconds=5)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Chauffage Intelligent sensors."""

    if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
        async_add_entities([SecuriteChauffageSensor(entry)])
        return

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


# ==========================================================
# SECURITE (config centrale)
# ==========================================================


class SecuriteChauffageSensor(RestoreEntity, SensorEntity):
    """House-wide heating safety status."""

    _attr_icon = "mdi:shield-check"
    _attr_has_entity_name = True
    _attr_name = "Securite chauffage"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""

        self._entry = entry
        self._remove_periodic_listener = None

        self._attr_unique_id = f"{entry.entry_id}_securite"
        self.entity_id = "sensor.securite_chauffage"
        self._attr_suggested_object_id = "securite_chauffage"

    async def async_added_to_hass(self) -> None:
        """Restore previous state and start periodic security checks."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._attr_native_value = last_state.state

        # Ne pas dépendre uniquement des événements d'état : certains
        # thermostats/intégrations ne signalent pas toujours correctement
        # le passage à off. Le contrôle est donc répété régulièrement.
        self._remove_periodic_listener = async_track_time_interval(
            self.hass,
            self._async_periodic_security_check,
            SECURITY_CHECK_INTERVAL,
        )

        self._async_periodic_security_check()

    async def async_will_remove_from_hass(self) -> None:
        """Stop periodic security checks."""
        if self._remove_periodic_listener is not None:
            self._remove_periodic_listener()
            self._remove_periodic_listener = None
        await super().async_will_remove_from_hass()

    def _async_periodic_security_check(self, _now=None) -> None:
        """Recalculate and publish the security state."""
        self.update()
        self.async_write_ha_state()

    def update(self) -> None:
        """Compute the current status using the security rules module."""

        # 1. État du commutateur maître
        switch_state = self.hass.states.get("switch.chauffage_general")
        master_on = switch_state is not None and switch_state.state == "on"

        # 2. État du bouton de réarmement
        rearm_state = self.hass.states.get("button.rearmement_securite_chauffage")
        rearm_pressed = rearm_state is not None and rearm_state.state == "on"

        # 3. Calcul du nouvel état avec maintien de l'alerte
        self._attr_native_value = compute_security_state(
            hass=self.hass,
            master_switch_on=master_on,
            current_state=self._attr_native_value,
            rearm_pressed=rearm_pressed,
        )


# ==========================================================
# PIECE
# ==========================================================


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

        self.entity_id = f"sensor.{key}_{area_slug}"
        self._attr_suggested_object_id = f"{key}_{area_slug}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

        self._attr_extra_state_attributes = {
            "chauffage_intelligent": True,
            "piece": entry.data[CONF_AREA],
            "piece_slug": area_slug,
            "temperature_exterieure": entry.data.get(CONF_TEMP_EXT),
            "temperature_interieure": entry.data.get(CONF_TEMP_INT),
            "climate": entry.data.get(CONF_CLIMATE),
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

    def _get_planning(self) -> str:
        """Fetch the currently resolved planning string for this room."""

        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        resolver = data.get("resolver")

        if resolver is None:
            return ""

        return resolver.get_active_planning()


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


class DeriveSensor(RestoreEntity, ChauffageSensorBase):
    """Derivative sensor, computed internally from the interior temperature."""

    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "°C/min"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(entry, area_slug, "derive", "Derive")

        self._attr_native_value = 0
        self._reference_time = None
        self._reference_temp = None
        self._remove_listener = None

    async def async_added_to_hass(self) -> None:
        """Start listening to the interior temperature."""

        await super().async_added_to_hass()

        temp_entity_id = self._entry.data.get(CONF_TEMP_INT)

        if temp_entity_id:
            self._remove_listener = async_track_state_change_event(
                self.hass,
                [temp_entity_id],
                self._handle_temp_change,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up the listener."""

        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    async def _handle_temp_change(self, event) -> None:
        """React to a new interior temperature reading."""

        new_state = event.data.get("new_state")

        if new_state is None:
            return

        try:
            temp = float(new_state.state)
        except (ValueError, TypeError):
            return

        now = dt_util.utcnow()

        if self._reference_time is None:
            self._reference_time = now
            self._reference_temp = temp
            return

        delta_minutes = (now - self._reference_time).total_seconds() / 60

        if delta_minutes < DERIVE_INTERVAL_MINUTES:
            return

        self._attr_native_value = round((temp - self._reference_temp) / delta_minutes, 3)

        self._reference_time = now
        self._reference_temp = temp

        self.async_write_ha_state()


class HeurePlanningSensor(ChauffageSensorBase):
    """Next planning sensor."""

    _attr_icon = "mdi:clock-outline"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(entry, area_slug, "heure_planning", "Heure planning")

    def update(self) -> None:
        """Update."""

        self._attr_native_value = get_next_schedule(self._get_planning())


class HeurePlanningPrecedentSensor(ChauffageSensorBase):
    """Previous schedule sensor."""

    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(
            entry, area_slug, "heure_planning_precedent", "Heure planning precedent"
        )

    def update(self) -> None:
        """Update."""

        self._attr_native_value = get_previous_schedule(self._get_planning())


class HeureAnticipeeSensor(ChauffageSensorBase):
    """Anticipated heating time."""

    _attr_icon = "mdi:clock-start"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(entry, area_slug, "heure_anticipee", "Heure anticipee")

    def update(self) -> None:
        """Update."""

        self._attr_native_value = calculate_anticipated_time(
            self.hass,
            self._entry.data,
            self._get_planning(),
            self._read_coefficient(),
        )
