"""Configuration flow for Chauffage Intelligent."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME

from .const import DOMAIN


class ChauffageIntelligentConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Handle a config flow for Chauffage Intelligent."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""

        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Chauffage Intelligent"): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )
