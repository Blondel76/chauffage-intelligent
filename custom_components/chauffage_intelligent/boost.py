"""Mode boost par pièce pour Chauffage Intelligent.

Le boost applique pendant une durée limitée un preset fixe, quel que soit
le mode de la maison. Il ne force pas le chauffage : fenêtre ouverte,
chauffage général coupé ou sécurité rouge continuent de bloquer la vanne.

L'état (heure de fin) n'est conservé qu'en mémoire ; l'entité switch de la
pièce le restaure au redémarrage de HA.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BOOST_DURATION,
    CONF_BOOST_ENABLED,
    CONF_BOOST_PRESET,
    DEFAULT_BOOST_DURATION,
    DEFAULT_BOOST_PRESET,
    SIGNAL_ROOM_BOOST,
)


class RoomBoost:
    """Suivi du boost d'une pièce."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""

        self.hass = hass
        self.entry = entry
        self._end: datetime | None = None

        # Valeur de l'option au moment du chargement : si elle change,
        # la pièce est rechargée (création/retrait de l'entité).
        self.enabled_at_setup: bool = self.enabled

    def _config(self) -> dict:
        """Config courante de la pièce (réglages modifiables)."""

        return {**self.entry.data, **self.entry.options}

    # ------------------------------------------------------------
    # Réglages
    # ------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True si le mode boost est activé pour cette pièce."""

        return bool(self._config().get(CONF_BOOST_ENABLED, False))

    @property
    def preset(self) -> str:
        """Preset appliqué pendant le boost."""

        raw = self._config().get(CONF_BOOST_PRESET)

        if isinstance(raw, str) and raw.strip():
            return raw.strip()

        return DEFAULT_BOOST_PRESET

    @property
    def duration(self) -> int:
        """Durée du boost, en minutes."""

        try:
            value = float(
                self._config().get(CONF_BOOST_DURATION, DEFAULT_BOOST_DURATION)
            )
        except (ValueError, TypeError):
            return DEFAULT_BOOST_DURATION

        return round(value) if value > 0 else DEFAULT_BOOST_DURATION

    # ------------------------------------------------------------
    # État
    # ------------------------------------------------------------

    @property
    def active(self) -> bool:
        """True si le boost est en cours."""

        return self.enabled and self._end is not None

    @property
    def end(self) -> datetime | None:
        """Heure de fin (UTC), ou None."""

        return self._end if self.active else None

    @property
    def remaining_minutes(self) -> int:
        """Minutes restantes (arrondi au supérieur), 0 si inactif."""

        if not self.active:
            return 0

        seconds = (self._end - dt_util.utcnow()).total_seconds()

        return max(0, math.ceil(seconds / 60))

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------

    def start(self) -> None:
        """Lance (ou relance) le boost."""

        if not self.enabled:
            return

        self._end = dt_util.utcnow() + timedelta(minutes=self.duration)
        self._notify()

    def cancel(self) -> None:
        """Annule le boost."""

        self._end = None
        self._notify()

    def restore(self, end: datetime) -> None:
        """Rétablit un boost en cours avant un redémarrage."""

        if not self.enabled or end <= dt_util.utcnow():
            return

        self._end = end
        self._notify()

    @callback
    def evaluate(self) -> None:
        """Vérifie l'expiration (appelé chaque minute par le scheduler).

        Tant que le boost est actif, l'entité est aussi notifiée pour
        rafraîchir les minutes restantes.
        """

        if self._end is None:
            return

        if dt_util.utcnow() >= self._end:
            self.cancel()
            return

        self._notify()

    def _notify(self) -> None:
        """Prévient l'entité de la pièce."""

        async_dispatcher_send(
            self.hass, SIGNAL_ROOM_BOOST.format(self.entry.entry_id)
        )
