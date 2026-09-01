"""Constants for Chauffage Intelligent."""

import re
import unicodedata


DOMAIN = "chauffage_intelligent"

CONF_AREA = "area"
CONF_TEMP_EXT = "Température extérieur"
CONF_TEMP_INT = "Température intérieur"
CONF_PLANNING = "Planning de la pièce utilisé"
CONF_CLIMATE = "Thermostat de la pièce"
CONF_DERIVE = "derive capteur de température"


def slugify_area(area_name: str) -> str:
    """Convert an area name into a safe entity-id part."""

    normalized = unicodedata.normalize(
        "NFKD",
        area_name,
    )

    normalized = normalized.encode(
        "ascii",
        "ignore",
    ).decode("ascii")

    normalized = normalized.lower()

    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        normalized,
    )

    return normalized.strip("_")
