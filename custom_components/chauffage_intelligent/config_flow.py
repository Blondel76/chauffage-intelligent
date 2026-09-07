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
    CONF_DOOR_SENSOR,
    CONF_MODE_PLANNINGS,
    CONF_MODE_SELECTOR,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    DEFAULT_PLANNING,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_ROOM,
)


def _get_central_modes(hass) -> list[str]:
    """Return the current options of the central mode selector."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            mode_selector = entry.data.get(CONF_MODE_SELECTOR)
            state = hass.states.get(mode_selector)

            if state is not None:
                return list(state.attributes.get("options", []))

    return []


def _plannings_schema(modes: list[str], existing: dict[str, str]) -> vol.Schema:
    """Build one text field per mode, pre-filled with the existing value or the default."""

    schema_dict = {
        vol.Required(
            mode, default=existing.get(mode, DEFAULT_PLANNING)
        ): selector.TextSelector()
        for mode in modes
    }

    return vol.Schema(schema_dict)


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
            return await self.async_step_plannings()

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

    async def async_step_plannings(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Single page: one planning field per current central mode."""

        modes = _get_central_modes(self.hass)

        if user_input is not None:
            self._room_data[CONF_MODE_PLANNINGS] = {
                mode: user_input[mode] for mode in modes
            }

            await self.async_set_unique_id(self._room_data[CONF_AREA])
            self._abort_if_unique_id_configured()

            area_reg = ar.async_get(self.hass)
            area_entry = area_reg.async_get_area(self._room_data[CONF_AREA])
            title = area_entry.name if area_entry else self._room_data[CONF_AREA]

            return self.async_create_entry(title=title, data=self._room_data)

        return self.async_show_form(
            step_id="plannings",
            data_schema=_plannings_schema(modes, {}),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""

        return ChauffageIntelligentOptionsFlow(config_entry)


class ChauffageIntelligentOptionsFlow(config_entries.OptionsFlow):
    """Options flow: edit the central mode selector, or a room's plannings page."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize."""

        self.entry = config_entry

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
        """Same single-page planning editor, reused for edits."""

        modes = _get_central_modes(self.hass)
        existing = self.entry.data.get(CONF_MODE_PLANNINGS, {})

        if user_input is not None:
            new_mode_plannings = {mode: user_input[mode] for mode in modes}
            new_data = {**self.entry.data, CONF_MODE_PLANNINGS: new_mode_plannings}
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="room_options",
            data_schema=_plannings_schema(modes, existing),
        )
