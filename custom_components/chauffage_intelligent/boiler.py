"""Boiler on/off control, aggregated across all rooms."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

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

_LOGGER = logging.getLogger(__name__)

_LOCK_KEY = "_boiler_lock"


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


def _boiler_already_in_state(boiler_state, boiler_entity: str, should_run: bool) -> bool:
    """True si la chaudière est déjà dans l'état voulu (aucune commande à envoyer).

    Chaudière en climate : « en marche » = tout mode sauf « off » (heat, auto...),
    pour ne pas renvoyer « heat » en boucle si elle est en « auto ».
    """

    if boiler_entity.startswith("climate."):
        is_on = boiler_state.state != "off"
    else:
        is_on = boiler_state.state == "on"

    return is_on == should_run


async def update_boiler_state(hass: HomeAssistant) -> None:
    """Turn the boiler on if any room is genuinely calling for heat with its valve/switch open.

    La commande n'est envoyée que si l'état de la chaudière diffère de
    l'état voulu. Le verrou évite que plusieurs pièces évaluées en même
    temps envoient chacune la même commande.
    """

    boiler_entity = _get_central_boiler_entity(hass)

    if not boiler_entity:
        return

    lock = hass.data.setdefault(DOMAIN, {}).setdefault(_LOCK_KEY, asyncio.Lock())

    async with lock:
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

        boiler_state = hass.states.get(boiler_entity)

        # Chaudière introuvable ou indisponible : aucune commande possible
        # (la sécurité des pièces signale déjà ce cas).
        if boiler_state is None or boiler_state.state in ("unknown", "unavailable"):
            return

        if _boiler_already_in_state(boiler_state, boiler_entity, should_run):
            return

        try:
            if boiler_entity.startswith("climate."):
                await hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {
                        "entity_id": boiler_entity,
                        "hvac_mode": "heat" if should_run else "off",
                    },
                    blocking=True,
                )
            else:
                await hass.services.async_call(
                    "switch",
                    "turn_on" if should_run else "turn_off",
                    {"entity_id": boiler_entity},
                    blocking=True,
                )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "[Chaudière] Impossible de %s %s : %s",
                "démarrer" if should_run else "arrêter",
                boiler_entity,
                err,
            )
