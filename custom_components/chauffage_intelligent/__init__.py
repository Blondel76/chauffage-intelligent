"""Initialisation du package de l'intégration Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MODE_SELECTOR,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_GROUP,
    ENTRY_TYPE_ROOM,
)
from .resolver import PlanningResolver
from .scheduler import ChauffageScheduler

CENTRAL_PLATFORMS = ["switch", "sensor"]
ROOM_PLATFORMS = ["sensor", "number", "switch", "binary_sensor"]


def _preload_platforms() -> None:
    """Import platform modules ahead of time (blocking, run in executor)."""

    from . import binary_sensor, number, sensor, switch  # noqa: F401


def _get_central_mode_selector(hass: HomeAssistant) -> str | None:
    """Find the mode selector entity from the central config entry, if any."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry.data.get(CONF_MODE_SELECTOR)

    return None


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Réapplique le créneau en cours quand le planning d'une pièce est modifié."""

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    if not data:
        return

    scheduler = data.get("scheduler")

    if scheduler is not None:
        await scheduler.async_reconcile()


async def _async_central_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """La config centrale a changé : recharger les pièces.

    Le resolver et le scheduler de chaque pièce mémorisent l'entité
    sélecteur de mode à leur création ; un rechargement leur fait relire
    la nouvelle valeur.
    """

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
        hass.data[DOMAIN][entry.entry_id] = {}

        # Modification de la config centrale (ex. sélecteur de mode)
        # -> les pièces doivent être rechargées.
        entry.async_on_unload(entry.add_update_listener(_async_central_updated))

        return True

    mode_selector = _get_central_mode_selector(hass)
    resolver = PlanningResolver(hass, entry, mode_selector)

    await hass.config_entries.async_forward_entry_setups(
        entry, ROOM_PLATFORMS
    )

    scheduler = ChauffageScheduler(hass, entry, mode_selector)

    # Le resolver et le scheduler doivent être dans hass.data AVANT
    # scheduler.start(), car async_reconcile() y lit le resolver.
    hass.data[DOMAIN][entry.entry_id] = {
        "resolver": resolver,
        "scheduler": scheduler,
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

        if data is not None:
            data["scheduler"].stop()

    return unloaded
