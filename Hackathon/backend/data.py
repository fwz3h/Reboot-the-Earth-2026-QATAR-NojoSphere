"""
data.py -- fetches real climate and soil data for a point on the map.

Two free public APIs are used (no API key needed):

1. NASA POWER -- average temperature, rainfall and solar radiation.
   https://power.larc.nasa.gov/api/temporal/climatology/point
2. SoilGrids (ISRIC) -- soil pH in the top 5 cm of the ground.
   https://rest.isric.org/soilgrids/v2.0/properties/query

Because both APIs are slow (a few seconds each) and the map data around a spot
does not change, every successful answer is cached in a small JSON file
(site_cache.json) keyed by the coordinates rounded to 2 decimal places
(about 1.1 km). Asking for the same village twice returns instantly.

If there is no internet, or an API is down, we return PLACEHOLDER values instead
of crashing -- the app must keep working during a demo. Such answers are marked
with "source": "fallback" and are NOT cached, so a real answer can still come in
later.

THIRD SOURCE: THE DOWNLOADED OFFLINE DATABASE
backend/fetch_offline_data.py can download both datasets ONCE, in bulk (run it
with internet; afterwards the laptop needs none):

    offline_db/power_grid.json   NASA POWER climate on a 0.625 degree grid
    offline_db/soil_grid.json    SoilGrids pH on a 2.5 degree grid over land
    offline_db/countries.geojson world borders -> which country is a pin in
    offline_db/faostat.json      FAOSTAT producer prices + yields per crop

get_site_info() therefore tries sources in this order:

    1. site_cache.json  -- exact answer saved by an earlier live request
    2. offline_db/      -- the downloaded grids (nearest cell, real data)
    3. the live APIs    -- only for values 1 and 2 do not have
    4. PLACEHOLDER_SITE -- last resort, so the app never crashes

so a laptop with the downloaded files answers every request with real numbers
and never needs the internet. The "source" field is one of:
"cache", "offline_db", "live", "mixed" (some placeholder values) or
"fallback" (all placeholder).
"""

import json
import math
import os

try:
    import requests  # listed in requirements.txt
except ImportError:  # pragma: no cover - only happens before pip install
    requests = None

# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------
# Folder of this file, so the cache file always sits next to the code no matter
# which folder you started Python from.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "site_cache.json")

# Round coordinates to this many decimals before using them as a cache key.
CACHE_PRECISION = 2

# How long to wait for each API before giving up, in seconds. SoilGrids can be
# slow, so this is generous. Change it without editing code by setting the
# environment variable MAZRAA_HTTP_TIMEOUT, e.g. MAZRAA_HTTP_TIMEOUT=30
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("MAZRAA_HTTP_TIMEOUT", "20"))

# Set the environment variable MAZRAA_OFFLINE=1 to never touch the internet
# (useful when demoing without WiFi). The downloaded offline_db/ files and the
# cache are still used, so results stay real; only the live API calls are
# skipped and placeholders appear only when no local value exists at all.
ENABLE_NETWORK = os.environ.get("MAZRAA_OFFLINE") != "1"

# Set MAZRAA_NO_DB=1 to ignore the downloaded offline database (debugging).
ENABLE_OFFLINE_DB = os.environ.get("MAZRAA_NO_DB") != "1"

# The downloaded offline database, written by fetch_offline_data.py.
OFFLINE_DB_DIR = os.path.join(BASE_DIR, "offline_db")
POWER_GRID_FILE = os.path.join(OFFLINE_DB_DIR, "power_grid.json")
SOIL_GRID_FILE = os.path.join(OFFLINE_DB_DIR, "soil_grid.json")
FAOSTAT_FILE = os.path.join(OFFLINE_DB_DIR, "faostat.json")
COUNTRIES_FILE = os.path.join(OFFLINE_DB_DIR, "countries.geojson")

