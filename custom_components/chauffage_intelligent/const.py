"""Les constantes pour l'intégration Chauffage Intelligent. Permet de déterminer les noms et valeurs pour les autres fichiers"""

import re
import unicodedata

"""Nom de l'intégration"""
DOMAIN = "chauffage_intelligent"

"""Types de configuration"""
ENTRY_TYPE = "entry_type"
ENTRY_TYPE_CENTRAL = "central"
ENTRY_TYPE_ROOM = "room"
ENTRY_TYPE_GROUP = "group"
"""id de la config centrale"""
CENTRAL_UNIQUE_ID = "chauffage_intelligent_central"

"""Types d'entrées pour la config centrale"""
CONF_MODE_SELECTOR = "Sélecteur de mode"
CONF_HEATING_TYPE = "Type de chauffage"
HEATING_TYPE_GAS = "gaz"
HEATING_TYPE_ELECTRIC = "electrique"

"""Types d'entrées pour la config des pièces"""
CONF_AREA = "area"
CONF_TEMP_EXT = "Température extérieur"
CONF_TEMP_INT = "Température intérieur"
CONF_CLIMATE = "Thermostat de la pièce"
CONF_DOOR_SENSOR = "Capteur de porte/fenêtre (optionnel)"
CONF_HEATER_ENTITY = "Vanne ou interrupteur du radiateur"
CONF_BOILER_ENTITY = "Chaudière (interrupteur ou climat, optionnel si chauffage électrique)"

"""Écarts pièce froide / pièce chaude (par rapport à la consigne, en °C)"""
CONF_COLD_OFFSET = "Écart pièce froide (°C sous la consigne)"
CONF_HOT_OFFSET = "Écart pièce chaude (°C au-dessus de la consigne)"
DEFAULT_COLD_OFFSET = 1.0
DEFAULT_HOT_OFFSET = 0.5

"""Suivi « la température ne monte pas » (réglable par pièce, dans l'UI de la pièce)"""
CONF_NO_RISE_DELAY = "Délai sans montée de température (min)"
CONF_NO_RISE_DELTA = "Gain minimal de température (°C)"
DEFAULT_NO_RISE_DELAY = 10
DEFAULT_NO_RISE_DELTA = 0.2
"""Défaut aussi déclaré si la chauffe dure plus de (multiple x temps de chauffe estimé) + marge.
La marge fixe évite les faux défauts sur les petits écarts de température, où
l'estimation est très courte alors que la vanne et le capteur ont un retard fixe."""
NO_RISE_ESTIMATE_FACTOR = 1.5
NO_RISE_ESTIMATE_MARGIN_MINUTES = 10
FAULT_REASON_NO_RISE = "no_rise"
FAULT_REASON_TOO_LONG = "too_long"

"""Mode boost (optionnel, activable par pièce)"""
CONF_BOOST_ENABLED = "Activer le mode boost"
CONF_BOOST_PRESET = "Preset du mode boost"
CONF_BOOST_DURATION = "Durée du mode boost (min)"
DEFAULT_BOOST_PRESET = "comfort"
DEFAULT_BOOST_DURATION = 30

"""Chaudière en chauffe alors qu'aucun thermostat ne demande de chauffe (alerte centrale)"""
BOILER_NO_DEMAND_GRACE_MINUTES = 5

"""Config centrale (optionnel) : capteur extérieur (mini/maxi réels du jour) et entité météo (mini/maxi prévus)"""
CONF_TEMP_EXT_CENTRAL = "Capteur de température extérieure du jour (optionnel)"
CONF_WEATHER_ENTITY = "Entité météo pour les prévisions (optionnel)"
FORECAST_REFRESH_MINUTES = 30
FORECAST_RETRY_MINUTES = 5

"""Types d'entrées pour les planning"""
CONF_MODE_PLANNINGS = "mode_plannings"
DEFAULT_PLANNING = "12:00|eco"

"""Signal interne : preset courant publié par le scheduler (formaté avec l'entry_id)"""
SIGNAL_CURRENT_PRESET = "chauffage_intelligent_current_preset_{}"

