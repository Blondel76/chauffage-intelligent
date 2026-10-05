"""Scheduling, window safety, and heater activation for Chauffage Intelligent."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
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
    CONF_MODE_SELECTOR,
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


def _get_central_heating_type(hass: HomeAssistant) -> str:
    """Return the house's heating type, defaulting to gas."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry.data.get(CONF_HEATING_TYPE, HEATING_TYPE_GAS)

    return HEATING_TYPE_GAS


def _get_central_mode_selector(hass: HomeAssistant) -> str | None:
    """Return the central mode selector entity id, if any."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry.data.get(CONF_MODE_SELECTOR)

    return None


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
        self._delayed_apply_task: asyncio.Task | None = None

        # Cible (date/heure du créneau) pour laquelle l'anticipation a déjà
        # été appliquée : évite de réappliquer le même créneau à chaque minute.
        self._anticipated_target: datetime | None = None

    def start(self) -> None:
        """Start listening for time changes, window state, mode and climate changes."""

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

        # Changement du mode de la maison (input_select central).
        mode_entity = _get_central_mode_selector(self.hass)

        if mode_entity:
            self._remove_listeners.append(
                async_track_state_change_event(
                    self.hass, [mode_entity], self._handle_mode_change
                )
            )

        # Au démarrage : appliquer le créneau EN COURS (le précédent),
        # mais seulement une fois HA entièrement démarré (thermostats
        # chargés). Si HA tourne déjà (rechargement), c'est immédiat.
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

        if self._delayed_apply_task is not None:
            self._delayed_apply_task.cancel()
            self._delayed_apply_task = None

    @callback
    def _handle_ha_started(self, hass: HomeAssistant) -> None:
        """HA est entièrement démarré : appliquer le créneau en cours."""

        self._schedule_delayed_apply()

    def _schedule_delayed_apply(self) -> None:
        """Planifie l'application du créneau en cours après un court délai."""

        if self._delayed_apply_task is not None:
            self._delayed_apply_task.cancel()

        self._delayed_apply_task = self.hass.async_create_task(
            self._delayed_apply()
        )

    async def _delayed_apply(self) -> None:
        """Attend que le thermostat se stabilise, puis applique le créneau."""

        await asyncio.sleep(5)
        await self.apply_slot_for_now()

    # ------------------------------------------------------------
    # Programmation horaire
    # ------------------------------------------------------------

    def _read_coefficient(self) -> float:
        """Read this room's coefficient number entity."""

        state = self.hass.states.get(f"number.coefficient_{self.area_slug}")

        try:
            return float(state.state)
        except (AttributeError, ValueError, TypeError):
            return COEFFICIENT_DEFAULT

    def _get_planning(self) -> str | None:
        """Return the active planning string, if the resolver is ready."""

        data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        resolver = data.get("resolver")

        if resolver is None:
            return None

        return resolver.get_active_planning()

    async def _handle_minute_tick(self, now) -> None:
        """Every minute: re-check the heater, and apply the next slot once its anticipated start is reached.

        Re-checking the heater every minute also catches group borrowing
        opportunities without needing a listener on every linked room's
        climate entity.
        """

        await self._update_heater()

        if self.climate_entity:
            await self._check_anticipation()

    async def _check_anticipation(self) -> None:
        """Apply the upcoming slot once, (heating time + 1 min) before its start.

        Calculé directement (sans lire de capteur) pour ne pas dépendre
        d'un état périodique potentiellement en retard. Le créneau n'est
        appliqué qu'une fois par cible (self._anticipated_target).
        """

        planning = self._get_planning()

        if not planning:
            return

        slot = get_next_schedule(planning)

        if "|" not in slot:
            return

        raw_hour = slot.split("|", 1)[0].strip().replace("h", ":")

        try:
            hh, mm = map(int, raw_hour.split(":")[:2])
        except ValueError:
            return

        now = datetime.now()
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)

        if target <= now:
            target += timedelta(days=1)

        besoin = calculate_heating_time(
            self.hass, self.entry.data, self._read_coefficient()
        )

        if not 0 < besoin < 180:
            besoin = 0

        # -1 min : à l'heure pile du créneau, get_next_schedule est déjà
        # passé au suivant, il faut donc appliquer juste avant.
        start = target - timedelta(minutes=besoin + 1)

        if now < start or self._anticipated_target == target:
            return

        self._anticipated_target = target
        await self._apply_slot(slot)

    async def _apply_slot(self, slot: str) -> None:
        """Apply the preset of a 'HH:MM|preset' slot."""

        if not self.climate_entity or not slot or "|" not in slot:
            return

        preset = slot.split("|", 1)[1].strip()

        if not preset:
            return

        await self.hass.services.async_call(
            "climate",
            "set_preset_mode",
            {"entity_id": self.climate_entity, "preset_mode": preset},
        )

    async def apply_slot_for_now(self) -> None:
        """Apply the slot that should be active right now.

        Appelé au démarrage, au retour du thermostat, au changement de mode
        et à la modification d'un planning. Calculé directement depuis le
        résolveur, sans dépendre d'un capteur qui pourrait ne pas être
        encore prêt.
        """

        climate_state = (
            self.hass.states.get(self.climate_entity) if self.climate_entity else None
        )

        if climate_state is None or climate_state.state in ("unknown", "unavailable"):
            return

        planning = self._get_planning()

        if planning is None:
            return

        await self._apply_slot(get_previous_schedule(planning))

        # Si on est déjà dans la fenêtre d'anticipation du prochain
        # créneau, l'appliquer plutôt que de rester sur l'ancien.
        self._anticipated_target = None
        await self._check_anticipation()

    # ------------------------------------------------------------
    # Changement de mode de la maison
    # ------------------------------------------------------------

    async def _handle_mode_change(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Le mode de la maison change : réappliquer immédiatement le créneau en cours."""

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        if new_state is None or new_state.state in ("unknown", "unavailable"):
            return

        # Ignore les simples changements d'attributs (ex. liste d'options modifiée)
        if old_state is not None and old_state.state == new_state.state:
            return

        await self.apply_slot_for_now()

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

        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        # Le thermostat redevient disponible (ex. après un démarrage de HA
        # ou une panne) : réappliquer le créneau en cours.
        if (
            new_state is not None
            and new_state.state not in ("unknown", "unavailable")
            and (old_state is None or old_state.state in ("unknown", "unavailable"))
        ):
            self._schedule_delayed_apply()

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
