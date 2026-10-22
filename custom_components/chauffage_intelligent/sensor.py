"""Sensor entities for Chauffage Intelligent."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import dt as dt_util

from .calculations import (
    calculate_aeration,
    calculate_anticipated_time,
    calculate_heating_time,
    calculate_humidity_level,
    get_central_boiler_entity,
    get_next_schedule,
    get_previous_schedule,
    is_boiler_heating,
    is_room_cold,
    is_room_hot,
    is_room_really_heating,
)
from .const import (
    CONF_AREA,
    CONF_BOILER_ENTITY,
    CONF_CLIMATE,
    CONF_HEATER_ENTITY,
    CONF_HEATING_TYPE,
    CONF_TEMP_EXT,
    CONF_TEMP_EXT_CENTRAL,
    CONF_TEMP_INT,
    CONF_WEATHER_ENTITY,
    COEFFICIENT_DEFAULT,
    DERIVE_INTERVAL_MINUTES,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_ROOM,
    FORECAST_REFRESH_MINUTES,
    FORECAST_RETRY_MINUTES,
    HEATING_TYPE_GAS,
    SECURITY_STATE_OFF,
    SIGNAL_CURRENT_PRESET,
    SIGNAL_ROOM_FAULT,
    slugify_area,
)
from .security import compute_room_security, get_room_critical_entities

_LOGGER = logging.getLogger(__name__)

SECURITY_CHECK_INTERVAL = timedelta(seconds=5)
COUNT_REFRESH_INTERVAL = timedelta(seconds=5)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Chauffage Intelligent sensors."""

    if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
        await _async_setup_central(hass, entry, async_add_entities)
        return

    area_name = entry.data[CONF_AREA]
    area_slug = slugify_area(area_name)

    async_add_entities(
        [
            TempsDeChauffeSensor(entry, area_slug),
            HeuresChauffeSensor(entry, area_slug),
            DeriveSensor(entry, area_slug),
            HeurePlanningSensor(entry, area_slug),
            HeurePlanningPrecedentSensor(entry, area_slug),
            HeureAnticipeeSensor(entry, area_slug),
            SecuriteRoomSensor(entry, area_slug),
            ChauffageActuelSensor(entry, area_slug),
            AerationSensor(entry, area_slug),
            HumiditeSensor(entry, area_slug),
        ]
    )


# ==========================================================
# COMPTEURS GLOBAUX (config centrale) : format "n/total"
# ==========================================================


