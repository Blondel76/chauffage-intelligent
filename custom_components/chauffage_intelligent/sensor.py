"""Sensor entities for Chauffage Intelligent."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .button import SECURITY_REARM_EVENT
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
    SECURITY_STATE_OK,
    slugify_area,
)
from .security import compute_security_state, get_all_critical_entities

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
# SECURITE
# ==========================================================


class SecuriteChauffageSensor(RestoreEntity, SensorEntity):
    """House-wide heating security status."""

    _attr_icon = "mdi:shield-check"
    _attr_has_entity_name = True
    _attr_name = "Securite chauffage"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""
        self._entry = entry
        self._remove_periodic_listener = None
        self._remove_rearm_listener = None
        self._remove_critical_listener = None

        self._attr_unique_id = f"{entry.entry_id}_securite"
        self.entity_id = "sensor.securite_chauffage"
        self._attr_suggested_object_id = "securite_chauffage"

        self._attr_native_value = SECURITY_STATE_OK

    async def async_added_to_hass(self) -> None:
        """Restore state and start security monitoring."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_native_value = last_state.state

        # Écoute de l'événement de réarmement
        self._remove_rearm_listener = self.hass.bus.async_listen(
            SECURITY_REARM_EVENT,
            self._handle_rearm,
        )

        # Recalcul immédiat dès qu'une entité critique change d'état
        # (ex. un climate coupé manuellement) plutôt que d'attendre le
        # sondage périodique ci-dessous.
        entites_a_surveiller = get_all_critical_entities(self.hass) + [
            "switch.chauffage_general"
        ]

        self._remove_critical_listener = async_track_state_change_event(
            self.hass,
            entites_a_surveiller,
            self._handle_critical_entity_change,
        )

        # Recalcul automatique toutes les 5 secondes (filet de sécurité,
        # couvre par ex. l'ajout d'une pièce après le démarrage)
        self._remove_periodic_listener = async_track_time_interval(
            self.hass,
            self._async_periodic_security_check,
            SECURITY_CHECK_INTERVAL,
        )

        # Calcul initial de l'état interne
        self._update_state(rearm_pressed=False)

    async def async_will_remove_from_hass(self) -> None:
        """Stop all security listeners."""
        if self._remove_periodic_listener is not None:
            self._remove_periodic_listener()
            self._remove_periodic_listener = None

        if self._remove_rearm_listener is not None:
            self._remove_rearm_listener()
            self._remove_rearm_listener = None

        if self._remove_critical_listener is not None:
            self._remove_critical_listener()
            self._remove_critical_listener = None

        await super().async_will_remove_from_hass()

    @callback
    def _update_state(self, rearm_pressed: bool = False) -> None:
        """Calcule et met à jour la valeur interne sans écrire sur le bus."""
        switch_state = self.hass.states.get("switch.chauffage_general")
        master_on = switch_state is not None and switch_state.state == "on"

        current_val = getattr(self, "_attr_native_value", SECURITY_STATE_OK)

        self._attr_native_value = compute_security_state(
            hass=self.hass,
            master_switch_on=master_on,
            current_state=current_val,
            rearm_pressed=rearm_pressed,
        )

    @callback
    def _async_periodic_security_check(self, _now=None) -> None:
        """Recalcul périodique de l'état de sécurité."""
        self._update_state(rearm_pressed=False)
        self.async_write_ha_state()

    @callback
    def _handle_critical_entity_change(self, event: Event) -> None:
        """Réévaluation immédiate suite au changement d'une entité critique."""
        self._update_state(rearm_pressed=False)
        self.async_write_ha_state()

    @callback
    def _handle_rearm(self, event: Event) -> None:
        """Réarmement manuel déclenché par l'événement."""
        self._update_state(rearm_pressed=True)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Mise à jour asynchrone standard."""
        self._update_state(rearm_pressed=False)


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
        """Read the current coefficient number entity."""
        coefficient_entity = f"number.coefficient_{self._area_slug}"
        coefficient_state = self.hass.states.get(coefficient_entity)

        if coefficient_state is not None:
            try:
                return float(coefficient_state.state)
            except (ValueError, TypeError):
                pass

        return COEFFICIENT_DEFAULT

    def _get_planning(self) -> str:
        """Fetch the currently resolved planning string."""
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
    """Derivative sensor."""

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

    @callback
    def _handle_temp_change(self, event: Event) -> None:
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

        self._attr_native_value = round(
            (temp - self._reference_temp) / delta_minutes,
            3,
        )

        self._reference_time = now
        self._reference_temp = temp

        self.async_write_ha_state()


class HeurePlanningSensor(ChauffageSensorBase):
    """Next schedule sensor."""

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
            entry,
            area_slug,
            "heure_planning_precedent",
            "Heure planning precedent",
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
