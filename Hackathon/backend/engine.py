"""
engine.py -- the "agronomy brain" of Mazraa.

This file holds two things:

1. Two big lookup tables of PLACEHOLDER numbers:
   - CROPS:   what each crop likes (temperature / rainfall / soil pH ranges) and
              rough production numbers (yield, water need, price...).
   - SYSTEMS: how each farming system changes those numbers (a greenhouse yields
              more than an open field but costs more to build and to run).

2. Two functions that use those tables:
   - score_suitability(crop, site_conditions)                -> a number from 0 to 1
   - calculate_economics(crop, system, area, site_conditions) -> yield/water/energy/money

IMPORTANT FOR THE TEAM:
Every number below is a rough, made-up placeholder chosen so the demo works
end-to-end. Replace them with real, sourced values (FAO, local extension
services, your own research) before showing this to judges. All the numbers live
in the two tables at the top, so you never have to touch the maths underneath.
"""

import model  # our ML placeholder (see model.py)

# ---------------------------------------------------------------------------
# LANGUAGES
# ---------------------------------------------------------------------------
# The codes here must match the codes in frontend/i18n.js and in explain.py.
# Every crop and farming system carries its own translated name (look at the
# "names" entries in the tables below), so the interface can be read by farmers
# who do not read English.
SUPPORTED_LANGUAGES = ("en", "ar", "fr", "es", "sw", "hi", "ur")
DEFAULT_LANGUAGE = "en"


def normalize_language(code):
    """
    Turn whatever the browser sent into a language we actually support.

    -> "ar"        (supported, used as is)
    -> "ar-EG"     (region stripped)
    -> "pt-BR"     (not supported, falls back to English)
    """
    if not isinstance(code, str) or not code.strip():
        return DEFAULT_LANGUAGE
    short = code.strip().lower().replace("_", "-").split("-")[0]
    return short if short in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

# ---------------------------------------------------------------------------
# GLOBAL PLACEHOLDER PRICES
# ---------------------------------------------------------------------------
# Used to turn physical quantities (kWh, cubic metres) into money.
ENERGY_PRICE_USD_PER_KWH = 0.12   # USD per kWh of electricity
WATER_PRICE_USD_PER_M3 = 0.05     # USD per cubic metre of irrigation water
MARKETING_RATE = 0.12             # harvest + packing + transport + selling, as a
                                  # fraction of revenue
MAINTENANCE_RATE = 0.06           # yearly repairs, as a fraction of CapEx

# 1 hectare covered with 1 mm of water = 10 cubic metres.
M3_PER_HA_PER_MM = 10.0