class _CentralCountSensor(SensorEntity):
    """Nombre de pièces répondant à un critère, au format 'n/total'."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    _key = ""
    _label = ""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""

        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_name = self._label

        self.entity_id = f"sensor.{self._key}"
        self._attr_suggested_object_id = self._key

        self._attr_native_value = "0/0"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, "central")},
            "name": "Chauffage Intelligent",
        }

    def _matches(self, room_entry: ConfigEntry) -> bool:
        """À surcharger : la pièce répond-elle au critère ?"""

        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        """Refresh periodically (pièces ajoutées/retirées incluses)."""

        await super().async_added_to_hass()

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._handle_periodic, COUNT_REFRESH_INTERVAL
            )
        )

        self._update_state()

    @callback
    def _update_state(self) -> None:
        rooms = [
            e
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.data.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM
        ]

        matching = sum(1 for e in rooms if self._matches(e))

        self._attr_native_value = f"{matching}/{len(rooms)}"

    @callback
    def _handle_periodic(self, _now=None) -> None:
        self._update_state()
        self.async_write_ha_state()


class PiecesEnChauffeSensor(_CentralCountSensor):
    """Pièces qui chauffent réellement (thermostat en électrique, vanne ouverte + chaudière en gaz)."""

    _key = "pieces_en_chauffe"
    _label = "Pièces en chauffe"
    _attr_icon = "mdi:radiator"

    def _matches(self, room_entry: ConfigEntry) -> bool:
        return is_room_really_heating(
            self.hass, {**room_entry.data, **room_entry.options}
        )


class PiecesFroidesSensor(_CentralCountSensor):
    """Pièces froides (température <= consigne - écart)."""

    _key = "pieces_froides"
    _label = "Pièces froides"
    _attr_icon = "mdi:snowflake"

    def _matches(self, room_entry: ConfigEntry) -> bool:
        return is_room_cold(self.hass, {**room_entry.data, **room_entry.options})


class PiecesChaudesSensor(_CentralCountSensor):
    """Pièces chaudes (température > consigne + écart, thermostat ou chaudière en chauffe)."""

    _key = "pieces_chaudes"
    _label = "Pièces chaudes"
    _attr_icon = "mdi:fire"

    def _matches(self, room_entry: ConfigEntry) -> bool:
        return is_room_hot(self.hass, {**room_entry.data, **room_entry.options})


# ==========================================================
# SECURITE (par pièce)
# ==========================================================


class SecuriteRoomSensor(SensorEntity):
    """État de sécurité d'une pièce : gris (éteint) / vert (ok) / orange (trop froide ou trop chaude) / rouge (problème).

    Recalculé en continu à partir de l'état réel des entités. Seul le défaut
    « la température ne monte pas » est verrouillé : il reste rouge jusqu'au
    réarmement de la pièce (bouton). L'attribut "raisons" liste les causes
    de l'état orange ou rouge.
    """

    _attr_icon = "mdi:shield-check"
    _attr_has_entity_name = True
    _attr_name = "Securite chauffage"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""
        self._entry = entry
        self._area_slug = area_slug
        self._remove_periodic_listener = None
        self._remove_state_listener = None

        self._attr_unique_id = f"{entry.entry_id}_securite"
        self.entity_id = f"sensor.securite_{area_slug}"
        self._attr_suggested_object_id = f"securite_{area_slug}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

        self._attr_native_value = SECURITY_STATE_OFF
        self._attr_extra_state_attributes = {"raisons": [], "en_chauffe": False}

    async def async_added_to_hass(self) -> None:
        """Start security monitoring for this room."""
        await super().async_added_to_hass()

        # Défaut de montée en température déclaré, restauré ou réarmé.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ROOM_FAULT.format(self._entry.entry_id),
                self._handle_fault_change,
            )
        )

        entites_a_surveiller = get_room_critical_entities(self.hass, self._entry)

        # Recalcul immédiat dès qu'une entité critique de la pièce change
        # d'état, plutôt que d'attendre le sondage périodique ci-dessous.
        self._remove_state_listener = async_track_state_change_event(
            self.hass,
            entites_a_surveiller,
            self._handle_entity_change,
        )

        # Recalcul automatique toutes les 5 secondes (filet de sécurité)
        self._remove_periodic_listener = async_track_time_interval(
            self.hass,
            self._async_periodic_security_check,
            SECURITY_CHECK_INTERVAL,
        )

        self._update_state()

    async def async_will_remove_from_hass(self) -> None:
        """Stop all security listeners."""
        if self._remove_periodic_listener is not None:
            self._remove_periodic_listener()
            self._remove_periodic_listener = None

        if self._remove_state_listener is not None:
            self._remove_state_listener()
            self._remove_state_listener = None

        await super().async_will_remove_from_hass()

    @callback
    def _update_state(self) -> None:
        """Calcule et met à jour la valeur interne sans écrire sur le bus."""
        state, reasons = compute_room_security(self.hass, self._entry)

        self._attr_native_value = state
        self._attr_extra_state_attributes = {
            "raisons": reasons,
            # Chauffe réelle de la pièce (lue par la carte pour son badge)
            "en_chauffe": is_room_really_heating(
                self.hass, {**self._entry.data, **self._entry.options}
            ),
        }

    @callback
    def _handle_fault_change(self) -> None:
        """Le suivi de montée en température a déclaré ou levé un défaut."""
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _async_periodic_security_check(self, _now=None) -> None:
        """Recalcul périodique de l'état de sécurité."""
        self._update_state()
        self.async_write_ha_state()

    @callback
    def _handle_entity_change(self, event: Event) -> None:
        """Réévaluation immédiate suite au changement d'une entité critique."""
        self._update_state()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Mise à jour asynchrone standard."""
        self._update_state()


# ==========================================================
# PRESET ACTUEL (par pièce) : publié par le scheduler
# ==========================================================


class ChauffageActuelSensor(RestoreEntity, SensorEntity):
    """Preset que la pièce DOIT avoir maintenant (publié par le scheduler).

    Les attributs (créneau, anticipation, cible) sont restaurés au
    redémarrage : le scheduler y relit la 'cible' pour reprendre une
    anticipation en cours.
    """

    _attr_icon = "mdi:calendar-clock"
    _attr_has_entity_name = True
    _attr_name = "Chauffage actuel"
    _attr_should_poll = False

    _RESTORED_ATTRIBUTES = ("creneau", "anticipation", "cible")

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""
        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = f"{entry.entry_id}_chauffage_actuel"
        self.entity_id = f"sensor.chauffage_actuel_{area_slug}"
        self._attr_suggested_object_id = f"chauffage_actuel_{area_slug}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Restore the last preset and listen to the scheduler."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            if last_state.state not in ("unknown", "unavailable"):
                self._attr_native_value = last_state.state

            self._attr_extra_state_attributes = {
                key: last_state.attributes[key]
                for key in self._RESTORED_ATTRIBUTES
                if key in last_state.attributes
            }

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CURRENT_PRESET.format(self._entry.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self, preset: str | None, attributes: dict) -> None:
        """New preset published by the scheduler."""
        self._attr_native_value = preset
        self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()


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


class AerationSensor(ChauffageSensorBase):
    """Recommandation d'aération (humidité absolue intérieure vs extérieure)."""

    _attr_icon = "mdi:window-open-variant"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""
        super().__init__(entry, area_slug, "aeration", "Aération")

    def update(self) -> None:
        """Update."""
        self._attr_native_value = calculate_aeration(self.hass, self._entry.data)


