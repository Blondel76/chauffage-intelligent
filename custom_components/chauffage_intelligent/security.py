"""Security rules and status computation for Chauffage Intelligent."""

from __future__ import annotations

import logging
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

_LOGGER = logging.getLogger(__name__)


def _get_room_entries(hass: HomeAssistant):
    """Récupère toutes les config entries correspondant à des pièces."""
    all_entries = hass.config_entries.async_entries(DOMAIN)
    room_entries = []

    for entry in all_entries:
        config = {**entry.data, **entry.options}
        if config.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM:
            room_entries.append(entry)

    if not room_entries:
        _LOGGER.warning("[Sécurité] Aucune pièce trouvée dans les config entries !")

    return room_entries


def _all_critical_entities_available(hass: HomeAssistant) -> bool:
    """Vérifie que toutes les entités critiques sont disponibles et actives."""
    rooms = _get_room_entries(hass)

    for entry in rooms:
        config = {**entry.data, **entry.options}

        for key in (
            CONF_CLIMATE,
            CONF_TEMP_INT,
            CONF_TEMP_EXT,
            CONF_HEATER_ENTITY,
            CONF_BOILER_ENTITY,
            CONF_DOOR_SENSOR,
        ):
            entity_id = config.get(key)

            if not entity_id:
                continue

            state = hass.states.get(entity_id)

            if state is None:
                _LOGGER.warning("[Sécurité] Entité introuvable dans HA : %s", entity_id)
                return False

            if state.state in ("unknown", "unavailable"):
                _LOGGER.warning(
                    "[Sécurité] Entité indisponible : %s (état : %s)",
                    entity_id,
                    state.state,
                )
                return False

            if key == CONF_CLIMATE and state.state == "off":
                _LOGGER.warning("[Sécurité] Thermostat éteint détecté : %s", entity_id)
                return False

    return True


def compute_security_state(
    hass: HomeAssistant,
    master_switch_on: bool,
    current_state: str = SECURITY_STATE_OK,
    rearm_pressed: bool = False,
) -> str:
    """Calculer l'état global de sécurité du chauffage avec auto-maintien."""
    if not master_switch_on:
        return SECURITY_STATE_OFF

    if not _all_critical_entities_available(hass):
        return SECURITY_STATE_CRITICAL

    if current_state == SECURITY_STATE_CRITICAL and not rearm_pressed:
        return SECURITY_STATE_CRITICAL

    return SECURITY_STATE_OK