# Grid steps in degrees -- must match what fetch_offline_data.py downloads.
POWER_GRID_STEP = 0.625   # NASA POWER's regional grid resolution
SOIL_GRID_STEP = 2.5      # the SoilGrids sampling grid we download
# How many rings of neighbouring cells we search when the exact cell is
# missing (ring 0 = the cell the point falls in, ring 1 = its 8 neighbours).
# The power grid is fine (4 rings = 2.5 degrees); soil cells are coarse, so we
# allow 2 rings (5 degrees) before giving up and trying the live APIs.
POWER_MAX_RINGS = 4
SOIL_MAX_RINGS = 2

NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"
SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

# Values used when the APIs cannot be reached. Rough world averages.
PLACEHOLDER_SITE = {
    "avg_temp_c": 22.0,
    "rainfall_mm": 600.0,
    "solar_radiation": 5.0,
    "soil_ph": 6.5,
}


# ---------------------------------------------------------------------------
# PUBLIC FUNCTION
# ---------------------------------------------------------------------------
def get_site_info(lat, lon, force_live=False):
    """
    Return the site conditions at (lat, lon) as a dictionary:

        {
          "lat": 24.71,
          "lon": 46.68,
          "avg_temp_c": 27.4,      # average yearly air temperature, Celsius
          "rainfall_mm": 112.0,    # total yearly rainfall, millimetres
          "solar_radiation": 6.31, # average daily sun, kWh per square metre
          "soil_ph": 7.2,          # soil pH (0-14, 7 is neutral)
          "source": "offline_db",  # "cache" | "offline_db" | "live"
                                   # | "mixed" | "fallback"
          "notes": [...]           # anything worth saying, for the UI
        }

    Sources are tried in this order:

        1. site_cache.json  -- exact answer saved by an earlier live request
        2. offline_db/      -- the downloaded grids (nearest cell, real data)
        3. the live APIs    -- only for values 1 and 2 do not have
        4. PLACEHOLDER_SITE -- last resort, so the app never crashes

    force_live=True (used by fetch_offline_data.py) skips 1 and 2 and asks the
    APIs directly, so the demo packs always contain exact point answers.

    This function never raises an exception: it always returns a usable
    dictionary, falling back to placeholder values when nothing else works.
    """
    lat = float(lat)
    lon = float(lon)
    cache_key = "%s,%s" % (round(lat, CACHE_PRECISION), round(lon, CACHE_PRECISION))

    # --- 1) Is this spot already in the cache? -----------------------------
    cache = None
    if not force_live:
        cache = _load_cache()
        if cache_key in cache:
            cached = dict(cache[cache_key])
            cached["lat"] = lat
            cached["lon"] = lon
            cached["source"] = "cache"
            cached["notes"] = ["Loaded from the local cache (site_cache.json)."]
            return cached

    values = {}    # avg_temp_c / rainfall_mm / solar_radiation / soil_ph
    sources = {}   # same keys -> "db" | "live" | "placeholder"
    notes = []

    # --- 2) The downloaded offline database (real data, no internet) ------
    if not force_live and ENABLE_OFFLINE_DB:
        climate, climate_km = _offline_climate(lat, lon)
        if climate:
            for key, value in climate.items():
                if value is not None:
                    values[key] = value
                    sources[key] = "db"
            if climate_km is not None:
                notes.append("Climate from offline_db/power_grid.json (nearest "
                             "cell %d km away)." % int(round(climate_km)))

        soil_ph, soil_km = _offline_soil_ph(lat, lon)
        if soil_ph is not None:
            values["soil_ph"] = soil_ph
            sources["soil_ph"] = "db"
            if soil_km is not None:
                notes.append("Soil pH from offline_db/soil_grid.json (nearest "
                             "cell %d km away)." % int(round(soil_km)))

    # --- 3) Ask the live APIs for whatever is still missing ---------------
    needs_climate = any(key not in values
                        for key in ("avg_temp_c", "rainfall_mm", "solar_radiation"))
    needs_soil = "soil_ph" not in values
    # force_live wins over MAZRAA_OFFLINE: the fetch script must hit the APIs.
    wants_live = ENABLE_NETWORK or force_live

    if (needs_climate or needs_soil) and wants_live and requests is not None:
        if needs_climate:
            try:
                climate = _fetch_nasa_power(lat, lon)
                for key, value in climate.items():
                    if value is not None:
                        values[key] = value
                        sources[key] = "live"
            except Exception as exc:
                notes.append("NASA POWER request failed (%s), using placeholder "
                             "climate." % _short_error(exc))

        if needs_soil:
            try:
                soil_ph = _fetch_soilgrids_ph(lat, lon)
                if soil_ph is not None:
                    values["soil_ph"] = soil_ph
                    sources["soil_ph"] = "live"
                else:
                    notes.append("SoilGrids returned no pH value for this point.")
            except Exception as exc:
                notes.append("SoilGrids request failed (%s), using placeholder "
                             "soil pH." % _short_error(exc))
    elif needs_climate or needs_soil:
        notes.append("No internet and no offline value for this point, so "
                     "placeholder values are used for the rest.")

    # --- 4) Placeholder values for anything still missing -----------------
    for key, value in PLACEHOLDER_SITE.items():
        if values.get(key) is None:
            values[key] = value
            sources[key] = "placeholder"

    # --- 5) One word that says where the numbers came from ----------------
    used = set(sources.values())
    has_placeholder = "placeholder" in used
    if has_placeholder and len(used) == 1:
        source = "fallback"
    elif has_placeholder:
        source = "mixed"
    elif "db" in used:
        source = "offline_db"   # downloaded grid (+ live/cache), all real
    elif used == {"live"}:
        source = "live"
    else:
        source = "mixed"

    if has_placeholder:
        notes.append("Placeholder values are NOT cached, so a later request can "
                     "try the real sources again.")

    result = {"lat": lat, "lon": lon}
    result.update(values)
    result["source"] = source
    result["notes"] = notes

    # --- 6) Remember live answers so we do not ask again ------------------
    # Only fully-real answers are cached: a placeholder must never become a
    # "cached fact". Failures are not cached either, so when the internet
    # comes back the next request tries the APIs again.
    live_keys = [key for key, where in sources.items() if where == "live"]
    if live_keys and not has_placeholder:
        if cache is None:
            cache = _load_cache()
        cache[cache_key] = result
        _save_cache(cache)

    return result


