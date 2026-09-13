"""Constants for Chauffage Intelligent."""

import re
import unicodedata


DOMAIN = "chauffage_intelligent"

ENTRY_TYPE = "entry_type"
ENTRY_TYPE_CENTRAL = "central"
ENTRY_TYPE_ROOM = "room"
ENTRY_TYPE_GROUP = "group"
CENTRAL_UNIQUE_ID = "chauffage_intelligent_central"

CONF_MODE_SELECTOR = "mode_selector"
CONF_HEATING_TYPE = "heating_type"
HEATING_TYPE_GAS = "gaz"
HEATING_TYPE_ELECTRIC = "electrique"

CONF_AREA = "area"
CONF_TEMP_EXT = "temp_ext"
CONF_TEMP_INT = "temp_int"
CONF_CLIMATE = "climate_entity"
CONF_DOOR_SENSOR = "door_sensor"
CONF_HEATER_ENTITY = "heater_entity"

CONF_MODE_PLANNINGS = "mode_plannings"
DEFAULT_PLANNING = "12:00|eco"

CONF_GROUP_NAME = "group_name"
CONF_GROUP_AREAS = "group_areas"
CONF_GROUP_THRESHOLD = "group_threshold"
DEFAULT_GROUP_THRESHOLD = 0.2

COEFFICIENT_MIN = 10.0
COEFFICIENT_MAX = 60.0
COEFFICIENT_DEFAULT = 25.0

DERIVE_INTERVAL_MINUTES = 3

VALVE_OPEN_TEMP = 29
VALVE_CLOSED_TEMP = 7


def slugify_area(area_name: str) -> str:
    """Convert an area name into a safe entity-id part."""

    normalized = unicodedata.normalize("NFKD", area_name)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)

    return normalized.strip("_")
