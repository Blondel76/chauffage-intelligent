"""Calculations for Chauffage Intelligent."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant

from .const import (
    AERATION_SEUIL_POSSIBLE,
    AERATION_SEUIL_RECOMMANDEE,
    CONF_CLIMATE,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    COEFFICIENT_MIN,
    COEFFICIENT_MAX,
    HUMIDITE_SEUIL_ELEVEE,
    HUMIDITE_SEUIL_EXCESSIVE,
    HUMIDITE_SEUIL_TRES_ELEVEE,
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
# PLANNING (prend directement la chaîne déjà résolue)
# ==========================================================


def get_next_schedule(planning: str) -> str:
    """Return the next heating schedule slot from a resolved planning string."""

    if not planning or planning in {"unknown", "unavailable", "none"}:
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

    return planning.split(",")[0].strip()


def get_previous_schedule(planning: str) -> str:
    """Return the previous heating schedule slot from a resolved planning string."""

    if not planning or planning in {"unknown", "unavailable", "none"}:
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

    return planning.split(",")[-1].strip()


# ==========================================================
# HEURE ANTICIPÉE
# ==========================================================


def calculate_anticipated_time(
    hass: HomeAssistant,
    config: dict,
    planning: str,
    coefficient: float,
) -> str:
    """Calculate the anticipated heating start time."""

    next_slot = get_next_schedule(planning)

    if next_slot == "unknown":
        return "unknown"

    if "|" not in next_slot:
        return next_slot

    cible = next_slot.split("|")[0].strip()
    cible_ok = cible.replace("h", ":")[:5]

    try:
        hh, mm = map(int, cible_ok.split(":"))
    except (ValueError, TypeError):
        return cible_ok

    besoin = calculate_heating_time(hass, config, coefficient)

    if besoin <= 0 or besoin >= 180:
        return cible_ok

    maintenant = datetime.now()

    cible_date = maintenant.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if cible_date < maintenant:
        cible_date += timedelta(days=1)

    debut = cible_date - timedelta(minutes=besoin)

    if debut < maintenant:
        return maintenant.strftime("%H:%M")

    return debut.strftime("%H:%M")


# ==========================================================
# APPRENTISSAGE DU COEFFICIENT (inchangé)
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

    if climate.attributes.get("hvac_action") != "heating":
        return None

    derive = get_float(hass, derive_entity_id, 0)

    if derive <= 0.02:
        return None

    nouveau = 1 / derive
    coefficient = ancien * 0.8 + nouveau * 0.2

    return round(_clamp_coefficient(coefficient), 1)


# ==========================================================
# HUMIDITÉ / AÉRATION
# ==========================================================


def _humidite_associee(temp_entity_id: str | None) -> str | None:
    """Déduit l'entité humidité du même appareil qu'un capteur de température.

    Suppose que le capteur de température s'appelle
    'sensor.xxx_temperature' et que le capteur d'humidité du même
    appareil s'appelle 'sensor.xxx_humidity'.
    """

    if not temp_entity_id or not temp_entity_id.endswith("_temperature"):
        return None

    return temp_entity_id[: -len("_temperature")] + "_humidity"


def _humidite_absolue(temp: float, humidite: float) -> float:
    """Humidité absolue en g/m³ (formule de Magnus)."""

    es = 6.112 * math.exp((17.62 * temp) / (243.12 + temp))
    ea = es * humidite / 100

    return 216.7 * ea / (temp + 273.15)


def _entite_disponible(hass: HomeAssistant, entity_id: str | None) -> bool:
    """Vérifie qu'une entité existe et n'est pas unknown/unavailable."""

    if not entity_id:
        return False

    state = hass.states.get(entity_id)

    return state is not None and state.state not in ("unknown", "unavailable")


def calculate_aeration(hass: HomeAssistant, config: dict) -> str:
    """Recommandation d'aération à partir de l'humidité absolue int/ext."""

    temp_int = config.get(CONF_TEMP_INT)
    temp_ext = config.get(CONF_TEMP_EXT)
    hum_int = _humidite_associee(temp_int)
    hum_ext = _humidite_associee(temp_ext)

    entites = (temp_int, temp_ext, hum_int, hum_ext)

    if not all(entites) or not all(_entite_disponible(hass, e) for e in entites):
        return "unknown"

    ti = get_float(hass, temp_int)
    te = get_float(hass, temp_ext)
    rhi = get_float(hass, hum_int)
    rhe = get_float(hass, hum_ext)

    difference = _humidite_absolue(ti, rhi) - _humidite_absolue(te, rhe)

    if difference > AERATION_SEUIL_RECOMMANDEE:
        return "Aération recommandée"

    if difference > AERATION_SEUIL_POSSIBLE:
        return "Aération possible"

    if difference > -AERATION_SEUIL_POSSIBLE:
        return "Peu utile"

    return "Aération déconseillée"


def calculate_humidity_level(hass: HomeAssistant, config: dict) -> str:
    """Niveau qualitatif de l'humidité intérieure."""

    hum_int = _humidite_associee(config.get(CONF_TEMP_INT))

    if not _entite_disponible(hass, hum_int):
        return "unknown"

    h = get_float(hass, hum_int)

    if h < HUMIDITE_SEUIL_ELEVEE:
        return "Normal"

    if h < HUMIDITE_SEUIL_TRES_ELEVEE:
        return "Élevée"

    if h < HUMIDITE_SEUIL_EXCESSIVE:
        return "Très élevée"

    return "Excessive"