# ---------------------------------------------------------------------------
# THE DOWNLOADED OFFLINE DATABASE (backend/offline_db/)
# ---------------------------------------------------------------------------
# Parsed files are remembered here so a running server reads each JSON file
# from disk only once. Missing files are re-checked on every request, so the
# fetch script can fill offline_db/ while the server is already up (restart
# the server after re-running the fetch script to pick up *replaced* files).
_JSON_CACHE = {}
_COUNTRY_SHAPES = {"loaded": False, "shapes": None}


def cell_key(lat, lon, step):
    """
    How a grid cell is written into the offline database files.

        cell_key(52.5, 50.0, 2.5)      -> "52.5,50.0"
        cell_key(20.0, 40.625, 0.625)  -> "20.000,40.625"

    fetch_offline_data.py imports this, so keys written by the downloader and
    keys computed by the lookups below can never drift apart.
    """
    decimals = 1 if step >= 1 else 3
    pattern = "%." + str(decimals) + "f,%." + str(decimals) + "f"
    return pattern % (lat, lon)


def _read_json_cached(path):
    """Parse a JSON file once and remember it, or return None if missing/broken."""
    if path in _JSON_CACHE:
        return _JSON_CACHE[path]
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None   # not cached: a half-written file should be retried later
    _JSON_CACHE[path] = payload
    return payload