# ---------------------------------------------------------------------------
# CROP TABLE  (PLACEHOLDER DATA -- replace with sourced numbers)
# ---------------------------------------------------------------------------
# For every crop we store:
#   temp_range   ideal average temperature in Celsius (low, high)
#   rain_range   ideal yearly rainfall in millimetres (low, high)
#   ph_range     ideal soil pH (low, high)
#   base_yield   tonnes per hectare per year in an OPEN FIELD with perfect weather
#   water_need   cubic metres of water per hectare per year (an open field crop
#                usually needs 400-700 mm, which is 4000-7000 m3/ha)
#   energy_need  kilowatt-hours per hectare per year (pumps, fans, basic tools)
#   price        farm-gate selling price, USD per tonne
#   seed_cost    seeds/nursery per hectare, USD
#   labour_cost  workers per hectare per year, USD
#   base_capex   money to set up an open field for this crop, USD per hectare
#                (drip irrigation, plastic mulch, trellising...)
#   system_fit   how naturally this crop fits each system, 0 to 1.
#                e.g. wheat in a vertical farm scores 0.45: it grows, but the
#                economics of putting a low-value grain in an expensive tower
#                are poor.
CROPS = {
    "tomato": {
        "name": "Tomato",  # English name, used when there is no translation
        "names": {
            "ar": "الطماطم", "fr": "Tomate", "es": "Tomate",
            "sw": "Nyanya", "hi": "टमाटर", "ur": "ٹماٹر",
        },
        "temp_range": (18.0, 27.0),
        "rain_range": (400.0, 1200.0),
        "ph_range": (6.0, 6.8),
        "base_yield": 55.0,
        "water_need": 6000.0,
        "energy_need": 900.0,
        "price": 700.0,
        "seed_cost": 1500.0,
        "labour_cost": 6000.0,
        "base_capex": 8000.0,
        "system_fit": {
            "open_field": 0.95,
            "greenhouse": 1.00,
            "hydroponic": 0.95,
            "vertical": 0.80,
        },
    },
    "lettuce": {
        "name": "Lettuce",
        "names": {
            "ar": "الخس", "fr": "Laitue", "es": "Lechuga",
            "sw": "Saladi", "hi": "सलाद पत्ता", "ur": "سلاد پتہ",
        },
        "temp_range": (12.0, 22.0),
        "rain_range": (300.0, 900.0),
        "ph_range": (6.0, 7.0),
        "base_yield": 25.0,
        "water_need": 3500.0,
        "energy_need": 600.0,
        "price": 1500.0,
        "seed_cost": 800.0,
        "labour_cost": 2600.0,
        "base_capex": 5000.0,
        "system_fit": {
            "open_field": 0.80,
            "greenhouse": 1.00,
            "hydroponic": 1.00,
            "vertical": 1.00,
        },
    },
    "cucumber": {
        "name": "Cucumber",
        "names": {
            "ar": "الخيار", "fr": "Concombre", "es": "Pepino",
            "sw": "Tango", "hi": "खीरा", "ur": "کھیرا",
        },
        "temp_range": (20.0, 30.0),
        "rain_range": (400.0, 1100.0),
        "ph_range": (6.0, 7.0),
        "base_yield": 45.0,
        "water_need": 5200.0,
        "energy_need": 850.0,
        "price": 600.0,
        "seed_cost": 1100.0,
        "labour_cost": 3000.0,
        "base_capex": 7000.0,
        "system_fit": {
            "open_field": 0.90,
            "greenhouse": 1.00,
            "hydroponic": 0.90,
            "vertical": 0.75,
        },
    },
    "wheat": {
        "name": "Wheat",
        "names": {
            "ar": "القمح", "fr": "Blé", "es": "Trigo",
            "sw": "Ngano", "hi": "गेहूँ", "ur": "گندم",
        },
        "temp_range": (10.0, 24.0),
        "rain_range": (300.0, 800.0),
        "ph_range": (6.0, 7.5),
        "base_yield": 4.5,
        "water_need": 3500.0,
        "energy_need": 700.0,
        "price": 320.0,
        "seed_cost": 200.0,
        "labour_cost": 600.0,
        "base_capex": 1200.0,
        "system_fit": {
            "open_field": 1.00,
            "greenhouse": 0.60,
            "hydroponic": 0.50,
            "vertical": 0.45,
        },
    },
    "strawberry": {
        "name": "Strawberry",
        "names": {
            "ar": "الفراولة", "fr": "Fraise", "es": "Fresa",
            "sw": "Stroberi", "hi": "स्ट्रॉबेरी", "ur": "اسٹرابیری",
        },
        "temp_range": (10.0, 24.0),
        "rain_range": (400.0, 1000.0),
        "ph_range": (5.5, 6.5),
        "base_yield": 18.0,
        "water_need": 4000.0,
        "energy_need": 700.0,
        "price": 2200.0,
        "seed_cost": 3000.0,
        "labour_cost": 5000.0,
        "base_capex": 9000.0,
        "system_fit": {
            "open_field": 0.85,
            "greenhouse": 1.00,
            "hydroponic": 0.90,
            "vertical": 0.85,
        },
    },
}

