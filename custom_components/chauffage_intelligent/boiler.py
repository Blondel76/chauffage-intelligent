"""Boiler on/off control, aggregated across all rooms."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import (
    CONF_BOILER_ENTITY,
    CONF_CLIMATE,
    CONF_HEATER_ENTITY,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_ROOM,
    VALVE_OPEN_TEMP,
)


def _get_central_boiler_entity(hass: HomeAssistant) -> str | None:
    """Return the configured boiler switch entity, if any."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry.data.get(CONF_BOILER_ENTITY)

    return None


def _is_heater_open(hass: HomeAssistant, heater_entity: str) -> bool:
    """Return True if a room's heater entity is in its 'open/on' state."""

    state = hass.states.get(heater_entity)

    if state is None:
        return False

    if heater_entity.startswith("climate."):
        temperature = state.attributes.get("temperature")
        return temperature == VALVE_OPEN_TEMP

    return state.state == "on"


async def update_boiler_state(hass: HomeAssistant) -> None:
    """Turn the boiler on if any room is genuinely calling for heat with its valve/switch open."""

    boiler_entity = _get_central_boiler_entity(hass)

    if not boiler_entity:
        return

    should_run = False

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) != ENTRY_TYPE_ROOM:
            continue

        climate_entity = entry.data.get(CONF_CLIMATE)
        heater_entity = entry.data.get(CONF_HEATER_ENTITY)

        if not climate_entity or not heater_entity:
            continue

        climate_state = hass.states.get(climate_entity)

        if climate_state is None:
            continue

        is_heating = climate_state.attributes.get("hvac_action") == "heating"

        if is_heating and _is_heater_open(hass, heater_entity):
            should_run = True
            break

    if boiler_entity.startswith("climate."):
        await hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": boiler_entity, "hvac_mode": "heat" if should_run else "off"},
        )
    else:
        await hass.services.async_call(
            "switch",
            "turn_on" if should_run else "turn_off",
            {"entity_id": boiler_entity},
        )
