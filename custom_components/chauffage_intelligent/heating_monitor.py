"""Suivi « la température ne monte pas » pour Chauffage Intelligent.

Une pièce est « en chauffe réelle » quand son thermostat demande de la
chaleur, que sa vanne/son interrupteur est ouvert et que la chaudière (si
elle est configurée) est en chauffe. Dès que ces trois conditions sont
réunies, on relève l'heure, la température et le temps de chauffe estimé.
Si la condition cesse (demande terminée, vanne fermée, fenêtre ouverte,
consigne modifiée...), le suivi repart de zéro.

Un défaut est déclaré, puis VERROUILLÉ jusqu'au réarmement manuel de la
pièce (bouton), dans l'un des deux cas :

- "no_rise"  : le délai réglé est écoulé et le gain de température est
  inférieur au gain minimal réglé ;
- "too_long" : la chauffe dure plus de NO_RISE_ESTIMATE_FACTOR fois le temps
  de chauffe estimé au départ.

Le suivi (chrono, température de départ) n'est conservé qu'en mémoire : un
redémarrage de HA le remet à zéro. Le verrou, lui, est restauré par l'entité
« Défaut chauffe » de la pièce.
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .calculations import (
    _numeric_state,
    _room_target,
    calculate_heating_time,
    get_central_boiler_entity,
    is_boiler_heating,
    is_heater_open,
)
from .const import (
    COEFFICIENT_DEFAULT,
    CONF_AREA,
    CONF_CLIMATE,
    CONF_HEATER_ENTITY,
    CONF_NO_RISE_DELAY,
    CONF_NO_RISE_DELTA,
    CONF_TEMP_INT,
    DEFAULT_NO_RISE_DELAY,
    DEFAULT_NO_RISE_DELTA,
    FAULT_REASON_NO_RISE,
    FAULT_REASON_TOO_LONG,
    NO_RISE_ESTIMATE_FACTOR,
    SIGNAL_ROOM_FAULT,
    slugify_area,
)

_LOGGER = logging.getLogger(__name__)

FAULT_LABELS = {
    FAULT_REASON_NO_RISE: "La température ne monte pas (réarmement nécessaire)",
    FAULT_REASON_TOO_LONG: "Chauffe trop longue (réarmement nécessaire)",
}


def _float_setting(config: dict, key: str, default: float) -> float:
    """Lit un réglage numérique de la pièce, avec valeur par défaut."""

    try:
        value = float(config.get(key, default))
    except (ValueError, TypeError):
        return default

    return value if value > 0 else default


class RoomHeatingMonitor:
    """Suivi de la montée en température d'une pièce, avec défaut verrouillé."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""

        self.hass = hass
        self.entry = entry
        self._area_slug = slugify_area(entry.data[CONF_AREA])

        self._fault_reason: str | None = None
        self._fault_since: datetime | None = None

        self._reset_tracking()

    # ------------------------------------------------------------
    # État du défaut (verrou)
    # ------------------------------------------------------------

    @property
    def fault(self) -> bool:
        """True si un défaut est verrouillé (rouge jusqu'au réarmement)."""

        return self._fault_reason is not None

    @property
    def fault_reason(self) -> str | None:
        """Code du défaut verrouillé, ou None."""

        return self._fault_reason

    @property
    def fault_label(self) -> str | None:
        """Texte lisible du défaut verrouillé, ou None."""

        if self._fault_reason is None:
            return None

        return FAULT_LABELS.get(
            self._fault_reason, "Défaut de chauffe (réarmement nécessaire)"
        )

    @property
    def fault_since(self) -> datetime | None:
        """Date de déclaration du défaut, ou None."""

        return self._fault_since

    def restore_fault(self, reason: str | None, since: datetime | None = None) -> None:
        """Rétablit un défaut mémorisé avant un redémarrage (sans le re-déclarer)."""

        self._fault_reason = reason or FAULT_REASON_NO_RISE
        self._fault_since = since or dt_util.utcnow()
        self._reset_tracking()
        self._notify()

    def rearm(self) -> None:
        """Lève le verrou et repart d'un suivi vierge."""

        self._fault_reason = None
        self._fault_since = None
        self._reset_tracking()
        self._notify()

    def _declare_fault(self, reason: str, details: str) -> None:
        """Verrouille le défaut."""

        _LOGGER.warning(
            "[Sécurité] %s : %s — %s", self._area_slug, FAULT_LABELS[reason], details
        )

        self._fault_reason = reason
        self._fault_since = dt_util.utcnow()
        self._reset_tracking()
        self._notify()

    def _notify(self) -> None:
        """Prévient les entités de la pièce (capteur de sécurité, défaut)."""

        async_dispatcher_send(
            self.hass, SIGNAL_ROOM_FAULT.format(self.entry.entry_id)
        )

    # ------------------------------------------------------------
    # Suivi
    # ------------------------------------------------------------

    def _reset_tracking(self) -> None:
        """Efface le suivi en cours."""

        self._reference_time: datetime | None = None
        self._reference_temp: float | None = None
        self._reference_target: float | None = None
        self._estimate_minutes: int = 0
        self._rise_confirmed: bool = False

    def _config(self) -> dict:
        """Config courante de la pièce (réglages modifiables)."""

        return {**self.entry.data, **self.entry.options}

    def _is_really_heating(self, config: dict) -> bool:
        """Thermostat en demande, vanne ouverte et chaudière en chauffe."""

        climate = self.hass.states.get(config.get(CONF_CLIMATE))

        if climate is None or climate.attributes.get("hvac_action") != "heating":
            return False

        if not is_heater_open(self.hass, config.get(CONF_HEATER_ENTITY)):
            return False

        boiler_entity = get_central_boiler_entity(self.hass)

        if boiler_entity and not is_boiler_heating(self.hass, boiler_entity):
            return False

        return True

    def _read_coefficient(self) -> float:
        """Lit le coefficient de chauffe de la pièce."""

        state = self.hass.states.get(f"number.coefficient_{self._area_slug}")

        try:
            return float(state.state)
        except (AttributeError, ValueError, TypeError):
            return COEFFICIENT_DEFAULT

    @callback
    def evaluate(self) -> None:
        """Réévalue le suivi (appelé chaque minute par le scheduler)."""

        # Défaut déjà verrouillé : plus de suivi jusqu'au réarmement.
        if self.fault:
            self._reset_tracking()
            return

        config = self._config()

        if not self._is_really_heating(config):
            self._reset_tracking()
            return

        temp = _numeric_state(self.hass, config.get(CONF_TEMP_INT))
        target = _room_target(self.hass, config)

        # Capteur ou consigne indisponible : déjà traité par la sécurité
        # (rouge), on ne suit pas dans le vide.
        if temp is None or target is None:
            self._reset_tracking()
            return

        # La consigne a changé (nouveau créneau du planning...) : le temps
        # de chauffe estimé n'est plus valable, on repart de zéro.
        if self._reference_time is not None and target != self._reference_target:
            self._reset_tracking()

        now = dt_util.utcnow()

        if self._reference_time is None:
            self._reference_time = now
            self._reference_temp = temp
            self._reference_target = target
            self._estimate_minutes = calculate_heating_time(
                self.hass, config, self._read_coefficient()
            )
            return

        elapsed = (now - self._reference_time).total_seconds() / 60
        gain = temp - self._reference_temp

        delay = _float_setting(config, CONF_NO_RISE_DELAY, DEFAULT_NO_RISE_DELAY)
        delta = _float_setting(config, CONF_NO_RISE_DELTA, DEFAULT_NO_RISE_DELTA)

        if gain >= delta:
            self._rise_confirmed = True

        # 1. Pas de montée suffisante au bout du délai réglé.
        if not self._rise_confirmed and elapsed >= delay:
            self._declare_fault(
                FAULT_REASON_NO_RISE,
                f"{gain:+.1f} °C en {elapsed:.0f} min "
                f"(minimum attendu : {delta:.1f} °C en {delay:.0f} min)",
            )
            return

        # 2. Chauffe plus longue que le temps de chauffe estimé au départ.
        if self._estimate_minutes > 0:
            limit = max(delay, NO_RISE_ESTIMATE_FACTOR * self._estimate_minutes)

            if elapsed > limit:
                self._declare_fault(
                    FAULT_REASON_TOO_LONG,
                    f"{elapsed:.0f} min de chauffe pour une estimation de "
                    f"{self._estimate_minutes} min",
                )
