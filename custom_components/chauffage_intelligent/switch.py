"""Switch entities for Chauffage Intelligent."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREA,
    CONF_BOILER_ENTITY,
    CONF_BOOST_ENABLED,
    CONF_CLIMATE,
    CONF_DOOR_SENSOR,
    CONF_HEATER_ENTITY,
    CONF_HEATING_TYPE,
    CONF_MODE_SELECTOR,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_ROOM,
    HEATING_TYPE_GAS,
    SIGNAL_ROOM_BOOST,
    slugify_area,
)


def _is_window_open(hass: HomeAssistant, room_entry: ConfigEntry) -> bool:
    """True si la fenêtre/porte de la pièce est ouverte (capteur ou interrupteur manuel)."""

    entity_id = room_entry.data.get(CONF_DOOR_SENSOR) or (
        f"switch.fenetre_ouverte_{slugify_area(room_entry.data[CONF_AREA])}"
    )

    state = hass.states.get(entity_id)

    return state is not None and state.state == "on"


def _remove_boost_entity(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Retire du registre l'entité boost d'une pièce où l'option est désactivée."""

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_boost"
    )

    if entity_id:
        registry.async_remove(entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the master heating switch (central), or the room switches (window override, boost)."""

    if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
        async_add_entities([ChauffageGeneralSwitch(entry)])
        return

    config = {**entry.data, **entry.options}

    area_name = entry.data[CONF_AREA]
    area_slug = slugify_area(area_name)

    entities: list[SwitchEntity] = []

    if not entry.data.get(CONF_DOOR_SENSOR):
        entities.append(WindowOverrideSwitch(entry, area_slug))

    if config.get(CONF_BOOST_ENABLED, False):
        entities.append(BoostSwitch(entry, area_slug))
    else:
        _remove_boost_entity(hass, entry)

    if entities:
        async_add_entities(entities)


class ChauffageGeneralSwitch(RestoreEntity, SwitchEntity):
    """Master switch: turns every room's climate heat mode on or off.

    Il publie aussi en attributs les réglages de la config centrale utiles
    à la carte : sélecteur de mode de la maison, chaudière et type de
    chauffage (la carte ne peut pas lire la config de l'intégration).
    """

    _attr_icon = "mdi:radiator"
    _attr_has_entity_name = True
    _attr_name = "Chauffage général"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize."""

        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_chauffage_general"
        self.entity_id = "switch.chauffage_general"
        self._attr_suggested_object_id = "chauffage_general"

        self._attr_is_on = True

        # Rattaché à l'appareil central (comme les compteurs de pièces).
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "central")},
            "name": "Chauffage Intelligent",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Mode de la maison, chaudière et type de chauffage (config centrale)."""

        config = {**self._entry.data, **self._entry.options}

        attributes: dict[str, Any] = {
            "heating_type": config.get(CONF_HEATING_TYPE, HEATING_TYPE_GAS),
        }

        mode_entity = config.get(CONF_MODE_SELECTOR)

        if mode_entity:
            attributes["mode_entity"] = mode_entity

        boiler_entity = config.get(CONF_BOILER_ENTITY)

        if boiler_entity:
            attributes["boiler_entity"] = boiler_entity

        return attributes

    async def async_added_to_hass(self) -> None:
        """Restore the previous state and follow the central config."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

        # Config centrale modifiée (mode, chaudière...) : rafraîchit les attributs.
        self.async_on_remove(self._entry.add_update_listener(self._handle_entry_update))

    async def _handle_entry_update(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """La config centrale a changé : republier les attributs."""

        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on: set every room's climate to heat mode."""

        self._attr_is_on = True
        self.async_write_ha_state()
        await self._apply_to_all_rooms("heat")

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off: set every room's climate to off."""

        self._attr_is_on = False
        self.async_write_ha_state()
        await self._apply_to_all_rooms("off")

    async def _apply_to_all_rooms(self, hvac_mode: str) -> None:
        """Apply the given hvac_mode to every room's climate entity (and its valve, if any).

        En remise en chauffe ("heat"), les pièces dont la fenêtre/porte est
        ouverte sont ignorées : elles repasseront en chauffe à la fermeture
        (cf. scheduler._handle_window_change).
        """

        for room_entry in self.hass.config_entries.async_entries(DOMAIN):
            if room_entry.data.get(ENTRY_TYPE) != ENTRY_TYPE_ROOM:
                continue

            climate_entity = room_entry.data.get(CONF_CLIMATE)

            if not climate_entity:
                continue

            if hvac_mode == "heat" and _is_window_open(self.hass, room_entry):
                continue

            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {"entity_id": climate_entity, "hvac_mode": hvac_mode},
            )

            # Si la vanne (chauffage gaz) a été coupée manuellement, la
            # remettre en cohérence : sinon set_temperature (29/7°C) via
            # le scheduler n'a aucun effet tant qu'elle reste en "off".
            heater_entity = room_entry.data.get(CONF_HEATER_ENTITY)

            heater_state = (
                self.hass.states.get(heater_entity) if heater_entity else None
            )

            if (
                heater_entity
                and heater_entity.startswith("climate.")
                and heater_state is not None
                and heater_state.state not in ("unknown", "unavailable")
            ):
                await self.hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {"entity_id": heater_entity, "hvac_mode": hvac_mode},
                )


