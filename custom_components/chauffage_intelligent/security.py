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


def _room_critical_entities_ok(
    hass: HomeAssistant, config: dict, heating_should_be_active: bool
) -> bool:
    """Vérifie température int/ext, vanne/interrupteur et capteur de porte de la pièce.

    La disponibilité (introuvable/unknown/unavailable) est vérifiée en tout
    temps, été comme hiver, pour détecter une panne avant même le premier
    besoin de chauffe. La vanne "coupée manuellement" (hvac_mode off alors
    qu'elle devrait chauffer) n'est en revanche un problème que si le
    chauffage est censé être actif : sinon, la couper (via l'interrupteur
    général ou le climate de la pièce) est un comportement normal, pas une
    panne.
    """
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

        # Une vanne pilotée en climate (chauffage gaz) n'est mise en
        # hvac_mode "off" par l'intégration elle-même QUE lorsque le
        # chauffage est globalement coupé (cf. switch.py). La voir passer
        # à "off" alors que le chauffage devrait être actif signifie donc
        # qu'elle a été coupée manuellement.
        if (
            heating_should_be_active
            and key == CONF_HEATER_ENTITY
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

    entities.add("switch.chauffage_general")

    return list(entities)


def compute_room_security_state(hass: HomeAssistant, room_entry: ConfigEntry) -> str:
    """Calcule l'état de sécurité courant d'une pièce (gris/vert/rouge), sans mémoire.

    La disponibilité des capteurs/vanne/chaudière est vérifiée en tout
    temps (été comme hiver), pas seulement quand le chauffage tourne, pour
    ne pas découvrir une panne seulement au premier démarrage hivernal.
    """
    config = {**room_entry.data, **room_entry.options}
    climate_entity = config.get(CONF_CLIMATE)
    climate_state = hass.states.get(climate_entity) if climate_entity else None

    if climate_state is None or climate_state.state in ("unknown", "unavailable"):
        _LOGGER.warning(
            "[Sécurité] Thermostat introuvable ou indisponible : %s", climate_entity
        )
        return SECURITY_STATE_CRITICAL

    switch_state = hass.states.get("switch.chauffage_general")
    master_on = switch_state is not None and switch_state.state == "on"
    room_on = climate_state.state != "off"
    heating_should_be_active = master_on and room_on

    if not _room_critical_entities_ok(hass, config, heating_should_be_active):
        return SECURITY_STATE_CRITICAL

    if not _central_boiler_ok(hass):
        return SECURITY_STATE_CRITICAL

    if not heating_should_be_active:
        return SECURITY_STATE_OFF

    return SECURITY_STATE_OK