# ---------------------------------------------------------------------------
# SYSTEM TABLE  (PLACEHOLDER DATA -- replace with sourced numbers)
# ---------------------------------------------------------------------------
# For every farming system we store:
#   label          pretty name for the UI
#   yield_mult     how much more it produces per hectare than an open field
#   water_mult     how much water it uses compared with an open field
#                  (0.10 = 10% as much, because hydroponics recirculate water)
#   energy_mult    how much electricity it uses compared with an open field
#                  (lights, pumps, heating, cooling, ventilation)
#   capex_per_ha   construction cost, USD per hectare of ground footprint
#   labour_mult    how much more skilled labour it needs
#   climate_control 0 = fully at the mercy of the weather,
#                    1 = everything happens indoors, weather barely matters
#   control_quality  how good the controlled environment is (only used when
#                    climate_control is above 0)
#
# NOTE: "per hectare" for a vertical farm means per hectare of GROUND footprint
# (a tower stacks many growing layers on one hectare of land), which is why the
# yield multiplier is so high.
SYSTEMS = {
    "open_field": {
        "label": "Open Field",
        "names": {
            "ar": "زراعة مكشوفة", "fr": "Plein champ", "es": "Campo abierto",
            "sw": "Shamba la wazi", "hi": "खुला खेत", "ur": "کھلا کھیت",
        },
        "yield_mult": 1.0,
        "water_mult": 1.0,
        "energy_mult": 1.0,
        "capex_per_ha": 2000.0,
        "labour_mult": 1.0,
        "climate_control": 0.0,
        "control_quality": 0.0,
    },
    "greenhouse": {
        "label": "Greenhouse",
        "names": {
            "ar": "بيت محمي (دفيئة)", "fr": "Serre", "es": "Invernadero",
            "sw": "Nyumba ya kijani", "hi": "ग्रीनहाउस", "ur": "گرین ہاؤس",
        },
        "yield_mult": 4.0,
        "water_mult": 0.45,
        "energy_mult": 3.5,
        "capex_per_ha": 120000.0,
        "labour_mult": 1.6,
        "climate_control": 0.75,
        "control_quality": 0.85,
    },
    "hydroponic": {
        "label": "Hydroponic",
        "names": {
            "ar": "زراعة مائية (هيدروبونيك)", "fr": "Hydroponie", "es": "Hidroponía",
            "sw": "Kilimo cha maji", "hi": "हाइड्रोपोनिक", "ur": "ہائیڈروپونک",
        },
        "yield_mult": 6.0,
        "water_mult": 0.12,
        "energy_mult": 8.0,
        "capex_per_ha": 250000.0,
        "labour_mult": 2.0,
        "climate_control": 0.90,
        "control_quality": 0.90,
    },
    "vertical": {
        "label": "Vertical Farm",
        "names": {
            "ar": "زراعة رأسية", "fr": "Ferme verticale", "es": "Granja vertical",
            "sw": "Shamba la wima", "hi": "वर्टिकल फ़ार्म", "ur": "عمودی فارم",
        },
        "yield_mult": 22.0,
        "water_mult": 0.06,
        "energy_mult": 28.0,
        "capex_per_ha": 900000.0,
        "labour_mult": 2.6,
        "climate_control": 1.0,
        "control_quality": 0.90,
    },
}


# ---------------------------------------------------------------------------
# NAMES (translated)
# ---------------------------------------------------------------------------
def crop_name(crop_key, language=DEFAULT_LANGUAGE):
    """"Tomato" / "الطماطم" / "Nyanya" ... for a key from CROPS."""
    crop = CROPS.get(crop_key)
    if crop is None:
        return str(crop_key)
    return crop.get("names", {}).get(language) or crop["name"]


def system_name(system_key, language=DEFAULT_LANGUAGE):
    """"Greenhouse" / "دفيئة" / "Nyumba ya kijani" ... for a key from SYSTEMS."""
    system = SYSTEMS.get(system_key)
    if system is None:
        return str(system_key)
    return system.get("names", {}).get(language) or system["label"]


# ---------------------------------------------------------------------------
# SMALL HELPERS
# ---------------------------------------------------------------------------
def _resolve_crop(crop):
    """Accept either a crop dictionary or a crop key like "tomato"."""
    if isinstance(crop, str):
        if crop not in CROPS:
            raise KeyError("Unknown crop '%s'. Known crops: %s"
                           % (crop, ", ".join(sorted(CROPS))))
        return CROPS[crop]
    return crop


def _resolve_system(system):
    """Accept either a system dictionary or a system key like "greenhouse"."""
    if isinstance(system, str):
        if system not in SYSTEMS:
            raise KeyError("Unknown system '%s'. Known systems: %s"
                           % (system, ", ".join(sorted(SYSTEMS))))
        return SYSTEMS[system]
    return system


