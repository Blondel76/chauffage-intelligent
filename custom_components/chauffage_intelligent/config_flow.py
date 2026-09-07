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
)


def _get_preset_options(hass, climate_entity_id: str | None) -> list[str]:
    """Return the preset_modes of a climate entity, if available."""

    if not climate_entity_id:
        return []

    state = hass.states.get(climate_entity_id)

    if state is None:
        return []

    return list(state.attributes.get("preset_modes", []))


def _build_planning_string(user_input: dict[str, Any], slot_count: int) -> str:
    """Build the 'HH:MM|preset,...' planning string from slot fields."""

    slots = []

    for i in range(1, slot_count + 1):
        heure = user_input[f"heure_{i}"][:5]
        preset = user_input[f"preset_{i}"]
        slots.append((heure, preset))

    slots.sort(key=lambda s: s[0])

    return ",".join(f"{h}|{p}" for h, p in slots)


def _planning_slots_schema(presets: list[str], slot_count: int) -> vol.Schema:
    """Build a schema with `slot_count` time+preset pairs."""

    schema_dict: dict[Any, Any] = {}

    for i in range(1, slot_count + 1):
        schema_dict[vol.Required(f"heure_{i}")] = selector.TimeSelector()
        schema_dict[vol.Required(f"preset_{i}")] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=presets)
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
        self._slot_count: int = 0

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
            return await self.async_step_default_planning_count()

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

    async def async_step_default_planning_count(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Ask how many time slots the default planning has."""

        if user_input is not None:
            self._slot_count = user_input["nb_creneaux"]
            return await self.async_step_default_planning_slots()

        schema = vol.Schema(
            {
                vol.Required("nb_creneaux", default=2): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=10, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )

        return self.async_show_form(step_id="default_planning_count", data_schema=schema)

    async def async_step_default_planning_slots(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Fill in the default planning's time slots."""

        presets = _get_preset_options(self.hass, self._room_data.get(CONF_CLIMATE))

        if user_input is not None:
            self._room_data[CONF_DEFAULT_PLANNING] = _build_planning_string(
                user_input, self._slot_count
            )
            self._room_data[CONF_MODE_PLANNINGS] = {}

            await self.async_set_unique_id(self._room_data[CONF_AREA])
            self._abort_if_unique_id_configured()

            area_reg = ar.async_get(self.hass)
            area_entry = area_reg.async_get_area(self._room_data[CONF_AREA])
            title = area_entry.name if area_entry else self._room_data[CONF_AREA]

            return self.async_create_entry(title=title, data=self._room_data)

        return self.async_show_form(
            step_id="default_planning_slots",
            data_schema=_planning_slots_schema(presets, self._slot_count),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""

        return ChauffageIntelligentOptionsFlow(config_entry)


class ChauffageIntelligentOptionsFlow(config_entries.OptionsFlow):
    """Options flow: edit the central mode selector, or add/edit a room's mode planning."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize."""

        self.entry = config_entry
        self._selected_mode: str | None = None
        self._slot_count: int = 0

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
        """Pick which mode's planning to add or edit."""

        if user_input is not None:
            self._selected_mode = user_input["mode"]
            return await self.async_step_mode_planning_count()

        modes = _get_central_modes(self.hass)

        if not modes:
            return self.async_abort(reason="no_modes_defined")

        schema = vol.Schema(
            {
                vol.Required("mode"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=modes)
                ),
            }
        )

        return self.async_show_form(step_id="room_options", data_schema=schema)

    async def async_step_mode_planning_count(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Ask how many time slots this mode's planning has."""

        if user_input is not None:
            self._slot_count = user_input["nb_creneaux"]
            return await self.async_step_mode_planning_slots()

        schema = vol.Schema(
            {
                vol.Required("nb_creneaux", default=2): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=10, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )

        return self.async_show_form(step_id="mode_planning_count", data_schema=schema)

    async def async_step_mode_planning_slots(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Fill in the selected mode's time slots and save."""

        presets = _get_preset_options(self.hass, self.entry.data.get(CONF_CLIMATE))

        if user_input is not None:
            planning_str = _build_planning_string(user_input, self._slot_count)

            mode_plannings = dict(self.entry.data.get(CONF_MODE_PLANNINGS, {}))
            mode_plannings[self._selected_mode] = planning_str

            new_data = {**self.entry.data, CONF_MODE_PLANNINGS: mode_plannings}
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)

            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="mode_planning_slots",
            data_schema=_planning_slots_schema(presets, self._slot_count),
        )