def _lookup_cell(cells, step, lat, lon, max_rings):
    """
    Find the nearest stored grid cell around (lat, lon).

    Cells sit on a regular lattice of `step` degrees, so the point is
    quantised onto that lattice and searched outward ring by ring (ring 0 =
    the cell the point falls in, ring 1 = its 8 neighbours, ...) up to
    `max_rings`. Cells stored as None ("queried, no data there") are skipped.

    Returns (key, cell_lat, cell_lon, distance_km) or None.
    """
    base_lat = round(lat / step) * step
    base_lon = round(lon / step) * step
    for ring in range(max_rings + 1):
        best = None
        for i in range(-ring, ring + 1):
            for j in range(-ring, ring + 1):
                if ring > 0 and max(abs(i), abs(j)) != ring:
                    continue   # only the outermost cells belong to this ring
                cell_lat = base_lat + i * step
                cell_lon = base_lon + j * step
                key = cell_key(cell_lat, cell_lon, step)
                if cells.get(key) is None:
                    continue
                km = _distance_km(lat, lon, cell_lat, cell_lon)
                if best is None or km < best[3]:
                    best = (key, cell_lat, cell_lon, km)
        if best:
            return best
    return None


def _distance_km(lat1, lon1, lat2, lon2):
    """Straight-line distance between two points, in kilometres."""
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2.0) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lon / 2.0) ** 2)
    return 2.0 * radius * math.asin(math.sqrt(a))


def _offline_climate(lat, lon):
    """
    Climate for this point from offline_db/power_grid.json.

    Returns (dict, distance_to_cell_km) on a hit, or (None, None) when the
    database is missing or nothing usable is stored nearby.
    """
    if not ENABLE_OFFLINE_DB:
        return None, None
    grid = _read_json_cached(POWER_GRID_FILE)
    if not isinstance(grid, dict) or not grid.get("cells"):
        return None, None
    found = _lookup_cell(grid["cells"], POWER_GRID_STEP, lat, lon,
                         POWER_MAX_RINGS)
    if found is None:
        return None, None
    key, _cell_lat, _cell_lon, km = found
    temperature, rainfall, solar = grid["cells"][key]
    return {"avg_temp_c": temperature, "rainfall_mm": rainfall,
            "solar_radiation": solar}, km


def _offline_soil_ph(lat, lon):
    """
    Soil pH for this point from offline_db/soil_grid.json.

    Returns (ph, distance_to_cell_km) on a hit, or (None, None).
    """
    if not ENABLE_OFFLINE_DB:
        return None, None
    grid = _read_json_cached(SOIL_GRID_FILE)
    if not isinstance(grid, dict) or not grid.get("cells"):
        return None, None
    found = _lookup_cell(grid["cells"], SOIL_GRID_STEP, lat, lon,
                         SOIL_MAX_RINGS)
    if found is None:
        return None, None
    key, _cell_lat, _cell_lon, km = found
    return grid["cells"][key], km


def get_country_iso3(lat, lon):
    """
    ISO-3166 alpha-3 code of the country containing this point ("ESP"), or
    None when the point is in the sea or outside the downloaded borders.

    Uses offline_db/countries.geojson (world borders, downloaded once by
    fetch_offline_data.py) -- a small point-in-polygon test, no internet.
    """
    shapes = _load_country_shapes()
    if not shapes:
        return None
    lon = float(lon)
    lat = float(lat)
    for bbox, outer, holes, iso3 in shapes:
        min_lon, max_lon, min_lat, max_lat = bbox
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if _in_ring(lon, lat, outer) and not any(
                _in_ring(lon, lat, hole) for hole in holes):
            return iso3
    return None


def _load_country_shapes():
    """Flatten countries.geojson into [(bbox, outer_ring, holes, iso3), ...]."""
    if _COUNTRY_SHAPES["loaded"]:
        return _COUNTRY_SHAPES["shapes"]
    _COUNTRY_SHAPES["loaded"] = True   # missing file: retry only after restart
    geojson = _read_json_cached(COUNTRIES_FILE)
    if not isinstance(geojson, dict):
        return None
    shapes = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        coordinates = geometry.get("coordinates") or []
        if kind == "Polygon":
            polygons = [coordinates]
        elif kind == "MultiPolygon":
            polygons = coordinates
        else:
            continue   # points and lines are not countries
        iso3 = str(feature.get("id") or "")
        for polygon in polygons:
            if not polygon:
                continue
            outer = polygon[0]
            holes = polygon[1:]
            if not outer:
                continue
            lons = [point[0] for point in outer]
            lats = [point[1] for point in outer]
            shapes.append(((min(lons), max(lons), min(lats), max(lats)),
                           outer, holes, iso3))
    _COUNTRY_SHAPES["shapes"] = shapes
    return shapes