"""Signal interne : défaut verrouillé d'une pièce modifié (formaté avec l'entry_id)"""
SIGNAL_ROOM_FAULT = "chauffage_intelligent_room_fault_{}"

"""Signal interne : boost d'une pièce modifié (formaté avec l'entry_id)"""
SIGNAL_ROOM_BOOST = "chauffage_intelligent_room_boost_{}"

"""Types d'entrées pour la config sécurité"""
SECURITY_STATE_OFF = "gris"
SECURITY_STATE_OK = "vert"
SECURITY_STATE_WARNING = "orange"
SECURITY_STATE_CRITICAL = "rouge"

"""Types d'entrées pour la config des groupe"""
CONF_GROUP_NAME = "Nom du groupe"
CONF_GROUP_AREAS = "Pièces du groupe"
CONF_GROUP_THRESHOLD = "Seuil de température (°C)"
DEFAULT_GROUP_THRESHOLD = 0.2

"""Types d'entrées pour le coef"""
COEFFICIENT_MIN = 10.0
COEFFICIENT_MAX = 60.0
COEFFICIENT_DEFAULT = 25.0

"""Types d'entrées pour la derive"""
DERIVE_INTERVAL_MINUTES = 3

"""Types d'entrées pour la config des valves"""
VALVE_OPEN_TEMP = 29
VALVE_CLOSED_TEMP = 7

"""Seuils pour l'aération (différence d'humidité absolue int/ext, en g/m³)"""
AERATION_SEUIL_RECOMMANDEE = 1.0
AERATION_SEUIL_POSSIBLE = 0.3

"""Seuils pour le niveau d'humidité intérieure (en %), réglables par pièce.
Les valeurs HUMIDITE_SEUIL_* sont les valeurs par défaut."""
CONF_HUM_HIGH = "Seuil humidité élevée (%)"
CONF_HUM_VERY_HIGH = "Seuil humidité très élevée (%)"
CONF_HUM_EXCESSIVE = "Seuil humidité excessive (%)"
HUMIDITE_SEUIL_ELEVEE = 60
HUMIDITE_SEUIL_TRES_ELEVEE = 65
HUMIDITE_SEUIL_EXCESSIVE = 70


"""Thermostat intégré : une entité climate créée par pièce, réglée par pièce"""
CONF_PRESET_TEMPS = "preset_temperatures"
CONF_COLD_TOLERANCE = "Tolérance basse du thermostat (°C sous la consigne)"
CONF_HOT_TOLERANCE = "Tolérance haute du thermostat (°C au-dessus de la consigne)"
CONF_MIN_CYCLE = "Durée minimale entre deux changements chauffe/repos (min)"
DEFAULT_COLD_TOLERANCE = 0.3
DEFAULT_HOT_TOLERANCE = 0.0
DEFAULT_MIN_CYCLE = 2
THERMOSTAT_MIN_TEMP = 5.0
THERMOSTAT_MAX_TEMP = 30.0
THERMOSTAT_REFRESH_SECONDS = 30
"""Température d'un preset non réglé dans la pièce : valeur du nom connu, sinon valeur par défaut"""
DEFAULT_PRESET_TEMP = 18.0
DEFAULT_PRESET_TEMPS = {
    "comfort": 19.0,
    "confort": 19.0,
    "home": 19.0,
    "activity": 20.0,
    "boost": 21.0,
    "eco": 17.0,
    "sleep": 17.0,
    "nuit": 17.0,
    "away": 15.0,
    "absent": 15.0,
    "frost": 7.0,
    "hors_gel": 7.0,
    "hors gel": 7.0,
}

"""Apprentissage du coefficient : poids de la mesure = départ / (1 + mesures / décroissance), plancher"""
LEARNING_START_WEIGHT = 0.3
LEARNING_MIN_WEIGHT = 0.02
LEARNING_DECAY_SAMPLES = 10


def climate_entity_id(area_slug: str) -> str:
    """Entity id du thermostat intégré d'une pièce."""

    return f"climate.thermostat_{area_slug}"


"""formule pour uniformiser le code"""
def slugify_area(area_name: str) -> str:
    """Convert an area name into a safe entity-id part."""

    normalized = unicodedata.normalize("NFKD", area_name)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)

    return normalized.strip("_")
