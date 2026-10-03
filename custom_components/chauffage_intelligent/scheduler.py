"""Scheduling, window safety, and heater activation for Chauffage Intelligent."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from .boiler import update_boiler_state
from .calculations import _normalized_hour, get_float, get_previous_schedule
from .const import (
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
    VALVE_CLOSED_TEMP,
    VALVE_OPEN_TEMP,
    slugify_area,
)
from .security import compute_room_security_state

_LOGGER = logging.getLogger(__name__)


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

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""

        self.hass = hass
        self.entry = entry
        self.area_id = entry.data[CONF_AREA]
        self.area_slug = slugify_area(self.area_id)
        self.climate_entity = entry.data.get(CONF_CLIMATE)
        self.heater_entity = entry.data.get(CONF_HEATER_ENTITY)
        self.door_entity = entry.data.get(CONF_DOOR_SENSOR)
        self.temp_int_entity = entry.data.get(CONF_TEMP_INT)
        self.window_switch_entity = f"switch.fenetre_ouverte_{self.area_slug}"

        self._remove_listeners: list = []

        # Créneau à venir pour lequel l'anticipation a démarré. Mémorisé
        # pour ne pas repasser au créneau précédent si l'heure anticipée
        # recalculée glisse pendant la montée en température.
        self._anticipated_slot: str | None = None

    def start(self) -> None:
        """Start listening for time changes, window state, and climate changes."""

        # Toutes les 30 s : pilotage de la chauffe + réconciliation du preset.
        self._remove_listeners.append(
            async_track_time_change(self.hass, self._handle_tick, second=[0, 30])
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

        self.hass.async_create_task(self._update_heater())

    def stop(self) -> None:
        """Stop all listeners."""

        for remove in self._remove_listeners:
            if remove is not None:
                remove()

        self._remove_listeners.clear()

    # ------------------------------------------------------------
    # Programmation horaire
    # ------------------------------------------------------------

    async def _handle_tick(self, now) -> None:
        """Toutes les 30 s : chauffe + réconciliation du preset.

        La réconciliation compare le preset attendu (planning, anticipation
        comprise) avec celui du thermostat et corrige l'écart. Cela couvre
        sans code dédié le démarrage de HA, la modification d'un planning,
        un changement de mode et un thermostat qui redevient disponible.
        """

        await self._update_heater()
        await self._reconcile_preset()

    def _slot_due_now(self) -> str | None:
        """Retourne le créneau 'HH:MM|preset' qui doit être actif maintenant.

        - Hors anticipation : le créneau précédent (dernier passé).
        - En anticipation : le créneau à venir, dès l'heure anticipée.
        """

        data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        resolver = data.get("resolver")

        if resolver is None:
            return None

        due = get_previous_schedule(resolver.get_active_planning())

        if due == "unknown":
            return None

        # Le créneau anticipé est arrivé : il devient le créneau normal.
        if self._anticipated_slot is not None and due == self._anticipated_slot:
            self._anticipated_slot = None
            return due

        ant = self.hass.states.get(f"sensor.heure_anticipee_{self.area_slug}")
        nxt = self.hass.states.get(f"sensor.heure_planning_{self.area_slug}")

        if nxt is None or "|" not in nxt.state:
            self._anticipated_slot = None
            return due

        # Anticipation déjà démarrée pour ce créneau : on la conserve.
        if self._anticipated_slot is not None:
            if nxt.state == self._anticipated_slot:
                return nxt.state

            # Le planning a changé entre-temps : on abandonne l'anticipation.
            self._anticipated_slot = None

        if ant is None or ant.state in ("unknown", "unavailable"):
            return due

        try:
            ah, am = map(int, ant.state.split(":"))
            nxt_hour = _normalized_hour(nxt.state.split("|")[0].strip())
            nh, nm = map(int, nxt_hour.split(":"))
        except (ValueError, AttributeError):
            return due

        now = dt_util.now()
        now_min = now.hour * 60 + now.minute
        start = ah * 60 + am
        end = nh * 60 + nm

        if start <= end:
            in_window = start <= now_min <= end
        else:
            # Anticipation à cheval sur minuit.
            in_window = now_min >= start or now_min <= end

        if in_window:
            self._anticipated_slot = nxt.state
            return nxt.state

        return due

    async def _reconcile_preset(self) -> None:
        """Applique le preset attendu s'il diffère de celui du thermostat."""

        if not self.climate_entity:
            return

        climate_state = self.hass.states.get(self.climate_entity)

        if climate_state is None or climate_state.state in ("unknown", "unavailable"):
            return

        slot = self._slot_due_now()

        if slot is None or "|" not in slot:
            return

        preset = slot.split("|", 1)[1].strip()

        if not preset:
            return

        if climate_state.attributes.get("preset_mode") == preset:
            return

        await self._apply_slot(slot)

    async def _apply_slot(self, slot: str) -> None:
        """Apply the preset of a 'HH:MM|preset' slot."""

        if not self.climate_entity or not slot or "|" not in slot:
            return

        preset = slot.split("|", 1)[1].strip()

        if not preset:
            return

        try:
            await self.hass.services.async_call(
                "climate",
                "set_preset_mode",
                {"entity_id": self.climate_entity, "preset_mode": preset},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Preset '%s' refusé par %s : %s", preset, self.climate_entity, err
            )

    # ------------------------------------------------------------
    # Sécurité fenêtre/porte
    # ------------------------------------------------------------

    async def _handle_window_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Cut or restore heating when the window/door state changes."""

        if not self.climate_entity:
            return

        new_state = event.data.get("new_state")

        if new_state is None:
            return

        is_open = new_state.state == "on"

        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": self.climate_entity, "hvac_mode": "off" if is_open else "heat"},
        )

    # ------------------------------------------------------------
    # Pilotage de l'entité de chauffe réelle (vanne ou interrupteur)
    # ------------------------------------------------------------

    async def _handle_climate_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """React immediately to this room's own climate changes."""

        await self._update_heater()

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

        heating_type = _get_central_heating_type(self.hass)

        # Sécurité : même si le thermostat demande à chauffer, on
        # n'ouvre jamais la vanne/l'interrupteur tant que la pièce est
        # en alarme rouge (capteur indisponible, vanne déjà coupée
        # manuellement, chaudière indisponible...).
        if compute_room_security_state(self.hass, self.entry) == SECURITY_STATE_CRITICAL:
            should_activate = False

        if heating_type == HEATING_TYPE_ELECTRIC:
            service = "turn_on" if should_activate else "turn_off"

            await self.hass.services.async_call(
                "switch", service, {"entity_id": self.heater_entity}
            )
        else:
            temperature = VALVE_OPEN_TEMP if should_activate else VALVE_CLOSED_TEMP

            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": self.heater_entity, "temperature": temperature},
            )

        await update_boiler_state(self.hass)
