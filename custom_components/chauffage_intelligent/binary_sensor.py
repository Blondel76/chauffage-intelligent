"""Binary sensors for Chauffage Intelligent.

Par pièce : pièce froide / pièce chaude / défaut chauffe (verrouillé).
Central : chaudière en chauffe alors qu'aucun thermostat ne demande de chauffe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .calculations import (
    get_central_boiler_entity,
    is_boiler_heating,
    is_room_cold,
    is_room_hot,
)
from .const import (
    BOILER_NO_DEMAND_GRACE_MINUTES,
    CONF_AREA,
    CONF_CLIMATE,
    CONF_HEATER_ENTITY,
    CONF_TEMP_INT,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_ROOM,
    SIGNAL_ROOM_FAULT,
    slugify_area,
)

_LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the room binary sensors, or the central boiler alert."""

    if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
        async_add_entities([ChaudiereSansDemandeBinarySensor(entry)])
        return

    area_slug = slugify_area(entry.data[CONF_AREA])

    async_add_entities(
        [
            PieceFroideBinarySensor(entry, area_slug),
            PieceChaudeBinarySensor(entry, area_slug),
            DefautChauffeBinarySensor(entry, area_slug),
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
            config.get(CONF_HEATER_ENTITY),
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


class DefautChauffeBinarySensor(RestoreEntity, BinarySensorEntity):
    """Défaut verrouillé « la température ne monte pas » (réarmement manuel).

    L'état vient du suivi de la pièce (heating_monitor.py). Il est restauré
    au redémarrage de HA : un défaut non réarmé reste actif.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Défaut chauffe"
    _attr_icon = "mdi:radiator-off"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = f"{entry.entry_id}_defaut_chauffe"
        self.entity_id = f"binary_sensor.defaut_chauffe_{area_slug}"
        self._attr_suggested_object_id = f"defaut_chauffe_{area_slug}"

        self._attr_is_on = False
        self._attr_extra_state_attributes = {}

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

    def _monitor(self):
        """Suivi de la pièce (créé par __init__ avant les plateformes)."""

        return self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {}).get(
            "monitor"
        )

    async def async_added_to_hass(self) -> None:
        """Restore a pending fault and follow the monitor."""

        await super().async_added_to_hass()

        monitor = self._monitor()
        last_state = await self.async_get_last_state()

        if monitor is not None and last_state is not None and last_state.state == "on":
            since = None
            raw_since = last_state.attributes.get("depuis")

            if raw_since:
                since = dt_util.parse_datetime(raw_since)

            monitor.restore_fault(last_state.attributes.get("raison"), since)

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ROOM_FAULT.format(self._entry.entry_id),
                self._handle_fault_change,
            )
        )

        self._sync()

    def _sync(self) -> None:
        """Copie l'état du suivi dans l'entité."""

        monitor = self._monitor()

        if monitor is None or not monitor.fault:
            self._attr_is_on = False
            self._attr_extra_state_attributes = {}
            return

        since: datetime | None = monitor.fault_since

        self._attr_is_on = True
        self._attr_extra_state_attributes = {
            "raison": monitor.fault_reason,
            "description": monitor.fault_label,
            "depuis": since.isoformat() if since else None,
        }

    @callback
    def _handle_fault_change(self) -> None:
        """Le suivi a déclaré ou levé un défaut."""

        self._sync()
        self.async_write_ha_state()


class ChaudiereSansDemandeBinarySensor(BinarySensorEntity):
    """Chaudière en chauffe alors qu'aucun thermostat ne demande de chauffe.

    Alerte centrale, sans blocage des pièces : fermer toutes les vannes
    pendant que la chaudière tourne serait pire. Le défaut n'est déclaré
    qu'après BOILER_NO_DEMAND_GRACE_MINUTES minutes de situation continue
    (post-circulation de la chaudière, retard de la régulation).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Chaudière en chauffe sans demande"
    _attr_icon = "mdi:fire-alert"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""

        self._entry = entry
        self._since: datetime | None = None

        self._attr_unique_id = f"{entry.entry_id}_chaudiere_sans_demande"
        self.entity_id = "binary_sensor.chaudiere_sans_demande"
        self._attr_suggested_object_id = "chaudiere_sans_demande"

        self._attr_is_on = False
        self._attr_extra_state_attributes = {}

        self._attr_device_info = {
            "identifiers": {(DOMAIN, "central")},
            "name": "Chauffage Intelligent",
        }

    def _any_room_heating(self) -> bool:
        """True si au moins un thermostat de pièce est en chauffe."""

        for room_entry in self.hass.config_entries.async_entries(DOMAIN):
            if room_entry.data.get(ENTRY_TYPE) != ENTRY_TYPE_ROOM:
                continue

            config = {**room_entry.data, **room_entry.options}
            state = self.hass.states.get(config.get(CONF_CLIMATE))

            if state is not None and state.attributes.get("hvac_action") == "heating":
                return True

        return False

    def _compute(self) -> bool:
        """Recalcule l'état (avec délai de grâce)."""

        boiler_entity = get_central_boiler_entity(self.hass)

        anomaly = (
            bool(boiler_entity)
            and is_boiler_heating(self.hass, boiler_entity)
            and not self._any_room_heating()
        )

        if not anomaly:
            self._since = None
            self._attr_extra_state_attributes = {}
            return False

        now = dt_util.utcnow()

        if self._since is None:
            self._since = now

        self._attr_extra_state_attributes = {"depuis": self._since.isoformat()}

        return now - self._since >= timedelta(minutes=BOILER_NO_DEMAND_GRACE_MINUTES)

    async def async_added_to_hass(self) -> None:
        """Listen to the boiler, plus a periodic safety net."""

        await super().async_added_to_hass()

        boiler_entity = get_central_boiler_entity(self.hass)

        if boiler_entity:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, [boiler_entity], self._handle_change
                )
            )

        # Filet de sécurité : thermostats qui s'arrêtent, chaudière modifiée
        # dans la config centrale, délai de grâce qui s'écoule.
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._handle_periodic, REFRESH_INTERVAL
            )
        )

        self._attr_is_on = self._compute()

    @callback
    def _refresh(self) -> None:
        """Recalculate and publish the state."""

        was_on = self._attr_is_on
        self._attr_is_on = self._compute()

        if self._attr_is_on and not was_on:
            _LOGGER.warning(
                "[Sécurité] La chaudière reste en chauffe alors qu'aucun "
                "thermostat ne demande de chauffe depuis plus de %s min",
                BOILER_NO_DEMAND_GRACE_MINUTES,
            )

        self.async_write_ha_state()

    @callback
    def _handle_change(self, event: Event) -> None:
        self._refresh()

    @callback
    def _handle_periodic(self, _now=None) -> None:
        self._refresh()
