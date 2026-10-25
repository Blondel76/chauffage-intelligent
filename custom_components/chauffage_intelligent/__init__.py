"""Initialisation du package de l'intégration Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .boost import RoomBoost
from .const import (
    CONF_AREA,
    CONF_BOILER_ENTITY,
    CONF_CLIMATE,
    CONF_HEATING_TYPE,
    CONF_MODE_SELECTOR,
    CONF_TEMP_EXT_CENTRAL,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_GROUP,
    ENTRY_TYPE_ROOM,
    HEATING_TYPE_GAS,
    climate_entity_id,
    slugify_area,
)
from .heating_monitor import RoomHeatingMonitor
from .resolver import PlanningResolver
from .scheduler import ChauffageScheduler

CENTRAL_PLATFORMS = ["switch", "sensor", "binary_sensor"]
ROOM_PLATFORMS = ["climate", "sensor", "number", "switch", "binary_sensor", "button"]


def _preload_platforms() -> None:
    """Import platform modules ahead of time (blocking, run in executor)."""

    from . import binary_sensor, button, climate, number, sensor, switch  # noqa: F401


def _get_central_mode_selector(hass: HomeAssistant) -> str | None:
    """Find the mode selector entity from the central config entry, if any."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry.data.get(CONF_MODE_SELECTOR)

    return None


def _migrate_room_climate(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Fait pointer la pièce vers son thermostat intégré.

    Les pièces créées avec un thermostat externe sont migrées : leur
    CONF_CLIMATE devient l'entité climate créée par l'intégration. Tout le
    reste du code (scheduler, sécurité, calculs, carte) la lit donc sans
    changement.
    """

    climate_id = climate_entity_id(slugify_area(entry.data[CONF_AREA]))

    if entry.data.get(CONF_CLIMATE) == climate_id:
        return

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CLIMATE: climate_id}
    )


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Réapplique le créneau en cours quand le planning d'une pièce est modifié.

    Si l'option « mode boost » a été activée ou désactivée, la pièce est
    rechargée : l'entité boost est alors créée ou retirée du registre.
    """

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    if not data:
        return

    boost = data.get("boost")

    if boost is not None and boost.enabled != boost.enabled_at_setup:
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))
        return

    scheduler = data.get("scheduler")

    if scheduler is not None:
        await scheduler.async_reconcile()


def _central_signature(entry: ConfigEntry) -> tuple:
    """Réglages centraux qui décident des entités créées (capteurs optionnels)."""

    config = {**entry.data, **entry.options}

    return (
        config.get(CONF_HEATING_TYPE, HEATING_TYPE_GAS),
        config.get(CONF_BOILER_ENTITY),
        config.get(CONF_TEMP_EXT_CENTRAL),
        config.get(CONF_WEATHER_ENTITY),
    )


async def _async_central_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """La config centrale a changé : recharger les pièces.

    Le resolver et le scheduler de chaque pièce mémorisent l'entité
    sélecteur de mode à leur création ; un rechargement leur fait relire
    la nouvelle valeur.

    Si le type de chauffage, la chaudière, le capteur extérieur ou l'entité
    météo ont changé, l'entrée centrale est aussi rechargée : les capteurs
    optionnels (heures de la chaudière, températures du jour) sont alors
    créés ou retirés.
    """

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}

    if data.get("signature") != _central_signature(entry):
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    for room_entry in hass.config_entries.async_entries(DOMAIN):
        if room_entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM:
            hass.async_create_task(
                hass.config_entries.async_reload(room_entry.entry_id)
            )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Chauffage Intelligent from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    entry_type = entry.data.get(ENTRY_TYPE)

    if entry_type == ENTRY_TYPE_GROUP:
        hass.data[DOMAIN][entry.entry_id] = {}
        return True

    await hass.async_add_executor_job(_preload_platforms)

    if entry_type == ENTRY_TYPE_CENTRAL:
        await hass.config_entries.async_forward_entry_setups(
            entry, CENTRAL_PLATFORMS
        )
        hass.data[DOMAIN][entry.entry_id] = {"signature": _central_signature(entry)}

        # Modification de la config centrale (ex. sélecteur de mode)
        # -> les pièces doivent être rechargées.
        entry.async_on_unload(entry.add_update_listener(_async_central_updated))

        return True

    _migrate_room_climate(hass, entry)

    mode_selector = _get_central_mode_selector(hass)
    resolver = PlanningResolver(hass, entry, mode_selector)

    # Le suivi de montée en température et le boost doivent exister AVANT
    # les plateformes : l'entité « Défaut chauffe » restaure un défaut non
    # réarmé dans le suivi, l'entité « Boost » restaure un boost en cours,
    # et le capteur de sécurité consulte le suivi.
    monitor = RoomHeatingMonitor(hass, entry)
    boost = RoomBoost(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = {"monitor": monitor, "boost": boost}

    await hass.config_entries.async_forward_entry_setups(
        entry, ROOM_PLATFORMS
    )

    scheduler = ChauffageScheduler(hass, entry, mode_selector)

    # Le resolver et le scheduler doivent être dans hass.data AVANT
    # scheduler.start(), car async_reconcile() y lit le resolver.
    hass.data[DOMAIN][entry.entry_id] = {
        "resolver": resolver,
        "scheduler": scheduler,
        "monitor": monitor,
        "boost": boost,
    }

    scheduler.start()

    # Sauvegarde d'un planning (options flow) -> applique le créneau en cours.
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload Chauffage Intelligent."""

    entry_type = entry.data.get(ENTRY_TYPE)

    if entry_type == ENTRY_TYPE_GROUP:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True

    if entry_type == ENTRY_TYPE_CENTRAL:
        unloaded = await hass.config_entries.async_unload_platforms(
            entry, CENTRAL_PLATFORMS
        )

        if unloaded:
            hass.data[DOMAIN].pop(entry.entry_id, None)

        return unloaded

    unloaded = await hass.config_entries.async_unload_platforms(
        entry, ROOM_PLATFORMS
    )

    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)

        scheduler = data.get("scheduler") if data is not None else None

        if scheduler is not None:
            scheduler.stop()

    return unloaded
