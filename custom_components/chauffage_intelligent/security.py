"""Security rules and status computation for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

"""récupères les constantes du fichier const.py que tu m'as montré précédemment"""
from .const import (
    CONF_CLIMATE,
    CONF_TEMP_INT,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_ROOM,
    SECURITY_STATE_CRITICAL,
    SECURITY_STATE_OFF,
    SECURITY_STATE_OK,
)


def _get_room_entries(hass: HomeAssistant):
    """Récupère toutes les config entries correspondant à des pièces"""

    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN) """demande à Home Assistant : Donne-moi toutes les entrées de configuration de mon intégration chauffage_intelligent"""
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM """ne conserve que celles dont c'est des pièces """
    ]


def _all_critical_entities_available(hass: HomeAssistant) -> bool:
    """Cette fonction répond à une question très simple : Est-ce que toutes les entités critiques de toutes les pièces sont disponibles ?"""

    for entry in _get_room_entries(hass):
        for key in (CONF_CLIMATE, CONF_TEMP_INT, CONF_TEMP_EXT, CONF_HEATER_ENTITY, CONF_BOILER_ENTITY, CONF_DOOR_SENSOR): """entitésqui doivent être vérifier"""
            entity_id = entry.data.get(key)

            if not entity_id:
                continue

            state = hass.states.get(entity_id)

            if state is None or state.state in ("unknown", "unavailable"):
                return False

    return True


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
