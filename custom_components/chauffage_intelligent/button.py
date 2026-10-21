"""Button entities for Chauffage Intelligent (réarmement par pièce)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_AREA, DOMAIN, slugify_area


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the rearm button of a room."""

    area_slug = slugify_area(entry.data[CONF_AREA])

    async_add_entities([RearmButton(entry, area_slug)])


class RearmButton(ButtonEntity):
    """Réarme la sécurité d'une pièce après un défaut « la température ne monte pas ».

    Le réarmement lève le verrou et repart d'un suivi vierge, puis le
    scheduler réévalue la pièce. Le contrôle complet (thermostat, capteurs,
    vanne, porte, chaudière) est recalculé en continu par le capteur de
    sécurité : si une autre cause de panne subsiste, la pièce reste rouge
    et l'attribut « raisons » en donne la liste.
    """

    _attr_has_entity_name = True
    _attr_name = "Réarmer la sécurité"
    _attr_icon = "mdi:shield-refresh"

    def __init__(self, entry: ConfigEntry, area_slug: str) -> None:
        """Initialize."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = f"{entry.entry_id}_rearmer"
        self.entity_id = f"button.rearmer_{area_slug}"
        self._attr_suggested_object_id = f"rearmer_{area_slug}"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, area_slug)},
            "name": area_slug.replace("_", " ").title(),
        }

    async def async_press(self) -> None:
        """Lève le verrou puis réévalue la pièce."""

        data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})

        monitor = data.get("monitor")

        if monitor is not None:
            monitor.rearm()

        scheduler = data.get("scheduler")

        if scheduler is not None:
            await scheduler.async_refresh()