class HumiditeSensor(ChauffageSensorBase):
    """Niveau d'humidité intérieure."""

    _attr_icon = "mdi:water-percent"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""
        super().__init__(entry, area_slug, "humidite", "Humidité")

    def update(self) -> None:
        """Update."""
        self._attr_native_value = calculate_humidity_level(self.hass, self._entry.data)


# ==========================================================
# HEURES DE CHAUFFE PAR JOUR (pièce, et chaudière en gaz)
# ==========================================================

HOURS_TICK_INTERVAL = timedelta(seconds=30)
HOURS_MAX_GAP_SECONDS = 300


class _DailyHoursSensor(RestoreEntity, SensorEntity):
    """Temps de chauffe cumulé sur la journée, en heures (remis à zéro à minuit).

    Le temps s'accumule tant que `_is_active()` est vrai. Le cumul est mis à
    jour à chaque changement d'état des entités suivies et toutes les 30 s.
    Il est restauré au redémarrage de HA (même jour) ; le temps pendant
    lequel HA est arrêté n'est pas compté.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:timer-outline"

    _key = ""

    def __init__(self, entry: ConfigEntry, entity_id: str, device_info: dict) -> None:
        """Initialize."""

        self._entry = entry
        self._seconds = 0.0
        self._day = dt_util.now().date()
        self._last_tick = None
        self._was_active = False

        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self.entity_id = entity_id
        self._attr_suggested_object_id = entity_id.split(".", 1)[1]
        self._attr_device_info = device_info
        self._attr_last_reset = dt_util.as_utc(dt_util.start_of_local_day())

    def _is_active(self) -> bool:
        """À surcharger : la chauffe est-elle en cours ?"""

        raise NotImplementedError

    def _watched(self) -> list[str]:
        """À surcharger : entités dont un changement doit déclencher un calcul."""

        raise NotImplementedError

    @property
    def native_value(self) -> float:
        """Heures de chauffe depuis minuit."""

        return round(self._seconds / 3600, 2)

    @property
    def extra_state_attributes(self) -> dict:
        """Jour du cumul et secondes exactes (pour la restauration)."""

        return {"jour": self._day.isoformat(), "secondes": round(self._seconds)}

    async def async_added_to_hass(self) -> None:
        """Restore today's total and start tracking."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if (
            last_state is not None
            and last_state.attributes.get("jour") == self._day.isoformat()
        ):
            try:
                self._seconds = float(last_state.attributes.get("secondes"))
            except (TypeError, ValueError):
                try:
                    self._seconds = float(last_state.state) * 3600
                except (TypeError, ValueError):
                    self._seconds = 0.0

        self._was_active = self._is_active()
        self._last_tick = dt_util.utcnow()

        watched = [entity for entity in self._watched() if entity]

        if watched:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, watched, self._handle_change
                )
            )

        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._handle_tick, HOURS_TICK_INTERVAL
            )
        )

    @callback
    def _accumulate(self) -> None:
        """Crédite le temps écoulé depuis le dernier passage, puis relit l'état."""

        now = dt_util.utcnow()
        today = dt_util.now().date()
        start_of_day = dt_util.as_utc(dt_util.start_of_local_day())

        since = self._last_tick

        if today != self._day:
            # Minuit passé : nouveau jour, seule la part après minuit compte.
            self._day = today
            self._seconds = 0.0
            self._attr_last_reset = start_of_day

            if since is not None and since < start_of_day:
                since = start_of_day

        if self._was_active and since is not None:
            delta = (now - since).total_seconds()

            # Un grand écart (HA figé) n'est pas compté.
            if 0 < delta <= HOURS_MAX_GAP_SECONDS:
                self._seconds += delta

        self._was_active = self._is_active()
        self._last_tick = now

    @callback
    def _handle_change(self, event: Event) -> None:
        self._accumulate()
        self.async_write_ha_state()

    @callback
    def _handle_tick(self, _now=None) -> None:
        self._accumulate()
        self.async_write_ha_state()