class WindowOverrideSwitch(RestoreEntity, SwitchEntity):
    """Manual override used as the window/door state when no sensor is configured."""

    _attr_icon = "mdi:window-open-variant"
    _attr_has_entity_name = True
    _attr_name = "Fenêtre ouverte (manuel)"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = f"{entry.entry_id}_window_override"
        self.entity_id = f"switch.fenetre_ouverte_{area_slug}"
        self._attr_suggested_object_id = f"fenetre_ouverte_{area_slug}"

        self._attr_is_on = False

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

    async def async_added_to_hass(self) -> None:
        """Restore the previous state."""

        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is not None:
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on (simulate an open window)."""

        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off (simulate a closed window)."""

        self._attr_is_on = False
        self.async_write_ha_state()


class BoostSwitch(RestoreEntity, SwitchEntity):
    """Mode boost d'une pièce (créé seulement si l'option est activée).

    Allumer lance (ou relance) le boost, éteindre l'annule. L'état vient du
    suivi RoomBoost (boost.py) ; le scheduler lit le même suivi pour
    appliquer le preset du boost. Un boost en cours est restauré au
    redémarrage de HA.
    """

    _attr_icon = "mdi:rocket-launch"
    _attr_has_entity_name = True
    _attr_name = "Boost"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = f"{entry.entry_id}_boost"
        self.entity_id = f"switch.boost_{area_slug}"
        self._attr_suggested_object_id = f"boost_{area_slug}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

    def _data(self) -> dict:
        """Données de la pièce dans hass.data."""

        return self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})

    def _boost(self):
        """Suivi du boost de la pièce (créé par __init__ avant les plateformes)."""

        return self._data().get("boost")

    @property
    def is_on(self) -> bool:
        """True si le boost est en cours."""

        boost = self._boost()

        return boost is not None and boost.active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Preset, durée, heure de fin et minutes restantes (pour la carte)."""

        boost = self._boost()

        if boost is None:
            return {}

        attributes: dict[str, Any] = {
            "preset": boost.preset,
            "duree": boost.duration,
        }

        if boost.active:
            end = boost.end

            attributes["fin"] = end.isoformat() if end else None
            attributes["minutes_restantes"] = boost.remaining_minutes

        return attributes

    async def async_added_to_hass(self) -> None:
        """Restore a running boost and follow the boost tracker."""

        await super().async_added_to_hass()

        boost = self._boost()
        last_state = await self.async_get_last_state()

        if boost is not None and last_state is not None and last_state.state == "on":
            raw_end = last_state.attributes.get("fin")
            end = dt_util.parse_datetime(raw_end) if raw_end else None

            if end is not None:
                boost.restore(end)

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_ROOM_BOOST.format(self._entry.entry_id),
                self._handle_boost_change,
            )
        )

    @callback
    def _handle_boost_change(self) -> None:
        """Le boost a démarré, expiré, été annulé ou rafraîchi."""

        self.async_write_ha_state()

    async def _reconcile(self) -> None:
        """Applique tout de suite le preset attendu (boost ou planning)."""

        scheduler = self._data().get("scheduler")

        if scheduler is not None:
            await scheduler.async_reconcile()

    async def async_turn_on(self, **kwargs) -> None:
        """Lance (ou relance) le boost."""

        boost = self._boost()

        if boost is None:
            return

        boost.start()
        await self._reconcile()

    async def async_turn_off(self, **kwargs) -> None:
        """Annule le boost et revient au planning."""

        boost = self._boost()

        if boost is None:
            return

        boost.cancel()
        await self._reconcile()
