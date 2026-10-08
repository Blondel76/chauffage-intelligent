"""Scheduling, window safety, and heater activation for Chauffage Intelligent.

Principe : à chaque réévaluation (chaque minute + à chaque événement utile),
le scheduler calcule le preset que la pièce DOIT avoir maintenant, le publie
dans l'entité `sensor.chauffage_actuel_<pièce>`, puis aligne le thermostat
dessus s'il diffère. Le planning a toujours la main.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.start import async_at_started

from .boiler import update_boiler_state
from .calculations import (
    calculate_heating_time,
    get_float,
    get_next_schedule,
    get_previous_schedule,
)
from .const import (
    COEFFICIENT_DEFAULT,
    CONF_AREA,
    CONF_CLIMATE,
    CONF_DOOR_SENSOR,
    CONF_GROUP_AREAS,
    CONF_GROUP_THRESHOLD,
    CONF_HEATER_ENTITY,
    CONF_HEATING_TYPE,
    CONF_TEMP_INT,
    DEFAULT_GROUP_THRESHOLD,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_GROUP,
    ENTRY_TYPE_ROOM,
    HEATING_TYPE_ELECTRIC,
    HEATING_TYPE_GAS,
    SECURITY_STATE_CRITICAL,
    SIGNAL_CURRENT_PRESET,
    VALVE_CLOSED_TEMP,
    VALVE_OPEN_TEMP,
    slugify_area,
)
from .security import compute_room_security_state

_LOGGER = logging.getLogger(__name__)


def _preset_of(slot: str | None) -> str | None:
    """Return the preset name of a 'HH:MM|preset' slot, or None if invalid."""

    if not slot or "|" not in slot:
        return None

    return slot.split("|", 1)[1].strip() or None


def _get_central_heating_type(hass: HomeAssistant) -> str:
    """Return the house's heating type, defaulting to gas."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry.data.get(CONF_HEATING_TYPE, HEATING_TYPE_GAS)

    return HEATING_TYPE_GAS


def _find_room_entry_by_area(hass: HomeAssistant, area_id: str) -> ConfigEntry | None:
    """Find a room's config entry by its raw area id."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM and entry.data.get(CONF_AREA) == area_id:
            return entry

    return None


def _get_linked_areas(hass: HomeAssistant, area_id: str) -> tuple[list[str], float]:
    """Return (linked area ids, smallest matching threshold) for a given area."""

    linked: set[str] = set()
    threshold: float | None = None

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) != ENTRY_TYPE_GROUP:
            continue

        areas = entry.data.get(CONF_GROUP_AREAS, [])

        if area_id not in areas:
            continue

        linked.update(a for a in areas if a != area_id)

        group_threshold = entry.data.get(CONF_GROUP_THRESHOLD, DEFAULT_GROUP_THRESHOLD)

        if threshold is None or group_threshold < threshold:
            threshold = group_threshold

    return list(linked), threshold if threshold is not None else DEFAULT_GROUP_THRESHOLD


def _any_linked_room_heating(hass: HomeAssistant, linked_area_ids: list[str]) -> bool:
    """Check if any linked room's climate is currently heating."""

    for area_id in linked_area_ids:
        room_entry = _find_room_entry_by_area(hass, area_id)

        if room_entry is None:
            continue

        state = hass.states.get(room_entry.data.get(CONF_CLIMATE))

        if state is not None and state.attributes.get("hvac_action") == "heating":
            return True

    return False