def _range_score(value, low, high, tolerance=0.5):
    """
    Score one measurement against an ideal range, from 0.0 to 1.0.

    - Inside [low, high]  -> 1.0 (perfect)
    - Outside the range   -> falls off linearly and hits 0.0 once you are
                             `tolerance` times the width of the range away.

    Example with a tomato temperature range of 18-27 C and tolerance 0.5:
    the range is 9 C wide, so the score reaches 0 at 18 - 4.5 = 13.5 C and at
    27 + 4.5 = 31.5 C. A value of 15 C scores roughly 0.33.
    """
    if value is None:
        return 0.5  # we do not know, so assume "average"
    if low <= value <= high:
        return 1.0
    width = (high - low) if (high - low) > 0 else 1.0
    distance = (low - value) if value < low else (value - high)
    return max(0.0, 1.0 - (distance / (width * tolerance)))


def _get(site_conditions, *keys, default=None):
    """Read the first key that exists in a dictionary."""
    for key in keys:
        if key in site_conditions and site_conditions[key] is not None:
            return site_conditions[key]
    return default


# ---------------------------------------------------------------------------
# 1) SUITABILITY
# ---------------------------------------------------------------------------
def score_suitability(crop, site_conditions):
    """
    How well does `crop` fit this place (and this farming system)?

    Parameters
    ----------
    crop : str or dict
        A key from CROPS, or the crop dictionary itself.
    site_conditions : dict
        Climate + soil information, e.g. what data.get_site_info() returns:
        {"avg_temp_c": 24.0, "rainfall_mm": 600.0, "solar_radiation": 5.4,
         "soil_ph": 6.8, "lat": 24.7, "lon": 46.7}
        It may also contain "system" (a key from SYSTEMS). If you pass a system,
        the score takes the controlled environment into account.

    Returns
    -------
    float
        A score between 0.0 (terrible) and 1.0 (ideal).
    """
    crop = _resolve_crop(crop)
    system_key = _get(site_conditions, "system", default="open_field")
    system = SYSTEMS.get(system_key, SYSTEMS["open_field"])

    temperature = _get(site_conditions, "avg_temp_c", "temperature")
    rainfall = _get(site_conditions, "rainfall_mm", "rainfall")
    soil_ph = _get(site_conditions, "soil_ph", "ph")

    # --- Step 1: score the three natural conditions, 0 to 1 each -------------
    temp_score = _range_score(temperature, crop["temp_range"][0], crop["temp_range"][1])
    rain_score = _range_score(rainfall, crop["rain_range"][0], crop["rain_range"][1])
    ph_score = _range_score(soil_ph, crop["ph_range"][0], crop["ph_range"][1])

    # Temperature matters most, then water, then soil acidity.
    climate_score = (0.45 * temp_score) + (0.35 * rain_score) + (0.20 * ph_score)

    # --- Step 2: let the farming system hide some of the weather -------------
    # In an open field the weather is everything. Inside a greenhouse/hydroponic
    # setup/some vertical farms we control temperature and water, so the score is
    # pulled towards how good that controlled environment normally is.
    control = system["climate_control"]
    if control > 0:
        climate_score = ((1.0 - control) * climate_score) + (control * system["control_quality"])

    # --- Step 3: multiply by how naturally this crop fits the system ---------
    # e.g. wheat in a vertical farm -> 0.45, so it can never score highly.
    fit = crop["system_fit"].get(system_key, 0.7)

    return round(max(0.0, min(1.0, climate_score * fit)), 4)


