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
    CONF_GROUP_AREAS,
    CONF_GROUP_NAME,
    CONF_GROUP_THRESHOLD,
    CONF_HEATER_ENTITY,
    CONF_HEATING_TYPE,
    CONF_MODE_PLANNINGS,
    CONF_MODE_SELECTOR,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    DEFAULT_GROUP_THRESHOLD,
    DEFAULT_PLANNING,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_GROUP,
    ENTRY_TYPE_ROOM,
    HEATING_TYPE_ELECTRIC,
    HEATING_TYPE_GAS,
)


def _get_central_entry(hass):
    """Return the central config entry, if any."""

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_CENTRAL:
            return entry

    return None


def _get_central_modes(hass) -> list[str]:
    """Return the current options of the central mode selector."""

    central = _get_central_entry(hass)

    if central is None:
        return []

    state = hass.states.get(central.data.get(CONF_MODE_SELECTOR))

    if state is None:
        return []

    return list(state.attributes.get("options", []))

def _get_room_options(hass) -> list[dict]:
    """Return selectable options built from existing room entries (not raw HA areas)."""

    options = []

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_ROOM:
            options.append({"value": entry.data[CONF_AREA], "label": entry.title})

    return options

def _get_central_heating_type(hass) -> str:
    """Return the house's heating type, defaulting to gas."""

    central = _get_central_entry(hass)

    if central is None:
        return HEATING_TYPE_GAS

    return central.data.get(CONF_HEATING_TYPE, HEATING_TYPE_GAS)


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
        """Force the central setup first, otherwise offer room or group."""

        if _get_central_entry(self.hass) is None:
            return await self.async_step_central()

        return await self.async_step_menu()

    async def async_step_menu(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Choose whether to add a room or a group."""

        if user_input is not None:
            if user_input["type"] == "room":
                return await self.async_step_room()

            return await self.async_step_group()

        schema = vol.Schema(
            {
                vol.Required("type", default="room"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "room", "label": "Nouvelle pièce"},
                            {"value": "group", "label": "Nouveau groupe"},
                        ]
                    )
                ),
            }
        )

        return self.async_show_form(step_id="menu", data_schema=schema)

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
                vol.Required(CONF_HEATING_TYPE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": HEATING_TYPE_GAS, "label": "Gaz"},
                            {"value": HEATING_TYPE_ELECTRIC, "label": "Électrique"},
                        ]
                    )
                ),
            }
        )

        return self.async_show_form(step_id="central", data_schema=schema)

    async def async_step_group(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Create a room group (thermally open rooms), from existing rooms only."""

        room_options = _get_room_options(self.hass)

        if not room_options:
            return self.async_abort(reason="no_rooms_configured")

        if user_input is not None:
            await self.async_set_unique_id(f"group_{user_input[CONF_GROUP_NAME]}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Groupe : {user_input[CONF_GROUP_NAME]}",
                data={**user_input, ENTRY_TYPE: ENTRY_TYPE_GROUP},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_GROUP_NAME): selector.TextSelector(),
                vol.Required(CONF_GROUP_AREAS): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=room_options, multiple=True)
                ),
                vol.Required(
                    CONF_GROUP_THRESHOLD, default=DEFAULT_GROUP_THRESHOLD
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=5, step=0.1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )

        return self.async_show_form(step_id="group", data_schema=schema)

    async def async_step_room(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Collect the room's base configuration."""

        if user_input is not None:
            self._room_data = {**user_input, ENTRY_TYPE: ENTRY_TYPE_ROOM}
            return await self.async_step_plannings()

        heating_type = _get_central_heating_type(self.hass)
        heater_domain = (
            ["switch"] if heating_type == HEATING_TYPE_ELECTRIC else ["climate"]
        )

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

                vol.Required(CONF_HEATER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=heater_domain)
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
    """Options flow: routes to central, room, or group editing."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize."""

        self.entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Route to the right options step depending on the entry type."""

        entry_type = self.entry.data.get(ENTRY_TYPE)

        if entry_type == ENTRY_TYPE_CENTRAL:
            return await self.async_step_central_options()

        if entry_type == ENTRY_TYPE_GROUP:
            return await self.async_step_group_options()

        return await self.async_step_room_options()

    async def async_step_central_options(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Edit the central mode selector and heating type."""

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
                vol.Required(
                    CONF_HEATING_TYPE,
                    default=self.entry.data.get(CONF_HEATING_TYPE, HEATING_TYPE_GAS),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": HEATING_TYPE_GAS, "label": "Gaz"},
                            {"value": HEATING_TYPE_ELECTRIC, "label": "Électrique"},
                        ]
                    )
                ),
            }
        )

        return self.async_show_form(step_id="central_options", data_schema=schema)

    async def async_step_group_options(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Edit a group's name, rooms, and threshold."""

        room_options = _get_room_options(self.hass)

        if user_input is not None:
            new_data = {**self.entry.data, **user_input}
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
            return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_GROUP_NAME, default=self.entry.data.get(CONF_GROUP_NAME)
                ): selector.TextSelector(),
                vol.Required(
                    CONF_GROUP_AREAS, default=self.entry.data.get(CONF_GROUP_AREAS, [])
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=room_options, multiple=True)
                ),
                vol.Required(
                    CONF_GROUP_THRESHOLD,
                    default=self.entry.data.get(
                        CONF_GROUP_THRESHOLD, DEFAULT_GROUP_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=5, step=0.1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )

        return self.async_show_form(step_id="group_options", data_schema=schema)

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
