"""Intégration Chauffage Intelligent."""

from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Configurer l'intégration Chauffage Intelligent."""
    return True
