"""Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_MODE_SELECTOR, DOMAIN, ENTRY_TYPE, ENTRY_TYPE_CENTRAL
from .resolver import PlanningResolver
from .scheduler import ChauffageScheduler


def _preload_platforms() -> None:
    """Import platform modules ahead of time (blocking, run in executor)."""

    from . import number, sensor, switch  # noqa: F401


def _get_central_mode_selector(hass: HomeAssistant) -> str | None:
    """Find the mode selector entity from the central config entry, if any."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry.data.get(CONF_MODE_SELECTOR)

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Chauffage Intelligent from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
        hass.data[DOMAIN][entry.entry_id] = {}
        return True

    mode_selector = _get_central_mode_selector(hass)
    resolver = PlanningResolver(hass, entry, mode_selector)

    await hass.async_add_executor_job(_preload_platforms)

    await hass.config_entries.async_forward_entry_setups(
        entry, ["sensor", "number", "switch"]
    )

    scheduler = ChauffageScheduler(hass, entry)
    scheduler.start()

    hass.data[DOMAIN][entry.entry_id] = {
        "resolver": resolver,
        "scheduler": scheduler,
    }

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload Chauffage Intelligent."""

    if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True

    unloaded = await hass.config_entries.async_unload_platforms(
        entry, ["sensor", "number", "switch"]
    )

    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)

        if data is not None:
            data["scheduler"].stop()

    return unloaded