class HeuresChauffeSensor(_DailyHoursSensor):
    """Heures de chauffe de la pièce sur la journée (chauffe réelle).

    Électrique : thermostat en chauffe. Gaz : vanne ouverte et chaudière en
    chauffe, y compris quand la pièce profite de la chaleur d'un groupe.
    """

    _key = "heures_chauffe"
    _attr_name = "Heures de chauffe"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        super().__init__(
            entry,
            f"sensor.heures_chauffe_{area_slug}",
            {
                "identifiers": {(DOMAIN, area_slug)},
                "name": area_slug.replace("_", " ").title(),
            },
        )

    def _config(self) -> dict:
        return {**self._entry.data, **self._entry.options}

    def _watched(self) -> list[str]:
        config = self._config()

        return [
            config.get(CONF_CLIMATE),
            config.get(CONF_HEATER_ENTITY),
            get_central_boiler_entity(self.hass),
        ]

    def _is_active(self) -> bool:
        return is_room_really_heating(self.hass, self._config())


class ChaudiereHeuresSensor(_DailyHoursSensor):
    """Heures pendant lesquelles la chaudière est en chauffe (chauffage gaz)."""

    _key = "heures_chauffe_chaudiere"
    _attr_name = "Heures de chauffe chaudière"
    _attr_icon = "mdi:fire"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""

        super().__init__(
            entry,
            "sensor.heures_chauffe_chaudiere",
            {"identifiers": {(DOMAIN, "central")}, "name": "Chauffage Intelligent"},
        )

    def _watched(self) -> list[str]:
        return [get_central_boiler_entity(self.hass)]

    def _is_active(self) -> bool:
        boiler_entity = get_central_boiler_entity(self.hass)

        return bool(boiler_entity) and is_boiler_heating(self.hass, boiler_entity)


# ==========================================================
# TEMPÉRATURES EXTÉRIEURES MINI / MAXI DU JOUR (réelles)
# ==========================================================


