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
    """Return True when a configured heating entity is explicitly off."""
    if state is None:
        return False

    # Pour un climate, seul l'état explicite "off" déclenche le rouge.
    # "idle" signifie que le thermostat est actif mais ne chauffe pas actuellement.
    if state.domain == "climate":
        return state.state == "off"

    # Pour une vanne ou un chauffage électrique configuré comme switch.
    if state.domain == "switch":
        return state.state == "off"

    return False


def _get_room_entries(hass: HomeAssistant):
    """Return all room configuration entries."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM
    ]


def _all_critical_entities_available(hass: HomeAssistant) -> bool:
    """Check that critical entities are available and switched on."""
    for entry in _get_room_entries(hass):
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

            if state is None:
                return False

            if state.state in ("unknown", "unavailable"):
                return False

            if _entity_is_off(state):
                return False

    return True


def compute_security_state(
    hass: HomeAssistant,
    master_switch_on: bool,
    current_state: str = SECURITY_STATE_OK,
    rearm_pressed: bool = False,
) -> str:
    """Calculate the global heating security state."""
    if not master_switch_on:
        return SECURITY_STATE_OFF

    # Une panne reste prioritaire, même pendant une demande de réarmement.
    if not _all_critical_entities_available(hass):
        return SECURITY_STATE_CRITICAL

    # Une alerte reste mémorisée jusqu'à une demande de réarmement.
    if current_state == SECURITY_STATE_CRITICAL and not rearm_pressed:
        return SECURITY_STATE_CRITICAL

    return SECURITY_STATE_OK
