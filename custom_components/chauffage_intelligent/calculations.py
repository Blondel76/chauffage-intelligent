"""Calculations for Chauffage Intelligent."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant

from .const import (
    CONF_CLIMATE,
    CONF_PLANNING,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    COEFFICIENT_MIN,
    COEFFICIENT_MAX,
)


def get_float(
    hass: HomeAssistant,
    entity_id: str | None,
    default: float = 0.0,
) -> float:
    """Return a numeric state."""

    if not entity_id:
        return default

    state = hass.states.get(entity_id)

    if state is None:
        return default

    try:
        return float(state.state)
    except (ValueError, TypeError):
        return default


def get_state(
    hass: HomeAssistant,
    entity_id: str | None,
    default: str = "unknown",
) -> str:
    """Return an entity state."""

    if not entity_id:
        return default

    state = hass.states.get(entity_id)

    if state is None:
        return default

    return state.state


def _clamp_coefficient(value: float) -> float:
    """Clamp a coefficient to the allowed range."""

    return min(max(value, COEFFICIENT_MIN), COEFFICIENT_MAX)


def _normalized_hour(raw_hour: str) -> str | None:
    """Normalize 'HHhMM' or 'H:MM' into zero-padded 'HH:MM' for safe comparison."""

    heure = raw_hour.replace("h", ":")
    parts = heure.split(":")

    if len(parts) != 2:
        return None

    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        return None

    return f"{hh:02d}:{mm:02d}"


# ==========================================================
# TEMPS DE CHAUFFE
# ==========================================================


def calculate_heating_time(
    hass: HomeAssistant,
    config: dict,
    coefficient: float,
) -> int:
    """Calculate the estimated heating time."""

    temp = get_float(hass, config.get(CONF_TEMP_INT), 0)

    climate = hass.states.get(config.get(CONF_CLIMATE))

    if climate is None:
        return 0

    consigne = climate.attributes.get("temperature")

    try:
        consigne = float(consigne)
    except (ValueError, TypeError):
        consigne = 0

    delta = consigne - temp

    coefficient = _clamp_coefficient(float(coefficient))

    temp_ext = get_float(hass, config.get(CONF_TEMP_EXT), 10)

    facteur_ext = 1 + ((temp - temp_ext) / 50)
    facteur_ext = min(max(facteur_ext, 0.7), 1.5)

    if delta > 0.3:
        return round(delta * coefficient * facteur_ext)

    return 0


# ==========================================================
# PLANNING
# ==========================================================


def get_next_schedule(
    hass: HomeAssistant,
    config: dict,
) -> str:
    """Return the next heating schedule."""

    planning = get_state(hass, config.get(CONF_PLANNING))

    if planning in {"unknown", "unavailable", "none", ""}:
        return "unknown"

    maintenant = datetime.now().strftime("%H:%M")

    for item in planning.split(","):

        if "|" not in item:
            continue

        h, m = item.split("|", 1)
        h = h.strip()
        m = m.strip()

        heure = _normalized_hour(h)

        if heure is None:
            continue

        if heure > maintenant:
            return f"{h}|{m}"

    # Aucun créneau restant aujourd'hui :
    # on reprend le premier créneau du planning.
    return planning.split(",")[0].strip()


def get_previous_schedule(
    hass: HomeAssistant,
    config: dict,
) -> str:
    """Return the previous heating schedule."""

    planning = get_state(hass, config.get(CONF_PLANNING))

    if planning in {"unknown", "unavailable", "none", ""}:
        return "unknown"

    maintenant = datetime.now().strftime("%H:%M")

    resultat = None

    for item in planning.split(","):

        if "|" not in item:
            continue

        h, m = item.split("|", 1)
        h = h.strip()
        m = m.strip()

        heure = _normalized_hour(h)

        if heure is None:
            continue

        if heure <= maintenant:
            resultat = f"{h}|{m}"

    if resultat is not None:
        return resultat

    # Aucun créneau déjà passé aujourd'hui :
    # on prend le dernier créneau du planning.
    return planning.split(",")[-1].strip()


# ==========================================================
# HEURE ANTICIPÉE
# ==========================================================


def calculate_anticipated_time(
    hass: HomeAssistant,
    config: dict,
    coefficient: float,
) -> str:
    """Calculate the anticipated heating start time."""

    planning = get_next_schedule(hass, config)

    if planning == "unknown":
        return "unknown"

    if "|" not in planning:
        return planning

    cible = planning.split("|")[0].strip()
    cible_ok = cible.replace("h", ":")[:5]

    try:
        hh, mm = map(int, cible_ok.split(":"))
    except (ValueError, TypeError):
        return cible_ok

    besoin = calculate_heating_time(hass, config, coefficient)

    if besoin <= 0 or besoin >= 180:
        return cible_ok

    maintenant = datetime.now()

    cible_date = maintenant.replace(
        hour=hh,
        minute=mm,
        second=0,
        microsecond=0,
    )

    # Si le créneau est déjà passé,
    # il s'agit du prochain jour.
    if cible_date < maintenant:
        cible_date += timedelta(days=1)

    debut = cible_date - timedelta(minutes=besoin)

    if debut < maintenant:
        return maintenant.strftime("%H:%M")

    return debut.strftime("%H:%M")


# ==========================================================
# APPRENTISSAGE DU COEFFICIENT
# ==========================================================


def calculate_new_coefficient(
    hass: HomeAssistant,
    config: dict,
    ancien: float,
    derive_entity_id: str,
) -> float | None:
    """Calculate a new heating coefficient from the derivative."""

    climate = hass.states.get(config.get(CONF_CLIMATE))

    if climate is None:
        return None

    # On apprend uniquement lorsque le chauffage chauffe réellement.
    if climate.attributes.get("hvac_action") != "heating":
        return None

    derive = get_float(hass, derive_entity_id, 0)

    # Même seuil que ton automatisation actuelle.
    if derive <= 0.02:
        return None

    # nouveau = 1 / dérive
    nouveau = 1 / derive

    # Moyenne pondérée : 80 % ancien, 20 % nouvelle mesure.
    coefficient = ancien * 0.8 + nouveau * 0.2

    return round(_clamp_coefficient(coefficient), 1)
