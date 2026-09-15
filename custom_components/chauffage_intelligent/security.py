"""Security rules and status computation for Chauffage Intelligent."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

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
    """Return all room config entries."""

    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM
    ]


def _all_critical_entities_available(hass: HomeAssistant) -> bool:
    """Check that every room's climate and interior temperature sensor are available."""

    for entry in _get_room_entries(hass):
        for key in (CONF_CLIMATE, CONF_TEMP_INT):
            entity_id = entry.data.get(key)

            if not entity_id:
                continue

            state = hass.states.get(entity_id)

            if state is None or state.state in ("unknown", "unavailable"):
                return False

    return True


def compute_security_state(hass: HomeAssistant, master_switch_on: bool) -> str:
    """Compute the overall heating security state (gris/vert/orange/rouge).

    Rule 1 (this is the only rule for now): gris if the master switch is
    off; vert if it's on and every room's climate + interior temperature
    sensor are available; rouge if it's on but something is missing.
    More rules (orange, other components) will be added here later.
    """

    if not master_switch_on:
        return SECURITY_STATE_OFF

    if _all_critical_entities_available(hass):
        return SECURITY_STATE_OK

    return SECURITY_STATE_CRITICAL
