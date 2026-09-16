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


def _entity_is_off(state) -> bool:
    """Return True when a configured heating entity is switched off."""
    if state is None:
        return False

    # For a climate, only an explicit OFF mode is considered switched off.
    # IDLE means that the thermostat is active but is not heating at this
    # moment, so it must not trigger the security alarm.
    if state.domain == "climate":
        if state.state == "off":
            return True

        hvac_mode = state.attributes.get("hvac_mode")
        return isinstance(hvac_mode, str) and hvac_mode.lower() == "off"

    # A valve or an electric heater configured as a switch is off when its
    # switch state is explicitly off.
    if state.domain == "switch":
        return state.state == "off"

    return False


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
        # Fusionne data et options au cas où la configuration soit dans options
        config_data = {**entry.data, **entry.options}

        for key in (
            CONF_CLIMATE,
            CONF_TEMP_INT,
            CONF_TEMP_EXT,
            CONF_HEATER_ENTITY,
            CONF_BOILER_ENTITY,
            CONF_DOOR_SENSOR,
        ):
            entity_id = config_data.get(key)

            if not entity_id:
                continue

            state = hass.states.get(entity_id)

            # Entité non trouvée ou indisponible
            if state is None or state.state in ("unknown", "unavailable"):
                return False

            # Thermostat explicitement off, vanne off ou interrupteur
            # électrique off : la sécurité devient critique.
            if _entity_is_off(state):
                return False

    return True


def compute_security_state(
    hass: HomeAssistant,
    master_switch_on: bool,
    current_state: str = SECURITY_STATE_OK,
    rearm_pressed: bool = False,
) -> str:
    """Calculer l'état global de sécurité du chauffage avec réarmement manuel."""
    if not master_switch_on:
        return SECURITY_STATE_OFF

    if not _all_critical_entities_available(hass):
        return SECURITY_STATE_CRITICAL

    if current_state == SECURITY_STATE_CRITICAL and not rearm_pressed:
        return SECURITY_STATE_CRITICAL

    return SECURITY_STATE_OK
