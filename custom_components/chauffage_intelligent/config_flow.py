"""Config flow for Chauffage Intelligent."""

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import selector

from .const import (
    CONF_AREA,
    CONF_CLIMATE,
    CONF_DERIVE,
    CONF_PLANNING,
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
            await self.async_set_unique_id(user_input[CONF_AREA])
            self._abort_if_unique_id_configured()

            area_reg = ar.async_get(self.hass)
            area_entry = area_reg.async_get_area(user_input[CONF_AREA])
            title = area_entry.name if area_entry else user_input[CONF_AREA]

            return self.async_create_entry(
                title=title,
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_AREA): selector.AreaSelector(),

                vol.Required(CONF_TEMP_EXT): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor"],
                        device_class=["temperature"],
                    )
                ),

                vol.Required(CONF_TEMP_INT): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor"],
                        device_class=["temperature"],
                    )
                ),

                vol.Required(CONF_PLANNING): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["input_text"],
                    )
                ),

                vol.Required(CONF_CLIMATE): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["climate"],
                    )
                ),

                vol.Required(CONF_DERIVE): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor"],
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )
