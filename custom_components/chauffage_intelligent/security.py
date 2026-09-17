"""Security rules and status computation for Chauffage Intelligent.

L'état de sécurité est désormais calculé par pièce, sans mémoire :
il reflète toujours l'état réel courant (pas de "réarmement" nécessaire).
"""

from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
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
    ENTRY_TYPE_CENTRAL,
    SECURITY_STATE_CRITICAL,
    SECURITY_STATE_OFF,
    SECURITY_STATE_OK,
)

_LOGGER = logging.getLogger(__name__)


def _get_central_boiler_entity(hass: HomeAssistant) -> str | None:
    """Récupère l'entité chaudière déclarée dans la config centrale (si configurée)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        config = {**entry.data, **entry.options}
        if config.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return config.get(CONF_BOILER_ENTITY)

    return None


def _central_boiler_ok(hass: HomeAssistant) -> bool:
    """Vérifie que la chaudière centrale (si configurée) est disponible."""
    boiler_entity = _get_central_boiler_entity(hass)

    if not boiler_entity:
        return True

    state = hass.states.get(boiler_entity)

    if state is None:
        _LOGGER.warning(
            "[Sécurité] Entité chaudière introuvable dans HA : %s", boiler_entity
        )
        return False

    if state.state in ("unknown", "unavailable"):
        _LOGGER.warning(
            "[Sécurité] Chaudière indisponible : %s (état : %s)",
            boiler_entity,
            state.state,
        )
        return False

    return True


def _room_critical_entities_ok(hass: HomeAssistant, config: dict) -> bool:
    """Vérifie température int/ext, vanne/interrupteur et capteur de porte de la pièce."""
    for key in (
        CONF_TEMP_INT,
        CONF_TEMP_EXT,
        CONF_HEATER_ENTITY,
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

        # Une vanne pilotée en climate (chauffage gaz) n'est jamais mise en
        # hvac_mode "off" par l'intégration elle-même (seule sa température
        # cible 29/7°C est modifiée) : la voir passer à "off" signifie
        # qu'elle a été coupée manuellement.
        if (
            key == CONF_HEATER_ENTITY
            and entity_id.startswith("climate.")
            and state.state == "off"
        ):
            _LOGGER.warning("[Sécurité] Vanne coupée manuellement : %s", entity_id)
            return False

    return True


def get_room_critical_entities(hass: HomeAssistant, room_entry: ConfigEntry) -> list[str]:
    """Retourne les entités à surveiller pour la sécurité d'une pièce (thermostat, capteurs, vanne, porte, chaudière)."""
    config = {**room_entry.data, **room_entry.options}
    entities: set[str] = set()

    for key in (
        CONF_CLIMATE,
        CONF_TEMP_INT,
        CONF_TEMP_EXT,
        CONF_HEATER_ENTITY,
        CONF_DOOR_SENSOR,
    ):
        entity_id = config.get(key)

        if entity_id:
            entities.add(entity_id)

    boiler_entity = _get_central_boiler_entity(hass)

    if boiler_entity:
        entities.add(boiler_entity)

    return list(entities)


def compute_room_security_state(hass: HomeAssistant, room_entry: ConfigEntry) -> str:
    """Calcule l'état de sécurité courant d'une pièce (gris/vert/rouge), sans mémoire."""
    config = {**room_entry.data, **room_entry.options}
    climate_entity = config.get(CONF_CLIMATE)
    climate_state = hass.states.get(climate_entity) if climate_entity else None

    if climate_state is None or climate_state.state in ("unknown", "unavailable"):
        _LOGGER.warning(
            "[Sécurité] Thermostat introuvable ou indisponible : %s", climate_entity
        )
        return SECURITY_STATE_CRITICAL

    if climate_state.state == "off":
        return SECURITY_STATE_OFF

    if not _room_critical_entities_ok(hass, config):
        return SECURITY_STATE_CRITICAL

    if not _central_boiler_ok(hass):
        return SECURITY_STATE_CRITICAL

    return SECURITY_STATE_OK
