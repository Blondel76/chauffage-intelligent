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
)
from .resolver import PlanningResolver
from .scheduler import ChauffageScheduler

PLATFORMS_CENTRAL = ["switch"]
PLATFORMS_ROOM = ["sensor", "number", "switch"]


def _preload_platforms() -> None:
    """Import platform modules ahead of time (blocking, run in executor)."""

    from . import number, sensor, switch  # noqa: F401


def _get_central_mode_selector(hass: HomeAssistant) -> str | None:
    """Find the mode selector entity from the central config entry, if any."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry.data.get(CONF_MODE_SELECTOR)

    return None


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Réévalue le planning quand la configuration d'une pièce est modifiée."""

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    if not data:
        return

    scheduler = data.get("scheduler")

    if scheduler is not None:
        await scheduler.async_reconcile()


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
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_CENTRAL)
        hass.data[DOMAIN][entry.entry_id] = {}
        return True

    mode_selector = _get_central_mode_selector(hass)

    resolver = PlanningResolver(hass, entry, mode_selector)
    scheduler = ChauffageScheduler(hass, entry, mode_selector)

    # Le resolver doit être disponible AVANT les plateformes (les capteurs
    # de planning le lisent) et le scheduler avant son démarrage.
    hass.data[DOMAIN][entry.entry_id] = {
        "resolver": resolver,
        "scheduler": scheduler,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_ROOM)

    scheduler.start()

    # Modification d'un planning (options flow) -> réévaluation immédiate.
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

    platforms = PLATFORMS_CENTRAL if entry_type == ENTRY_TYPE_CENTRAL else PLATFORMS_ROOM
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)

    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)

        if data and data.get("scheduler") is not None:
            data["scheduler"].stop()

    return unloaded