def _in_ring(lon, lat, ring):
    """Ray-casting point-in-polygon test (the classic even-odd rule)."""
    inside = False
    previous = len(ring) - 1
    for current in range(len(ring)):
        x1, y1 = ring[current][0], ring[current][1]
        x2, y2 = ring[previous][0], ring[previous][1]
        if (y1 > lat) != (y2 > lat):
            if lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                inside = not inside
        previous = current
    return inside


def get_market_prices(lat=None, lon=None):
    """
    Real FAOSTAT crop prices and yields for this point (offline_db/faostat.json).

        get_market_prices(40.4, -3.7) ->
        {
          "source": "faostat",
          "scope": "country",          # or "world" when nothing matched
          "country": "ESP",
          "country_name": "Spain",
          "place": "Spain",            # what the UI should show
          "year": 2023,                # newest price year found
          "prices": {
            "tomato": {"price_usd_per_t": 913.4, "price_year": 2023,
                        "yield_tha": 57.2, "yield_year": 2023,
                        "scope": "country"},
            ...                         # every crop FAOSTAT knows
          }
        }

    The pin's country is found with the downloaded world borders; crops the
    country does not report fall back to the world average. Returns None when
    faostat.json has not been downloaded yet (the engine then keeps its own
    placeholder prices -- nothing breaks).
    """
    faostat = _read_json_cached(FAOSTAT_FILE)
    if not isinstance(faostat, dict):
        return None
    world = faostat.get("world") or {}
    countries_db = faostat.get("countries") or {}
    if not world and not countries_db:
        return None
    area_names = faostat.get("area_names") or {}

    iso3 = get_country_iso3(lat, lon) if lat is not None and lon is not None else None
    country_rows = countries_db.get(iso3) if iso3 else None

    prices = {}
    for crop, entry in world.items():
        prices[crop] = dict(entry)
        prices[crop]["scope"] = "world"
    scope = "world"
    if country_rows:
        for crop, entry in country_rows.items():
            prices[crop] = dict(entry)
            prices[crop]["scope"] = "country"
        scope = "country"

    years = [entry.get("price_year") for entry in prices.values()
             if entry.get("price_year")]
    return {
        "source": "faostat",
        "scope": scope,
        "country": iso3,
        "country_name": area_names.get(iso3) if iso3 else None,
        "place": area_names.get(iso3) if scope == "country"
                 else area_names.get("WORLD", "World"),
        "year": max(years) if years else None,
        "prices": prices,
    }


def get_offline_db_status():
    """What of the offline database is on disk right now (shown in /health)."""
    power = _read_json_cached(POWER_GRID_FILE)
    soil = _read_json_cached(SOIL_GRID_FILE)
    faostat = _read_json_cached(FAOSTAT_FILE)
    countries = _read_json_cached(COUNTRIES_FILE)
    power_cells = len(power.get("cells", {})) if isinstance(power, dict) else 0
    soil_cells = len(soil.get("cells", {})) if isinstance(soil, dict) else 0
    faostat_countries = (len(faostat.get("countries", {}))
                         if isinstance(faostat, dict) else 0)
    border_countries = (len(countries.get("features", []))
                        if isinstance(countries, dict) else 0)
    return {
        "power_grid_cells": power_cells,
        "soil_grid_cells": soil_cells,
        "faostat_countries": faostat_countries,
        "border_countries": border_countries,
        "ready": bool(power_cells and soil_cells and faostat_countries
                      and border_countries),
    }


