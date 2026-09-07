"""Constants for Chauffage Intelligent."""

import re
import unicodedata


DOMAIN = "chauffage_intelligent"

ENTRY_TYPE = "entry_type"
ENTRY_TYPE_CENTRAL = "central"
ENTRY_TYPE_ROOM = "room"
CENTRAL_UNIQUE_ID = "chauffage_intelligent_central"

CONF_MODE_SELECTOR = "mode_selector"

CONF_AREA = "area"
CONF_TEMP_EXT = "Température extérieur"
CONF_TEMP_INT = "Température intérieur"
CONF_CLIMATE = "Thermostat de la pièce"
CONF_DOOR_SENSOR = "Capteur de porte/fenêtre (optionnel)"

CONF_DEFAULT_PLANNING = "default_planning"
CONF_MODE_PLANNINGS = "mode_plannings"

COEFFICIENT_MIN = 10.0
COEFFICIENT_MAX = 60.0
COEFFICIENT_DEFAULT = 25.0

DERIVE_INTERVAL_MINUTES = 3


def slugify_area(area_name: str) -> str:
    """Convert an area name into a safe entity-id part."""

    normalized = unicodedata.normalize("NFKD", area_name)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)

    return normalized.strip("_")
