"""Configuration flow for Chauffage Intelligent."""

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_CLIMATE,
    CONF_PLANNING,
    CONF_ROOM,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    DOMAIN,
)


class ChauffageIntelligentConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Handle a config flow for Chauffage Intelligent."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Handle the initial setup."""

        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_ROOM],
                data=user_input,
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ROOM): str,

                vol.Required(CONF_TEMP_EXT): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor",
                    )
                ),

                vol.Required(CONF_TEMP_INT): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor",
                    )
                ),

                vol.Required(CONF_PLANNING): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="input_text",
                    )
                ),

                vol.Required(CONF_CLIMATE): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="climate",
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
        )
