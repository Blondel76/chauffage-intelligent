def compute_security_state(hass: HomeAssistant, master_switch_on: bool) -> str:
    """Calculer l'état global de sécurité du chauffage (gris/vert/orange/rouge).

    Règle 1 : gris si le commutateur maître est désactivé ; 
    vert s'il est allumé et les enties de chaque pièce sont disponibles ;
    rouge s'il est allumé mais il manque quelque chose."""

    if not master_switch_on:
        return SECURITY_STATE_OFF

    if _all_critical_entities_available(hass):
        return SECURITY_STATE_OK

    return SECURITY_STATE_CRITICAL


"""Security rules and status computation for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import (
    CONF_BOILER_ENTITY,
    CONF_CLIMATE,
    CONF_DOOR_SENSOR,
    CONF_HEATER_ENTITY,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_ROOM,
    SECURITY_STATE_CRITICAL,
    SECURITY_STATE_OFF,
    SECURITY_STATE_OK,
)


def _get_room_entries(hass: HomeAssistant):
    """Récupère toutes les config entries correspondant à des pièces."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM
    ]


def _all_critical_entities_available(hass: HomeAssistant) -> bool:
    """Vérifie que les entités critiques sont disponibles et non éteintes."""
    for entry in _get_room_entries(hass):
        for key in (
            CONF_CLIMATE,
            CONF_TEMP_INT,
            CONF_TEMP_EXT,
            CONF_HEATER_ENTITY,
            CONF_BOILER_ENTITY,
            CONF_DOOR_SENSOR,
        ):
            entity_id = entry.data.get(key)

            if not entity_id:
                continue

            state = hass.states.get(entity_id)

            # Entité inexistante ou indisponible
            if state is None or state.state in ("unknown", "unavailable"):
                return False

            # Thermostat éteint manuellement
            if key == CONF_CLIMATE and state.state == "off":
                return False

    return True


def compute_security_state(hass: HomeAssistant, master_switch_on: bool) -> str:
    """Calculer l'état global de sécurité du chauffage (gris/vert/rouge)."""
    if not master_switch_on:
        return SECURITY_STATE_OFF

    if _all_critical_entities_available(hass):
        return SECURITY_STATE_OK

    return SECURITY_STATE_CRITICAL
