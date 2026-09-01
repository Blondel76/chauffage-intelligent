"""Sensor entities for Chauffage Intelligent."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CLIMATE,
    CONF_DERIVE,
    CONF_PLANNING,
    CONF_TEMP_EXT,
    CONF_TEMP_INT,
    slugify_area,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up heating sensors."""

    area_name = entry.data["area"]
    area_slug = slugify_area(area_name)

    async_add_entities(
        [
            TempsDeChauffeSensor(entry, area_slug),
            DeriveSensor(entry, area_slug),
            HeurePlanningSensor(entry, area_slug),
            HeurePlanningPrecedentSensor(entry, area_slug),
            HeureAnticipeeSensor(entry, area_slug),
        ]
    )


class ChauffageSensorBase(SensorEntity):
    """Base class for heating sensors."""

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
        key: str,
        name: str,
    ) -> None:
        """Initialize the sensor."""

        self._entry = entry
        self._area_slug = area_slug

        self._attr_unique_id = (
            f"{entry.entry_id}_{key}"
        )

        self._attr_name = (
            f"{name} {area_slug.replace('_', ' ').title()}"
        )

    @property
    def area_name(self) -> str:
        """Return the configured area."""

        return self._entry.data["area"]


class TempsDeChauffeSensor(ChauffageSensorBase):
    """Estimated heating time."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "temps_de_chauffe",
            "Temps de chauffe",
        )

    def update(self) -> None:
        """Calculate the estimated heating time."""

        hass = self.hass
        config = self._entry.data

        temp = _get_float(
            hass,
            config.get(CONF_TEMP_INT),
            0,
        )

        climate = hass.states.get(
            config.get(CONF_CLIMATE)
        )

        consigne = 0.0

        if climate:
            consigne = _to_float(
                climate.attributes.get("temperature"),
                0,
            )

        delta = consigne - temp

        coefficient = _get_float(
            hass,
            f"number.coefficient_{self._area_slug}",
            25,
        )

        coefficient = min(
            max(coefficient, 10),
            60,
        )

        temp_ext = _get_float(
            hass,
            config.get(CONF_TEMP_EXT),
            10,
        )

        facteur_ext = 1 + (
            (temp - temp_ext) / 50
        )

        facteur_ext = min(
            max(facteur_ext, 0.7),
            1.5,
        )

        if delta > 0.3:
            self._attr_native_value = round(
                delta
                * coefficient
                * facteur_ext
            )
        else:
            self._attr_native_value = 0


class DeriveSensor(ChauffageSensorBase):
    """Expose the configured temperature derivative."""

    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "°C/min"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "derive",
            "Dérive",
        )

    def update(self) -> None:
        """Read the configured derivative sensor."""

        entity_id = self._entry.data.get(
            CONF_DERIVE
        )

        self._attr_native_value = _get_float(
            self.hass,
            entity_id,
            0,
        )


class HeurePlanningSensor(ChauffageSensorBase):
    """Next heating schedule time."""

    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "heure_planning",
            "Heure planning",
        )

    def update(self) -> None:
        """Calculate the next schedule time."""

        planning = self.hass.states[
            self._entry.data[CONF_PLANNING]
        ].state

        self._attr_native_value = (
            _heure_planning(planning)
        )


class HeurePlanningPrecedentSensor(
    ChauffageSensorBase
):
    """Previous heating schedule time."""

    _attr_icon = "mdi:clock-check-outline"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "heure_planning_precedent",
            "Heure planning précédent",
        )

    def update(self) -> None:
        """Calculate the previous schedule time."""

        planning = self.hass.states[
            self._entry.data[CONF_PLANNING]
        ].state

        self._attr_native_value = (
            _heure_planning_precedent(planning)
        )


class HeureAnticipeeSensor(ChauffageSensorBase):
    """Calculated heating start time."""

    _attr_icon = "mdi:clock-start"

    def __init__(
        self,
        entry: ConfigEntry,
        area_slug: str,
    ) -> None:
        """Initialize."""

        super().__init__(
            entry,
            area_slug,
            "heure_anticipee",
            "Heure anticipée",
        )

    def update(self) -> None:
        """Calculate the anticipated heating time."""

        config = self._entry.data

        planning = self.hass.states[
            config[CONF_PLANNING]
        ].state

        cible = _heure_planning(planning)

        if not cible:
            self._attr_native_value = "--"
            return

        try:
            heure = cible.replace("h", ":")[:5]
            hh, mm = map(
                int,
                heure.split(":"),
            )

        except (ValueError, AttributeError):
            self._attr_native_value = cible
            return

        temps = _get_float(
            self.hass,
            f"sensor.temps_de_chauffe_{self._area_slug}",
            0,
        )

        maintenant = datetime.now()

        cible_date = maintenant.replace(
            hour=hh,
            minute=mm,
            second=0,
            microsecond=0,
        )

        if temps > 0 and temps < 180:

            debut = (
                cible_date.timestamp()
                - temps * 60
            )

            debut_date = datetime.fromtimestamp(
                debut
            )

            if debut_date < maintenant:
                self._attr_native_value = (
                    maintenant.strftime("%H:%M")
                )
            else:
                self._attr_native_value = (
                    debut_date.strftime("%H:%M")
                )

        else:
            self._attr_native_value = heure


def _get_float(
    hass: HomeAssistant,
    entity_id: str | None,
    default: float,
) -> float:
    """Get a float from an entity."""

    if not entity_id:
        return default

    state = hass.states.get(entity_id)

    if state is None:
        return default

    return _to_float(
        state.state,
        default,
    )


def _to_float(
    value: object,
    default: float,
) -> float:
    """Convert a value to float."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _heure_planning(
    planning: str,
) -> str:
    """Return the next schedule."""

    if not planning or planning in {
        "unknown",
        "unavailable",
        "none",
    }:
        return "unknown"

    maintenant = datetime.now().strftime("%H:%M")

    for item in planning.split(","):

        if "|" not in item:
            continue

        heure, minute = item.split("|", 1)

        heure = heure.strip()
        minute = minute.strip()

        heure_comparee = heure.replace(
            "h",
            ":",
        )

        if heure_comparee > maintenant:
            return f"{heure}|{minute}"

    return planning.split(",")[0].strip()


def _heure_planning_precedent(
    planning: str,
) -> str:
    """Return the previous schedule."""

    if not planning or planning in {
        "unknown",
        "unavailable",
        "none",
    }:
        return "unknown"

    maintenant = datetime.now().strftime("%H:%M")

    resultat = None

    for item in planning.split(","):

        if "|" not in item:
            continue

        heure, minute = item.split("|", 1)

        heure = heure.strip()
        minute = minute.strip()

        heure_comparee = heure.replace(
            "h",
            ":",
        )

        if heure_comparee <= maintenant:
            resultat = f"{heure}|{minute}"

    if resultat is not None:
        return resultat

    items = planning.split(",")

    return items[-1].strip()