class _TempExtremeSensor(RestoreEntity, SensorEntity):
    """Mini ou maxi du jour d'un capteur de température extérieure (remis à zéro à minuit)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    _key = ""
    _use_max = False

    def __init__(self, entry: ConfigEntry, source_entity: str) -> None:
        """Initialize."""

        self._entry = entry
        self._source = source_entity
        self._day = dt_util.now().date()

        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self.entity_id = f"sensor.{self._key}"
        self._attr_suggested_object_id = self._key

        self._attr_native_value = None
        self._attr_extra_state_attributes = {"jour": self._day.isoformat()}

        self._attr_device_info = {
            "identifiers": {(DOMAIN, "central")},
            "name": "Chauffage Intelligent",
        }

    def _read_source(self) -> float | None:
        """Température courante du capteur extérieur, ou None si indisponible."""

        state = self.hass.states.get(self._source)

        if state is None or state.state in ("unknown", "unavailable"):
            return None

        unit = state.attributes.get("unit_of_measurement")

        if unit:
            self._attr_native_unit_of_measurement = unit

        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    @callback
    def _apply(self, value: float) -> None:
        """Intègre une mesure dans le mini/maxi du jour."""

        today = dt_util.now().date()

        if today != self._day:
            self._day = today
            self._attr_native_value = None

        value = round(value, 1)
        current = self._attr_native_value

        if (
            current is None
            or (self._use_max and value > current)
            or (not self._use_max and value < current)
        ):
            self._attr_native_value = value

        self._attr_extra_state_attributes = {"jour": self._day.isoformat()}

    async def async_added_to_hass(self) -> None:
        """Restore today's value, then follow the outdoor sensor."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if (
            last_state is not None
            and last_state.attributes.get("jour") == self._day.isoformat()
        ):
            try:
                self._attr_native_value = float(last_state.state)
            except (TypeError, ValueError):
                self._attr_native_value = None

        current = self._read_source()

        if current is not None:
            self._apply(current)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source], self._handle_source_change
            )
        )

        # Remise à zéro à minuit, même si le capteur ne change pas à ce moment.
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._handle_midnight, hour=0, minute=0, second=0
            )
        )

    @callback
    def _handle_source_change(self, event: Event) -> None:
        value = self._read_source()

        if value is None:
            return

        self._apply(value)
        self.async_write_ha_state()

    @callback
    def _handle_midnight(self, _now=None) -> None:
        self._day = dt_util.now().date()
        self._attr_native_value = None

        value = self._read_source()

        if value is not None:
            self._apply(value)

        self._attr_extra_state_attributes = {"jour": self._day.isoformat()}
        self.async_write_ha_state()


class TempExtMiniSensor(_TempExtremeSensor):
    """Température extérieure mini du jour (mesurée)."""

    _key = "temperature_ext_mini"
    _attr_name = "Température extérieure mini du jour"
    _attr_icon = "mdi:thermometer-low"
    _use_max = False


class TempExtMaxiSensor(_TempExtremeSensor):
    """Température extérieure maxi du jour (mesurée)."""

    _key = "temperature_ext_maxi"
    _attr_name = "Température extérieure maxi du jour"
    _attr_icon = "mdi:thermometer-high"
    _use_max = True


# ==========================================================
# TEMPÉRATURES MINI / MAXI PRÉVUES (entité météo)
# ==========================================================


class ForecastCoordinator(DataUpdateCoordinator):
    """Lit la prévision quotidienne de l'entité météo (mini/maxi d'aujourd'hui)."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, weather_entity: str
    ) -> None:
        """Initialize."""

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Chauffage Intelligent - prévisions",
            update_interval=timedelta(minutes=FORECAST_REFRESH_MINUTES),
        )

        self._weather_entity = weather_entity
        self._failed = False

    async def _async_update_data(self) -> dict[str, float | None]:
        """Récupère la prévision du jour (valeurs None si indisponible)."""

        empty: dict[str, float | None] = {"max": None, "min": None}

        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": self._weather_entity, "type": "daily"},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            if not self._failed:
                _LOGGER.warning(
                    "[Météo] Prévision indisponible pour %s : %s",
                    self._weather_entity,
                    err,
                )
                self._failed = True

            self.update_interval = timedelta(minutes=FORECAST_RETRY_MINUTES)
            return empty

        self._failed = False
        self.update_interval = timedelta(minutes=FORECAST_REFRESH_MINUTES)

        forecast = ((response or {}).get(self._weather_entity) or {}).get(
            "forecast"
        ) or []

        today = dt_util.now().date()

        for item in forecast:
            raw = item.get("datetime")
            parsed = dt_util.parse_datetime(raw) if isinstance(raw, str) else None

            # Date lue telle qu'écrite par le fournisseur (sans conversion de
            # fuseau) : un minuit UTC ne doit pas devenir la veille.
            if parsed is None or parsed.date() != today:
                continue

            return {
                "max": _to_float(item.get("temperature")),
                "min": _to_float(item.get("templow")),
            }

        return empty


def _to_float(value) -> float | None:
    """Convertit une valeur de prévision en float, ou None."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _ForecastExtremeSensor(CoordinatorEntity[ForecastCoordinator], SensorEntity):
    """Mini ou maxi prévu pour aujourd'hui."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_suggested_display_precision = 1

    _key = ""
    _field = ""

    def __init__(self, entry: ConfigEntry, coordinator: ForecastCoordinator) -> None:
        """Initialize."""

        super().__init__(coordinator)

        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self.entity_id = f"sensor.{self._key}"
        self._attr_suggested_object_id = self._key

        self._attr_device_info = {
            "identifiers": {(DOMAIN, "central")},
            "name": "Chauffage Intelligent",
        }

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data

        return data.get(self._field) if data else None

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.units.temperature_unit


class TempPrevueMiniSensor(_ForecastExtremeSensor):
    """Température mini prévue aujourd'hui."""

    _key = "temperature_prevue_mini"
    _field = "min"
    _attr_name = "Température prévue mini du jour"
    _attr_icon = "mdi:thermometer-low"


class TempPrevueMaxiSensor(_ForecastExtremeSensor):
    """Température maxi prévue aujourd'hui."""

    _key = "temperature_prevue_maxi"
    _field = "max"
    _attr_name = "Température prévue maxi du jour"
    _attr_icon = "mdi:thermometer-high"


# ==========================================================
# CONFIG CENTRALE : capteurs fixes + capteurs optionnels
# ==========================================================

_CENTRAL_OPTIONAL_KEYS = (
    ChaudiereHeuresSensor._key,
    TempExtMiniSensor._key,
    TempExtMaxiSensor._key,
    TempPrevueMiniSensor._key,
    TempPrevueMaxiSensor._key,
)


async def _async_setup_central(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée les compteurs de pièces, puis les capteurs optionnels selon la config.

    - heures de chauffe de la chaudière : chauffage gaz avec chaudière configurée ;
    - mini/maxi extérieurs réels : capteur extérieur configuré ;
    - mini/maxi prévus : entité météo configurée.

    Les capteurs optionnels qui ne sont plus configurés sont retirés du
    registre des entités (l'entrée centrale est rechargée à chaque
    changement de ces réglages).
    """

    config = {**entry.data, **entry.options}

    entities: list[SensorEntity] = [
        PiecesEnChauffeSensor(entry),
        PiecesFroidesSensor(entry),
        PiecesChaudesSensor(entry),
    ]
    created: set[str] = set()

    is_gas = config.get(CONF_HEATING_TYPE, HEATING_TYPE_GAS) == HEATING_TYPE_GAS

    if is_gas and config.get(CONF_BOILER_ENTITY):
        entities.append(ChaudiereHeuresSensor(entry))
        created.add(ChaudiereHeuresSensor._key)

    outdoor_sensor = config.get(CONF_TEMP_EXT_CENTRAL)

    if outdoor_sensor:
        entities.append(TempExtMiniSensor(entry, outdoor_sensor))
        entities.append(TempExtMaxiSensor(entry, outdoor_sensor))
        created.update({TempExtMiniSensor._key, TempExtMaxiSensor._key})

    weather_entity = config.get(CONF_WEATHER_ENTITY)
    coordinator: ForecastCoordinator | None = None

    if weather_entity:
        coordinator = ForecastCoordinator(hass, entry, weather_entity)
        entities.append(TempPrevueMiniSensor(entry, coordinator))
        entities.append(TempPrevueMaxiSensor(entry, coordinator))
        created.update({TempPrevueMiniSensor._key, TempPrevueMaxiSensor._key})

    registry = er.async_get(hass)

    for key in _CENTRAL_OPTIONAL_KEYS:
        if key in created:
            continue

        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_{key}"
        )

        if entity_id:
            registry.async_remove(entity_id)

    async_add_entities(entities)

    if coordinator is not None:

        async def _refresh_forecast(_hass_or_now) -> None:
            await coordinator.async_refresh()

        # Première lecture une fois HA démarré (l'entité météo est alors
        # chargée), puis juste après minuit pour passer à la prévision du jour.
        entry.async_on_unload(async_at_started(hass, _refresh_forecast))
        entry.async_on_unload(
            async_track_time_change(
                hass, _refresh_forecast, hour=0, minute=0, second=10
            )
        )
