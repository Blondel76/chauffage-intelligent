"""Intégration Chauffage Intelligent."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_setup(
    hass: HomeAssistant,
    config: dict,
) -> bool:
    """Set up the Chauffage Intelligent integration."""
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Chauffage Intelligent from a config entry."""

    await hass.config_entries.async_forward_entry_setups(
        entry,
        ["sensor"],
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload Chauffage Intelligent."""

    return await hass.config_entries.async_unload_platforms(
        entry,
        ["sensor"],
    )
