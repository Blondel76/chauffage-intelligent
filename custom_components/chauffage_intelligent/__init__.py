"""Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


def _preload_platforms() -> None:
    """Import platform modules ahead of time (blocking, run in executor)."""

    from . import number, sensor  # noqa: F401


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Chauffage Intelligent from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    # Précharge les modules de plateforme dans l'executor pour éviter
    # l'avertissement "blocking call to import_module" (Python 3.14+) :
    # async_forward_entry_setups importe sensor.py/number.py de façon
    # synchrone, ce qui est désormais détecté comme bloquant.
    await hass.async_add_executor_job(_preload_platforms)

    await hass.config_entries.async_forward_entry_setups(
        entry,
        ["sensor", "number"],
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload Chauffage Intelligent."""

    unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        ["sensor", "number"],
    )

    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unloaded