class ChauffageScheduler:
    """Applies planning presets, window safety, and heater activation for one room."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        mode_selector_entity: str | None,
    ) -> None:
        """Initialize."""

        self.hass = hass
        self.entry = entry
        self.mode_selector_entity = mode_selector_entity

        self.area_id = entry.data[CONF_AREA]
        self.area_slug = slugify_area(self.area_id)
        self.climate_entity = entry.data.get(CONF_CLIMATE)
        self.heater_entity = entry.data.get(CONF_HEATER_ENTITY)
        self.door_entity = entry.data.get(CONF_DOOR_SENSOR)
        self.temp_int_entity = entry.data.get(CONF_TEMP_INT)

        self.window_switch_entity = f"switch.fenetre_ouverte_{self.area_slug}"
        self.current_entity = f"sensor.chauffage_actuel_{self.area_slug}"

        self._remove_listeners: list = []
        self._delayed_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

        # Créneau (date/heure) pour lequel l'anticipation a démarré. Une fois
        # posée, elle est conservée jusqu'à l'heure du planning : le temps de
        # chauffe, qui dépend de la consigne du thermostat, ne peut plus la
        # remettre en cause (évite les va-et-vient de preset).
        self._anticipated_target: datetime | None = None

        # Dernier preset en échec, pour ne logger l'erreur qu'une fois.
        self._failed_preset: str | None = None

    # ------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------

    def start(self) -> None:
        """Start listening for time, window, climate and mode changes."""

        self._restore_anticipation()

        self._remove_listeners.append(
            async_track_time_change(self.hass, self._handle_minute_tick, second=0)
        )

        window_entity = self.door_entity or self.window_switch_entity

        self._remove_listeners.append(
            async_track_state_change_event(
                self.hass, [window_entity], self._handle_window_change
            )
        )

        if self.climate_entity:
            self._remove_listeners.append(
                async_track_state_change_event(
                    self.hass, [self.climate_entity], self._handle_climate_change
                )
            )

        if self.mode_selector_entity:
            self._remove_listeners.append(
                async_track_state_change_event(
                    self.hass, [self.mode_selector_entity], self._handle_mode_change
                )
            )

        # Au démarrage : réévaluer une fois HA entièrement démarré
        # (thermostats chargés). Si HA tourne déjà (rechargement), c'est
        # immédiat.
        self._remove_listeners.append(
            async_at_started(self.hass, self._handle_ha_started)
        )

        self.hass.async_create_task(self._update_heater())

    def stop(self) -> None:
        """Stop all listeners."""

        for remove in self._remove_listeners:
            if remove is not None:
                remove()

        self._remove_listeners.clear()

        if self._delayed_task is not None:
            self._delayed_task.cancel()
            self._delayed_task = None

    def _restore_anticipation(self) -> None:
        """Retrouve une anticipation en cours (mémorisée dans l'entité) après redémarrage."""

        state = self.hass.states.get(self.current_entity)

        if state is None:
            return

        raw = state.attributes.get("cible")

        if not raw:
            return

        try:
            target = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return

        if target > datetime.now():
            self._anticipated_target = target

    @callback
    def _handle_ha_started(self, hass: HomeAssistant) -> None:
        """HA est entièrement démarré : réévaluer après un court délai."""

        self._schedule_delayed_reconcile()

    def _schedule_delayed_reconcile(self) -> None:
        """Planifie une réévaluation après un court délai (le temps que le thermostat se stabilise)."""

        if self._delayed_task is not None:
            self._delayed_task.cancel()

        self._delayed_task = self.hass.async_create_task(self._delayed_reconcile())

    async def _delayed_reconcile(self) -> None:
        """Attend puis réévalue."""

        await asyncio.sleep(5)
        await self.async_reconcile()

    # ------------------------------------------------------------
    # Planning : calcul du preset attendu et alignement du thermostat
    # ------------------------------------------------------------

    def _get_planning(self) -> str | None:
        """Return the active planning string, if the resolver is ready."""

        data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        resolver = data.get("resolver")

        if resolver is None:
            return None

        return resolver.get_active_planning()

    def _read_coefficient(self) -> float:
        """Read this room's coefficient number entity."""

        state = self.hass.states.get(f"number.coefficient_{self.area_slug}")

        try:
            return float(state.state)
        except (AttributeError, ValueError, TypeError):
            return COEFFICIENT_DEFAULT

    def _anticipation_minutes(self) -> int:
        """Return the estimated heating time in minutes (0 = no anticipation)."""

        besoin = calculate_heating_time(
            self.hass, self.entry.data, self._read_coefficient()
        )

        return besoin if 0 < besoin < 180 else 0

    @staticmethod
    def _slot_datetime(slot: str, now: datetime) -> datetime | None:
        """Return the next occurrence (date + time) of a 'HH:MM|preset' slot."""

        if "|" not in slot:
            return None

        raw = slot.split("|", 1)[0].strip().replace("h", ":")

        try:
            hh, mm = map(int, raw.split(":")[:2])
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError:
            return None

        if target <= now:
            target += timedelta(days=1)

        return target

    def _compute_expected_slot(self, planning: str) -> tuple[str, bool] | None:
        """Return (slot attendu, anticipation en cours) pour maintenant.

        - Si l'anticipation du prochain créneau a démarré (ou démarre
          maintenant), le créneau attendu est le prochain.
        - Sinon, c'est le créneau précédent (celui en vigueur).

        L'anticipation démarre (temps de chauffe + 1 min) avant l'heure du
        créneau. Le "+ 1 min" est nécessaire : à l'heure pile, le prochain
        créneau n'est plus "à venir" mais devient le créneau précédent.
        """

        now = datetime.now()
        next_slot = get_next_schedule(planning)
        target = self._slot_datetime(next_slot, now)

        if target is not None and _preset_of(next_slot) is not None:
            if self._anticipated_target != target:
                start = target - timedelta(minutes=self._anticipation_minutes() + 1)

                if now >= start:
                    self._anticipated_target = target

            if self._anticipated_target == target:
                return next_slot, True

        previous = get_previous_schedule(planning)

        if _preset_of(previous) is None:
            return None

        return previous, False

    def _publish(self, slot: str, anticipating: bool) -> None:
        """Met à jour l'entité 'chauffage actuel' de la pièce."""

        target = self._anticipated_target
        cible = (
            target.isoformat()
            if target is not None and target > datetime.now()
            else None
        )

        async_dispatcher_send(
            self.hass,
            SIGNAL_CURRENT_PRESET.format(self.entry.entry_id),
            _preset_of(slot),
            {"creneau": slot, "anticipation": anticipating, "cible": cible},
        )

    async def _align_thermostat(self, preset: str) -> None:
        """Applique le preset au thermostat s'il diffère (qu'il soit on ou off)."""

        if not self.climate_entity:
            return

        state = self.hass.states.get(self.climate_entity)

        if state is None or state.state in ("unknown", "unavailable"):
            return

        if state.attributes.get("preset_mode") == preset:
            self._failed_preset = None
            return

        try:
            await self.hass.services.async_call(
                "climate",
                "set_preset_mode",
                {"entity_id": self.climate_entity, "preset_mode": preset},
                blocking=True,
            )
        except HomeAssistantError as err:
            if self._failed_preset != preset:
                _LOGGER.warning(
                    "[Planning] Impossible d'appliquer le preset '%s' sur %s : %s",
                    preset,
                    self.climate_entity,
                    err,
                )
                self._failed_preset = preset
            return

        self._failed_preset = None

    async def async_reconcile(self) -> None:
        """Calcule le preset attendu, met à jour l'entité et aligne le thermostat."""

        async with self._lock:
            planning = self._get_planning()

            if not planning:
                return

            result = self._compute_expected_slot(planning)

            if result is None:
                return

            slot, anticipating = result

            self._publish(slot, anticipating)
            await self._align_thermostat(_preset_of(slot))

    # ------------------------------------------------------------
    # Déclencheurs
    # ------------------------------------------------------------

    async def _handle_minute_tick(self, _now) -> None:
        """Chaque minute : réévalue le planning et l'entité de chauffe.

        Réévaluer l'entité de chauffe chaque minute permet aussi de détecter
        les emprunts de chaleur entre pièces d'un groupe sans écouter chaque
        thermostat lié.
        """

        await self._update_heater()
        await self.async_reconcile()

    async def _handle_mode_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Le mode de la maison change : réévaluer immédiatement."""

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        # Ignore les simples changements d'attributs (ex. liste d'options modifiée)
        if old_state is not None and old_state.state == new_state.state:
            return

        await self.async_reconcile()

    async def _handle_climate_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """React immediately to this room's own climate changes."""

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        # Le thermostat redevient disponible (démarrage de HA, panne) :
        # réévaluer le planning.
        if (
            new_state is not None
            and new_state.state not in ("unknown", "unavailable")
            and (old_state is None or old_state.state in ("unknown", "unavailable"))
        ):
            self._schedule_delayed_reconcile()

        await self._update_heater()

    # ------------------------------------------------------------
    # Sécurité fenêtre/porte
    # ------------------------------------------------------------

    async def _handle_window_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Cut or restore heating when the window/door state changes.

        - Un capteur indisponible/inconnu ne change rien (ni coupure, ni
          remise en chauffe).
        - À la fermeture, la pièce n'est remise en chauffe que si le
          chauffage général est allumé.
        """

        if not self.climate_entity:
            return

        new_state = event.data.get("new_state")

        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        if new_state.state == "on":
            hvac_mode = "off"
        else:
            general = self.hass.states.get("switch.chauffage_general")

            if general is not None and general.state == "off":
                return

            hvac_mode = "heat"

        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": self.climate_entity, "hvac_mode": hvac_mode},
        )

    # ------------------------------------------------------------
    # Pilotage de l'entité de chauffe réelle (vanne ou interrupteur)
    # ------------------------------------------------------------

    async def _update_heater(self) -> None:
        """Decide whether the heater entity should be active, and apply it."""

        if not self.heater_entity or not self.climate_entity:
            return

        climate_state = self.hass.states.get(self.climate_entity)

        if climate_state is None:
            return

        hvac_action = climate_state.attributes.get("hvac_action")
        should_activate = hvac_action == "heating"

        if not should_activate and hvac_action == "idle":
            linked_areas, threshold = _get_linked_areas(self.hass, self.area_id)

            if linked_areas and _any_linked_room_heating(self.hass, linked_areas):
                target = climate_state.attributes.get("temperature")

                try:
                    target = float(target)
                except (TypeError, ValueError):
                    target = None

                if target is not None:
                    current = get_float(self.hass, self.temp_int_entity, target)

                    if current < target - threshold:
                        should_activate = True

        # Sécurité : même si le thermostat demande à chauffer, on
        # n'ouvre jamais la vanne/l'interrupteur tant que la pièce est
        # en alarme rouge (capteur indisponible, vanne déjà coupée
        # manuellement, chaudière indisponible...).
        if compute_room_security_state(self.hass, self.entry) == SECURITY_STATE_CRITICAL:
            should_activate = False

        if _get_central_heating_type(self.hass) == HEATING_TYPE_ELECTRIC:
            await self.hass.services.async_call(
                "switch",
                "turn_on" if should_activate else "turn_off",
                {"entity_id": self.heater_entity},
            )
        else:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {
                    "entity_id": self.heater_entity,
                    "temperature": VALVE_OPEN_TEMP if should_activate else VALVE_CLOSED_TEMP,
                },
            )

        await update_boiler_state(self.hass)
