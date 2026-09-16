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
    room_entries = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        # On fusionne data et options pour être sûr de trouver le type d'entrée
        config = {**entry.data, **entry.options}
        if config.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM:
            room_entries.append(entry)
    return room_entries


def _all_critical_entities_available(hass: HomeAssistant) -> bool:
    """Vérifie que toutes les entités critiques sont disponibles et actives."""
    for entry in _get_room_entries(hass):
        # Fusion des données initiales et des options modifiées
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

            # Entité absente, non disponible ou inconnue
            if state is None or state.state in ("unknown", "unavailable"):
                return False

            # Thermostat éteint
            if key == CONF_CLIMATE and state.state == "off":
                return False

    return True


def compute_security_state(
    hass: HomeAssistant,
    master_switch_on: bool,
    current_state: str = SECURITY_STATE_OK,
    rearm_pressed: bool = False,
) -> str:
    """Calculer l'état global de sécurité du chauffage avec auto-maintien."""
    # 1. Si le commutateur maître est éteint
    if not master_switch_on:
        return SECURITY_STATE_OFF

    # 2. Si une entité est indisponible ou un thermostat est off
    if not _all_critical_entities_available(hass):
        return SECURITY_STATE_CRITICAL

    # 3. Verrouillage : si on était en rouge et sans réarmement, on reste en rouge
    if current_state == SECURITY_STATE_CRITICAL and not rearm_pressed:
        return SECURITY_STATE_CRITICAL

    # 4. Tout est conforme
    return SECURITY_STATE_OK