# ---------------------------------------------------------------------------
# 2) ECONOMICS
# ---------------------------------------------------------------------------
def calculate_economics(crop, system, area, site_conditions):
    """
    Estimate what one crop/system option looks like on a given piece of land.

    Parameters
    ----------
    crop : str or dict   -- a key from CROPS, or the crop dictionary
    system : str or dict -- a key from SYSTEMS, or the system dictionary
    area : float         -- land area in hectares
    site_conditions : dict -- same shape as for score_suitability()

    Returns
    -------
    dict with these keys:
        yield_per_ha    tonnes per hectare per year
        yield_tonnes    total tonnes per year for the whole area
        water_m3        cubic metres of water per year
        energy_kwh      kilowatt-hours per year
        capex_usd       one-off build/setup cost
        opex_usd        running cost per year
        revenue_usd     money in per year
        annual_profit_usd  revenue - opex
        payback_years   capex / annual_profit, or None if it never pays back
    """
    crop = _resolve_crop(crop)
    system = _resolve_system(system)
    area = float(area)
    site_conditions = site_conditions or {}

    # The system key ("greenhouse", ...) is needed to look up this crop's fit.
    system_key = _system_key(system)

    # How suitable is this combination? 0 to 1. A poor site yields less.
    conditions_with_system = dict(site_conditions)
    conditions_with_system["system"] = system_key
    suitability = score_suitability(crop, conditions_with_system)

    # ------------------------------------------------------------------
    # YIELD
    # ------------------------------------------------------------------
    # Placeholder formula:
    #   crop's perfect-weather yield
    #   x what the system multiplies it by
    #   x a site penalty (a bad site produces 55% of the ideal at worst)
    #   x a correction from the ML placeholder in model.py
    site_factor = 0.55 + (0.45 * suitability)
    ml_factor = model.predict_yield_ml({
        "crop": crop["name"],
        "system": system_key,
        "area": area,
        "suitability": suitability,
        "avg_temp_c": _get(site_conditions, "avg_temp_c", "temperature"),
        "rainfall_mm": _get(site_conditions, "rainfall_mm", "rainfall"),
        "soil_ph": _get(site_conditions, "soil_ph", "ph"),
    })

    yield_per_ha = crop["base_yield"] * system["yield_mult"] * site_factor * ml_factor
    yield_tonnes = yield_per_ha * area

    # ------------------------------------------------------------------
    # WATER
    # ------------------------------------------------------------------
    # An open field gets free rain, so it only has to irrigate the shortfall.
    # We assume 70% of the yearly rain actually reaches the plant roots.
    # Inside a greenhouse/hydroponic/vertical farm rain never reaches the plants,
    # so the full demand has to be supplied -- but those systems recirculate
    # water, which is what water_mult captures.
    crop_water_mm = crop["water_need"] / M3_PER_HA_PER_MM   # m3/ha -> mm
    rainfall_mm = _get(site_conditions, "rainfall_mm", "rainfall", default=0.0) or 0.0
    useful_rain_mm = 0.7 * rainfall_mm
    if system_key == "open_field":
        irrigation_mm = max(0.15 * crop_water_mm, crop_water_mm - useful_rain_mm)
    else:
        irrigation_mm = crop_water_mm
    water_m3 = irrigation_mm * M3_PER_HA_PER_MM * system["water_mult"] * area

    # ------------------------------------------------------------------
    # ENERGY
    # ------------------------------------------------------------------
    energy_kwh = crop["energy_need"] * system["energy_mult"] * area

    # ------------------------------------------------------------------
    # CAPEX (one-off investment)
    # ------------------------------------------------------------------
    capex = (crop["base_capex"] + system["capex_per_ha"]) * area

    # ------------------------------------------------------------------
    # OPEX (running cost, per year)
    # ------------------------------------------------------------------
    seed_cost = crop["seed_cost"] * area
    labour_cost = crop["labour_cost"] * area * system["labour_mult"]
    energy_cost = energy_kwh * ENERGY_PRICE_USD_PER_KWH
    water_cost = water_m3 * WATER_PRICE_USD_PER_M3
    maintenance_cost = capex * MAINTENANCE_RATE

    # ------------------------------------------------------------------
    # REVENUE and PROFIT
    # ------------------------------------------------------------------
    revenue = yield_tonnes * crop["price"]
    marketing_cost = revenue * MARKETING_RATE

    opex = (seed_cost + labour_cost + energy_cost + water_cost
            + maintenance_cost + marketing_cost)
    annual_profit = revenue - opex

    # ------------------------------------------------------------------
    # PAYBACK
    # ------------------------------------------------------------------
    # How many years of profit it takes to earn back the investment.
    if annual_profit > 0:
        payback_years = round(capex / annual_profit, 2)
    else:
        payback_years = None  # this option loses money every year

    return {
        "yield_per_ha": round(yield_per_ha, 2),
        "yield_tonnes": round(yield_tonnes, 2),
        "water_m3": round(water_m3, 1),
        "energy_kwh": round(energy_kwh, 1),
        "capex_usd": round(capex, 2),
        "opex_usd": round(opex, 2),
        "revenue_usd": round(revenue, 2),
        "annual_profit_usd": round(annual_profit, 2),
        "payback_years": payback_years,
    }


def _system_key(system):
    """Find which SYSTEMS key a system dictionary belongs to."""
    for key, value in SYSTEMS.items():
        if value is system:
            return key
    # Fall back to matching by label, so a hand-built dict still works.
    for key, value in SYSTEMS.items():
        if value.get("label") == system.get("label"):
            return key
    return "open_field"