# ---------------------------------------------------------------------------
# API CALLS
# ---------------------------------------------------------------------------
def _fetch_nasa_power(lat, lon):
    """
    Ask NASA POWER for a climate summary of this point.

    The answer contains one block of yearly averages ("ANN"):
      T2M              average air temperature at 2 m, in Celsius
      PRECTOTCORR      rainfall, in millimetres PER DAY (we multiply by 365)
      ALLSKY_SFC_SW_DWN solar radiation, in kWh per square metre PER DAY
    """
    params = {
        "parameters": "T2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN",
        "community": "RE",          # RE = renewable energy / agroclimatology
        "longitude": lon,
        "latitude": lat,
        "format": "JSON",
    }
    payload = _http_get_json(NASA_POWER_URL, params=params)
    yearly = payload["properties"]["parameter"]

    temperature = _clean_number(yearly.get("T2M", {}).get("ANN"))
    rain_per_day = _clean_number(yearly.get("PRECTOTCORR", {}).get("ANN"))
    solar_per_day = _clean_number(yearly.get("ALLSKY_SFC_SW_DWN", {}).get("ANN"))

    return {
        "avg_temp_c": round(temperature, 1) if temperature is not None else None,
        # mm/day -> mm/year
        "rainfall_mm": round(rain_per_day * 365.0, 0) if rain_per_day is not None else None,
        "solar_radiation": round(solar_per_day, 2) if solar_per_day is not None else None,
    }


def _fetch_soilgrids_ph(lat, lon):
    """
    Ask SoilGrids for the average soil pH of the top 5 cm ("0-5cm" layer).

    SoilGrids sends pH multiplied by 10 (because JSON has no decimals in their
    format), so a value of 68 means pH 6.8.
    """
    params = {
        "lon": lon,
        "lat": lat,
        "property": "phh2o",
        "depth": "0-5cm",
        "value": "mean",
    }
    payload = _http_get_json(SOILGRIDS_URL, params=params)

    layers = payload.get("properties", {}).get("layers", [])
    for layer in layers:
        if layer.get("name") != "phh2o":
            continue
        for depth in layer.get("depths", []):
            values = depth.get("values", {})
            # Different SoilGrids deployments name the column differently.
            raw = values.get("mean")
            if raw is None:
                raw = values.get("Q0.5")
            raw = _clean_number(raw)
            if raw is not None:
                return round(raw / 10.0, 1)
    return None


# ---------------------------------------------------------------------------
# SMALL HELPERS
# ---------------------------------------------------------------------------
def _http_get_json(url, params):
    """GET a URL and return the decoded JSON, or raise an exception."""
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _clean_number(value):
    """
    Convert a value to a float, or return None if it is missing/not usable.

    NASA POWER uses -999 to mean "no data for this point", which we must treat
    as missing rather than as a real temperature of -999 C.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= -900:
        return None
    return number


def _short_error(exc):
    """A one-line, beginner-friendly version of an error message."""
    message = str(exc).strip() or exc.__class__.__name__
    return message if len(message) <= 120 else message[:117] + "..."


def _placeholder_site(lat, lon, note):
    """A complete site dictionary built only from placeholder values."""
    site = {"lat": lat, "lon": lon, "source": "fallback", "notes": [note]}
    site.update(PLACEHOLDER_SITE)
    return site


def _load_cache():
    """Read site_cache.json. Returns an empty dictionary if anything is wrong."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # A broken cache file must never break the app.
        return {}


def _save_cache(cache):
    """Write site_cache.json. Failures are ignored on purpose."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2, sort_keys=True)
    except OSError as exc:
        print("[data.py] Could not write the cache file: %s" % exc)


# Running this file on its own lets you try the API quickly:
#     python data.py
if __name__ == "__main__":
    import sys

    test_lat = float(sys.argv[1]) if len(sys.argv) > 1 else 24.7136   # Riyadh
    test_lon = float(sys.argv[2]) if len(sys.argv) > 2 else 46.6753
    print("Looking up %.4f, %.4f ..." % (test_lat, test_lon))
    print(json.dumps(get_site_info(test_lat, test_lon), indent=2))
    print("Country:", get_country_iso3(test_lat, test_lon))
    market = get_market_prices(test_lat, test_lon)
    if market:
        print("Market (%s, %s):" % (market["place"], market["year"]))
        print(json.dumps(market["prices"], indent=2))
    else:
        print("Market: faostat.json not downloaded yet "
              "(run fetch_offline_data.py).")
