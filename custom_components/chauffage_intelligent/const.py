"""Constantes de l'intégration Chauffage Intelligent."""

import re
import unicodedata

# Nom de l'intégration
DOMAIN = "chauffage_intelligent"

# Types de configuration
ENTRY_TYPE = "entry_type"
ENTRY_TYPE_CENTRAL = "central"
ENTRY_TYPE_ROOM = "room"
ENTRY_TYPE_GROUP = "group"

# Identifiant unique de la configuration centrale
CENTRAL_UNIQUE_ID = "chauffage_intelligent_central"

# Configuration centrale
CONF_MODE_SELECTOR = "Sélecteur de mode"
CONF_HEATING_TYPE = "Type de chauffage"
HEATING_TYPE_GAS = "gaz"
HEATING_TYPE_ELECTRIC = "electrique"

# Configuration des pièces
CONF_AREA = "area"
CONF_TEMP_EXT = "Température extérieur"
CONF_TEMP_INT = "Température intérieur"
CONF_CLIMATE = "Thermostat de la pièce"
CONF_DOOR_SENSOR = "Capteur de porte/fenêtre (optionnel)"
CONF_HEATER_ENTITY = "Vanne ou interrupteur du radiateur"
CONF_BOILER_ENTITY = "Chaudière (interrupteur ou climat, optionnel si chauffage électrique)"

# Plannings
CONF_MODE_PLANNINGS = "mode_plannings"
DEFAULT_PLANNING = "12:00|eco"

# Sécurité
SECURITY_STATE_OFF = "gris"
SECURITY_STATE_OK = "vert"
SECURITY_STATE_CRITICAL = "rouge"

# Groupes de pièces
CONF_GROUP_NAME = "Nom du groupe"
CONF_GROUP_AREAS = "Pièces du groupe"
CONF_GROUP_THRESHOLD = "Seuil de température (°C)"
DEFAULT_GROUP_THRESHOLD = 0.2

# Coefficient d'inertie thermique
COEFFICIENT_MIN = 10.0
COEFFICIENT_MAX = 60.0
COEFFICIENT_DEFAULT = 25.0

# Dérive de température
DERIVE_INTERVAL_MINUTES = 3

# Vannes (chauffage gaz)
VALVE_OPEN_TEMP = 29
VALVE_CLOSED_TEMP = 7

# Aération : différence d'humidité absolue int/ext, en g/m³
AERATION_SEUIL_RECOMMANDEE = 1.0
AERATION_SEUIL_POSSIBLE = 0.3

# Niveau d'humidité intérieure, en %
HUMIDITE_SEUIL_ELEVEE = 60
HUMIDITE_SEUIL_TRES_ELEVEE = 65
HUMIDITE_SEUIL_EXCESSIVE = 70

# Signal envoyé par le scheduler au capteur "chauffage actuel" d'une pièce.
# À formater avec l'entry_id de la pièce : SIGNAL_CURRENT_PRESET.format(entry_id)
SIGNAL_CURRENT_PRESET = f"{DOMAIN}_current_preset_{{}}"


def slugify_area(area_name: str) -> str:
    """Convert an area name into a safe entity-id part."""

    normalized = unicodedata.normalize("NFKD", area_name)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)

    return normalized.strip("_")
