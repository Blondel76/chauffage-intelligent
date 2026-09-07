"""Config flow for Chauffage Intelligent."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import selector

from .const import (
    CENTRAL_UNIQUE_ID,
    CONF_AREA,
    CONF_CLIMATE,
    CONF_DEFAULT_PLANNING,
    CONF_DOOR_SENSOR,
    CONF_MODE_PLANNINGS,
    CONF_MODE_SELECTOR,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_ROOM,
    MAX_PLANNING_SLOTS,
)


def _get_preset_options(hass, climate_entity_id: str | None) -> list[str]:
    """Return the preset_modes of a climate entity, if available."""

    if not climate_entity_id:
        return []

    state = hass.states.get(climate_entity_id)

    if state is None:
        return []

    return list(state.attributes.get("preset_modes", []))


def _parse_planning_slots(planning: str) -> list[tuple[str, str]]:
    """Parse a 'HH:MM|preset,...' string into a list of (heure, preset) tuples."""

    slots = []

    for item in planning.split(","):

        if "|" not in item:
            continue

        h, p = item.split("|", 1)
        slots.append((h.strip(), p.strip()))

    return slots


def _build_planning_string(user_input: dict[str, Any]) -> str:
    """Build the 'HH:MM|preset,...' planning string, skipping empty slots."""

    slots = []

    for i in range(1, MAX_PLANNING_SLOTS + 1):
        heure = user_input.get(f"heure_{i}")
        preset = user_input.get(f"preset_{i}")

        if not heure or not preset:
            continue

        slots.append((heure[:5], preset))

    slots.sort(key=lambda s: s[0])

    return ",".join(f"{h}|{p}" for h, p in slots)


def _planning_slots_schema(
    presets: list[str],
    existing_slots: list[tuple[str, str]] | None = None,
) -> vol.Schema:
    """Build a schema with up to MAX_PLANNING_SLOTS time+preset pairs.

    Slot 1 is required (at least one entry needed). The rest are optional
    and simply ignored if left empty.
    """

    existing_slots = existing_slots or []
    schema_dict: dict[Any, Any] = {}

    for i in range(1, MAX_PLANNING_SLOTS + 1):
        idx = i - 1
        marker = vol.Required if i == 1 else vol.Optional
        has_existing = idx < len(existing_slots)

        if has_existing:
            heure_field = marker(f"heure_{i}", default=existing_slots[idx][0])
            preset_field = marker(f"preset_{i}", default=existing_slots[idx][1])
        else:
            heure_field = marker(f"heure_{i}")
            preset_field = marker(f"preset_{i}")

        schema_dict[heure_field] = selector.TimeSelector()
        schema_dict[preset_field] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=presets, custom_value=True)
        )

    return vol.Schema(schema_dict)


def _get_central_modes(hass) -> list[str]:
    """Return the current options of the central mode selector."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            mode_selector = entry.data.get(CONF_MODE_SELECTOR)
            state = hass.states.get(mode_selector)

            if state is not None:
                return list(state.attributes.get("options", []))

    return []


class ChauffageIntelligentConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Handle a config flow for Chauffage Intelligent."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""

        self._room_data: dict[str, Any] = {}

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Route to the central setup (mandatory first) or the room setup."""

        central_exists = any(
            entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL
            for entry in self._async_current_entries()
        )

        if not central_exists:
            return await self.async_step_central()

        return await self.async_step_room()

    async def async_step_central(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Create the single central configuration entry."""

        if user_input is not None:
            await self.async_set_unique_id(CENTRAL_UNIQUE_ID)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title="Chauffage Intelligent - Configuration centrale",
                data={**user_input, ENTRY_TYPE: ENTRY_TYPE_CENTRAL},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_MODE_SELECTOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["input_select"])
                ),
            }
        )

        return self.async_show_form(step_id="central", data_schema=schema)

    async def async_step_room(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Collect the room's base configuration."""

        if user_input is not None:
            self._room_data = {**user_input, ENTRY_TYPE: ENTRY_TYPE_ROOM}
            return await self.async_step_default_planning_slots()

        schema = vol.Schema(
            {
                vol.Required(CONF_AREA): selector.AreaSelector(),

                vol.Required(CONF_TEMP_EXT): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor"], device_class=["temperature"]
                    )
                ),

                vol.Required(CONF_TEMP_INT): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor"], device_class=["temperature"]
                    )
                ),

                vol.Required(CONF_CLIMATE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["climate"])
                ),

                vol.Optional(CONF_DOOR_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor"])
                ),
            }
        )

        return self.async_show_form(step_id="room", data_schema=schema)

    async def async_step_default_planning_slots(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Fill in the default planning's time slots."""

        presets = _get_preset_options(self.hass, self._room_data.get(CONF_CLIMATE))

        if user_input is not None:
            self._room_data[CONF_DEFAULT_PLANNING] = _build_planning_string(user_input)
            self._room_data[CONF_MODE_PLANNINGS] = {}

            await self.async_set_unique_id(self._room_data[CONF_AREA])
            self._abort_if_unique_id_configured()

            area_reg = ar.async_get(self.hass)
            area_entry = area_reg.async_get_area(self._room_data[CONF_AREA])
            title = area_entry.name if area_entry else self._room_data[CONF_AREA]

            return self.async_create_entry(title=title, data=self._room_data)

        return self.async_show_form(
            step_id="default_planning_slots",
            data_schema=_planning_slots_schema(presets),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""

        return ChauffageIntelligentOptionsFlow(config_entry)


class ChauffageIntelligentOptionsFlow(config_entries.OptionsFlow):
    """Options flow: edit the central mode selector, or a room's plannings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize."""

        self.entry = config_entry
        self._selected_mode: str | None = None

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Route to the right options step depending on the entry type."""

        if self.entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return await self.async_step_central_options()

        return await self.async_step_room_options()

    async def async_step_central_options(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Edit the central mode selector."""

        if user_input is not None:
            new_data = {**self.entry.data, **user_input}
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
            return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MODE_SELECTOR,
                    default=self.entry.data.get(CONF_MODE_SELECTOR),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["input_select"])
                ),
            }
        )

        return self.async_show_form(step_id="central_options", data_schema=schema)

    async def async_step_room_options(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Pick 'Défaut' or a mode to edit its planning."""

        if user_input is not None:
            self._selected_mode = user_input["mode"]
            return await self.async_step_planning_slots()

        modes = ["Défaut (planning de base)"] + _get_central_modes(self.hass)

        schema = vol.Schema(
            {
                vol.Required("mode"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=modes)
                ),
            }
        )

        return self.async_show_form(step_id="room_options", data_schema=schema)

    async def async_step_planning_slots(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Fill in the selected mode's (or default's) time slots and save."""

        presets = _get_preset_options(self.hass, self.entry.data.get(CONF_CLIMATE))
        is_default = self._selected_mode == "Défaut (planning de base)"

        if is_default:
            existing = self.entry.data.get(CONF_DEFAULT_PLANNING, "")
        else:
            mode_plannings = self.entry.data.get(CONF_MODE_PLANNINGS, {})
            existing = mode_plannings.get(self._selected_mode, "")

        existing_slots = _parse_planning_slots(existing) if existing else []

        if user_input is not None:
            planning_str = _build_planning_string(user_input)

            if is_default:
                new_data = {**self.entry.data, CONF_DEFAULT_PLANNING: planning_str}
            else:
                mode_plannings = dict(self.entry.data.get(CONF_MODE_PLANNINGS, {}))
                mode_plannings[self._selected_mode] = planning_str
                new_data = {**self.entry.data, CONF_MODE_PLANNINGS: mode_plannings}

            self.hass.config_entries.async_update_entry(self.entry, data=new_data)

            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="planning_slots",
            data_schema=_planning_slots_schema(presets, existing_slots),
        )
