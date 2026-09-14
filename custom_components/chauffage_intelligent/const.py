"""Constants for Chauffage Intelligent."""

import re
import unicodedata


DOMAIN = "chauffage_intelligent"

ENTRY_TYPE = "entry_type"
ENTRY_TYPE_CENTRAL = "central"
ENTRY_TYPE_ROOM = "room"
ENTRY_TYPE_GROUP = "group"
CENTRAL_UNIQUE_ID = "chauffage_intelligent_central"

CONF_MODE_SELECTOR = "Sélecteur de mode"
CONF_HEATING_TYPE = "Type de chauffage"
HEATING_TYPE_GAS = "gaz"
HEATING_TYPE_ELECTRIC = "electrique"

CONF_AREA = "area"
CONF_TEMP_EXT = "Température extérieur"
CONF_TEMP_INT = "Température intérieur"
CONF_CLIMATE = "Thermostat de la pièce"
CONF_DOOR_SENSOR = "Capteur de porte/fenêtre (optionnel)"
CONF_HEATER_ENTITY = "Vanne ou interrupteur du radiateur"
CONF_BOILER_ENTITY = "Chaudière (interrupteur ou climat, optionnel si chauffage électrique)"

CONF_MODE_PLANNINGS = "mode_plannings"
DEFAULT_PLANNING = "12:00|eco"

CONF_GROUP_NAME = "Nom du groupe"
CONF_GROUP_AREAS = "Pièces du groupe"
CONF_GROUP_THRESHOLD = "Seuil de température (°C)"
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
