"""Security rules and status computation for Chauffage Intelligent.

L'état de sécurité est désormais calculé par pièce, sans mémoire :
il reflète toujours l'état réel courant (pas de "réarmement" nécessaire).

Niveaux : gris (chauffage éteint) / vert (ok) / orange (pièce trop froide
ou trop chaude) / rouge (panne : le chauffage de la pièce est bloqué).

Seul le défaut « la température ne monte pas » (cf. heating_monitor.py) a
une mémoire : il reste rouge jusqu'au réarmement manuel de la pièce. Toutes
les autres causes sont recalculées en continu.
"""

from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .calculations import is_room_cold, is_room_hot
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
    SECURITY_STATE_WARNING,
)

_LOGGER = logging.getLogger(__name__)


def _get_central_boiler_entity(hass: HomeAssistant) -> str | None:
    """Récupère l'entité chaudière déclarée dans la config centrale (si configurée)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        config = {**entry.data, **entry.options}
        if config.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return config.get(CONF_BOILER_ENTITY)

    return None


def _central_boiler_problem(hass: HomeAssistant) -> str | None:
    """Retourne le problème de la chaudière centrale (si configurée), ou None."""
    boiler_entity = _get_central_boiler_entity(hass)

    if not boiler_entity:
        return None

    state = hass.states.get(boiler_entity)

    if state is None:
        _LOGGER.warning(
            "[Sécurité] Entité chaudière introuvable dans HA : %s", boiler_entity
        )
        return f"Chaudière introuvable : {boiler_entity}"

    if state.state in ("unknown", "unavailable"):
        _LOGGER.warning(
            "[Sécurité] Chaudière indisponible : %s (état : %s)",
            boiler_entity,
            state.state,
        )
        return f"Chaudière indisponible : {boiler_entity}"

    return None


def _room_critical_problems(
    hass: HomeAssistant, config: dict, heating_should_be_active: bool
) -> list[str]:
    """Liste les problèmes de température int/ext, vanne/interrupteur et capteur de porte.

    La disponibilité (introuvable/unknown/unavailable) est vérifiée en tout
    temps, été comme hiver, pour détecter une panne avant même le premier
    besoin de chauffe. La vanne "coupée manuellement" (hvac_mode off alors
    qu'elle devrait chauffer) n'est en revanche un problème que si le
    chauffage est censé être actif : sinon, la couper (via l'interrupteur
    général ou le climate de la pièce) est un comportement normal, pas une
    panne.
    """
    problems: list[str] = []

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
            problems.append(f"Entité introuvable : {entity_id}")
            continue

        if state.state in ("unknown", "unavailable"):
            _LOGGER.warning(
                "[Sécurité] Entité indisponible : %s (état : %s)",
                entity_id,
                state.state,
            )
            problems.append(f"Entité indisponible : {entity_id}")
            continue

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
            problems.append(f"Vanne coupée manuellement : {entity_id}")

    return problems


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


def _get_room_monitor(hass: HomeAssistant, room_entry: ConfigEntry):
    """Retourne le suivi de montée en température de la pièce, s'il existe."""
    return hass.data.get(DOMAIN, {}).get(room_entry.entry_id, {}).get("monitor")


def compute_room_security(
    hass: HomeAssistant, room_entry: ConfigEntry
) -> tuple[str, list[str]]:
    """Calcule l'état de sécurité courant d'une pièce et ses raisons.

    Retourne (état gris/vert/orange/rouge, liste des raisons). Aucune
    mémoire, sauf le défaut verrouillé de montée en température, qui reste
    rouge jusqu'au réarmement de la pièce.

    La disponibilité des capteurs/vanne/chaudière est vérifiée en tout
    temps (été comme hiver), pas seulement quand le chauffage tourne, pour
    ne pas découvrir une panne seulement au premier démarrage hivernal.

    Orange : chauffage censé être actif et pièce trop froide (température
    <= consigne - écart froid) ou trop chaude (température > consigne +
    écart chaud, thermostat en chauffe, ou chaudière en chauffe avec la
    vanne de la pièce ouverte).
    """
    config = {**room_entry.data, **room_entry.options}
    climate_entity = config.get(CONF_CLIMATE)
    climate_state = hass.states.get(climate_entity) if climate_entity else None

    if climate_state is None or climate_state.state in ("unknown", "unavailable"):
        _LOGGER.warning(
            "[Sécurité] Thermostat introuvable ou indisponible : %s", climate_entity
        )
        return (
            SECURITY_STATE_CRITICAL,
            [f"Thermostat introuvable ou indisponible : {climate_entity}"],
        )

    switch_state = hass.states.get("switch.chauffage_general")
    master_on = switch_state is not None and switch_state.state == "on"
    room_on = climate_state.state != "off"
    heating_should_be_active = master_on and room_on

    problems = _room_critical_problems(hass, config, heating_should_be_active)

    boiler_problem = _central_boiler_problem(hass)

    if boiler_problem:
        problems.append(boiler_problem)

    monitor = _get_room_monitor(hass, room_entry)

    if monitor is not None and monitor.fault:
        problems.append(monitor.fault_label)

    if problems:
        return SECURITY_STATE_CRITICAL, problems

    if not heating_should_be_active:
        return SECURITY_STATE_OFF, []

    warnings: list[str] = []

    if is_room_cold(hass, config):
        warnings.append("Pièce froide")

    if is_room_hot(hass, config):
        warnings.append("Pièce chaude")

    if warnings:
        return SECURITY_STATE_WARNING, warnings

    return SECURITY_STATE_OK, []


def compute_room_security_state(hass: HomeAssistant, room_entry: ConfigEntry) -> str:
    """Calcule l'état de sécurité courant d'une pièce (gris/vert/orange/rouge)."""
    return compute_room_security(hass, room_entry)[0]
