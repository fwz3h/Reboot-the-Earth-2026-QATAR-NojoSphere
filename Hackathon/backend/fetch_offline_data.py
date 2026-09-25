"""
fetch_offline_data.py -- run this ONCE with internet to download everything
Mazraa needs to work with NO internet afterwards (guide Steps 4 and 5).

    cd backend
    python fetch_offline_data.py                 # download everything (~10 min)
    python fetch_offline_data.py --only packs    # just the demo-location packs
    python fetch_offline_data.py --skip power    # everything except the NASA grid
    python fetch_offline_data.py --help

Stages, in the order they run:

    packs      frontend/data/offline_packs/<id>.json
               Exact live NASA POWER / SoilGrids answers for the three demo
               locations -- prints "Saved doha.json", "Saved almeria.json",
               "Saved rwanda.json". The browser uses these when the backend
               cannot be reached, so the core loop keeps working offline.

    countries  offline_db/countries.geojson
               World borders with ISO country codes. Two jobs: turn a map pin
               into a country (so FAOSTAT prices can be country-specific) and
               act as the land mask that tells the two grid stages which cells
               are worth querying at all.

    soil       offline_db/soil_grid.json
               SoilGrids soil pH sampled every 2.5 degrees over land
               (roughly 2,500 small API calls; a few minutes).

    power      offline_db/power_grid.json
               NASA POWER temperature / rainfall / solar radiation on their
               native 0.625 degree grid, for every 10x10 degree tile that
               contains land (one request per tile per parameter).

    faostat    offline_db/faostat.json
               FAOSTAT producer prices (USD/tonne) and yields (t/ha) for
               Mazraa's five crops: world averages plus every country that
               reports them. The two FAOSTAT bulk zips (~45 MB) are cached in
               offline_db/downloads/, so re-runs do not download them again.

Everything is resumable: re-running skips whatever is already on disk, so an
interrupted download continues where it stopped. Grid downloads save their
progress every hundred cells / ten tiles.

Afterwards backend/data.py reads those files FIRST and only calls the live APIs
for points the files do not cover -- see the docstring at the top of data.py.
Restart the backend after re-running this script so it picks up the new files.
"""

import csv
import datetime
import io
import json
import math
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

# Make sure the folder that contains this file is on Python's import path, so
# `python fetch_offline_data.py` works from anywhere.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Windows terminals often use cp1252, and some labels below contain accented
# or non-Latin characters. Force UTF-8 so printing can never crash the script.
try:  # pragma: no cover - depends on the machine running the script
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

try:
    import requests  # listed in requirements.txt
except ImportError:  # pragma: no cover - only happens before pip install
    print("This script needs the 'requests' package:  pip install requests")
    raise

import data  # noqa: E402  (NASA POWER + SoilGrids lookup, cell_key(), ...)

# ---------------------------------------------------------------------------
# WHERE THINGS GO
# ---------------------------------------------------------------------------
# The website expects the demo packs here (same folder as before).
OUT_DIR = os.path.normpath(
    os.path.join(BASE_DIR, os.pardir, "frontend", "data", "offline_packs")
)
# The offline database the backend reads at request time.
DB_DIR = os.path.join(BASE_DIR, "offline_db")
# Big FAOSTAT zips are kept here so re-runs do not download 45 MB again.
DOWNLOAD_DIR = os.path.join(DB_DIR, "downloads")

# ---------------------------------------------------------------------------
# WHERE THE DATA COMES FROM  (all free, no API key needed)
# ---------------------------------------------------------------------------
COUNTRIES_URL = ("https://raw.githubusercontent.com/johan/world.geo.json/"
                 "master/countries.geo.json")
M49_URL = ("https://raw.githubusercontent.com/lukes/ISO-3166-Countries-with-"
           "Regional-Codes/master/all/all.csv")
FAOSTAT_PRICES_URL = ("https://bulks-faostat.fao.org/production/"
                      "Prices_E_All_Data_(Normalized).zip")
FAOSTAT_CROPS_URL = ("https://bulks-faostat.fao.org/production/"
                     "Production_Crops_Livestock_E_All_Data_(Normalized).zip")
NASA_REGIONAL_URL = ("https://power.larc.nasa.gov/api/temporal/"
                     "climatology/regional")

# NASA POWER allows ONE parameter per request and 10x10 degree tiles at most.
NASA_PARAMETERS = ("T2M",              # air temperature, Celsius
                   "PRECTOTCORR",      # rainfall, millimetres PER DAY
                   "ALLSKY_SFC_SW_DWN")  # solar, kWh/m2 PER DAY
POWER_TILE_DEG = 10

# Land/soil grid: every cell centre of this lattice is tested against the
# world borders, and only land cells are sent to SoilGrids.
LAND_GRID_STEP = data.SOIL_GRID_STEP     # 2.5 degrees
LAT_MIN, LAT_MAX = -58.0, 84.0           # no point sampling Antarctica

# How many requests run at the same time. Polite but still quick.
SOIL_WORKERS = 6
POWER_WORKERS = 6

# ---------------------------------------------------------------------------
# WHICH FILES COUNT AS "OUR" CROPS (FAOSTAT item names, lowercase)
# ---------------------------------------------------------------------------
CROP_ITEMS = {
    "tomato": ("tomatoes",),
    "lettuce": ("lettuce and chicory",),
    "cucumber": ("cucumbers and gherkins",),
    "wheat": ("wheat",),
    "strawberry": ("strawberries",),
}
ITEM_BY_CROP = {crop: names[0] for crop, names in CROP_ITEMS.items()}

# Sanity windows: anything outside these is a parsing mistake, not real data.
PRICE_MIN_USD_PER_T = 5.0
PRICE_MAX_USD_PER_T = 20000.0
YIELD_MIN_T_HA = 0.01
YIELD_MAX_T_HA = 500.0

# The three locations we demo with. Add your own village here and re-run.
#   id    -> becomes the file name (<id>.json)
#   label -> shown in the website so the farmer knows which pack was used
DEMO_LOCATIONS = [
    ("doha",    "Doha, Qatar",         25.2854,  51.5310),
    ("almeria", "Almeria, Spain",      36.8341,  -2.4637),
    ("rwanda",  "Kigali, Rwanda",      -1.9403,  29.8739),
]

STAGES = ("packs", "countries", "soil", "power", "faostat")


# ---------------------------------------------------------------------------
# SMALL HELPERS
# ---------------------------------------------------------------------------
_thread_local = __import__("threading").local()
_LAND_RINGS = {"loaded": False, "rings": None}


def _session():
    """One requests.Session per worker thread (keeps connections alive, which
    turns SoilGrids' 3+ seconds per call into a few hundred milliseconds)."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def _http_get(url, params=None, timeout=60, tries=3):
    """GET with retries on timeouts / rate limits. Raises on real errors."""
    last_error = None
    for attempt in range(tries):
        try:
            response = _session().get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                return response
            last_error = RuntimeError("HTTP %s from %s"
                                      % (response.status_code, url[:70]))
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(2.0 * (attempt + 1))   # transient: worth another try
                continue
            break   # a 4xx answer will not change: stop here
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(2.0 * (attempt + 1))
    if isinstance(last_error, Exception):
        raise last_error
    raise RuntimeError("request failed: %s" % url[:70])


def _http_get_json(url, params=None, timeout=60):
    return _http_get(url, params=params, timeout=timeout).json()


def _write_json(path, payload, pretty=True):
    """Write JSON via a temp file so an interrupt can never truncate it."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", errors="replace") as handle:
        if pretty:
            json.dump(payload, handle, indent=2, ensure_ascii=False,
                      sort_keys=True)
        else:
            json.dump(payload, handle, ensure_ascii=False,
                      separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def _read_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _ensure_download(name, url, timeout=600):
    """Download a file into offline_db/downloads/ once; reuse it afterwards."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    path = os.path.join(DOWNLOAD_DIR, name)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        print("Using cached download: %s (%.1f MB)"
              % (name, os.path.getsize(path) / 1e6), flush=True)
        return path
    print("Downloading %s ..." % name, flush=True)
    last_error = None
    for attempt in range(3):
        try:
            response = _session().get(url, stream=True, timeout=(30, timeout))
            response.raise_for_status()
            with open(path + ".part", "wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    if chunk:
                        handle.write(chunk)
            os.replace(path + ".part", path)
            print("Downloaded %s (%.1f MB)" % (name, os.path.getsize(path) / 1e6),
                  flush=True)
            return path
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(3.0 * (attempt + 1))
    raise RuntimeError("could not download %s: %s" % (url, last_error))


def _short_error(exc):
    message = str(exc).strip() or exc.__class__.__name__
    return message if len(message) <= 120 else message[:117] + "..."


# ---------------------------------------------------------------------------
# THE LAND MASK (world borders -> which cells are land)
# ---------------------------------------------------------------------------
def _load_land_rings():
    """[(min_lon, max_lon, min_lat, max_lat, outer_ring, holes), ...] for every
    country polygon in offline_db/countries.geojson."""
    if _LAND_RINGS["loaded"]:
        return _LAND_RINGS["rings"]
    geojson = _read_json(os.path.join(DB_DIR, "countries.geojson"))
    if geojson is None:
        return None
    rings = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        coordinates = geometry.get("coordinates") or []
        if kind == "Polygon":
            polygons = [coordinates]
        elif kind == "MultiPolygon":
            polygons = coordinates
        else:
            continue
        for polygon in polygons:
            if not polygon or not polygon[0]:
                continue
            outer = polygon[0]
            holes = polygon[1:]
            lons = [point[0] for point in outer]
            lats = [point[1] for point in outer]
            rings.append((min(lons), max(lons), min(lats), max(lats),
                          outer, holes))
    _LAND_RINGS["loaded"] = True
    _LAND_RINGS["rings"] = rings
    return rings


def _in_ring(lon, lat, ring):
    """Ray-casting point-in-polygon test (even-odd rule)."""
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


def _is_land(lon, lat, rings):
    for min_lon, max_lon, min_lat, max_lat, outer, holes in rings:
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        if _in_ring(lon, lat, outer) and not any(
                _in_ring(lon, lat, hole) for hole in holes):
            return True
    return False


def _land_cells(step=LAND_GRID_STEP):
    """Centres of every `step`-degree cell that sits on land."""
    rings = _load_land_rings()
    if not rings:
        raise RuntimeError("countries.geojson is missing -- run the "
                           "'countries' stage first (it runs automatically "
                           "in the normal order).")
    cells = []
    lat_from = int(math.floor(LAT_MIN / step))
    lat_to = int(math.ceil(LAT_MAX / step))
    lon_from = int(math.floor(-180.0 / step))
    lon_to = int(math.ceil(180.0 / step))
    for i in range(lat_from, lat_to):
        lat = i * step
        for j in range(lon_from, lon_to):
            lon = j * step
            if _is_land(lon, lat, rings):
                cells.append((lat, lon))
    return cells


# ---------------------------------------------------------------------------
# STAGE 1: THE DEMO-LOCATION PACKS (what the guide calls Step 4)
# ---------------------------------------------------------------------------
def stage_packs():
    if not data.ENABLE_NETWORK:
        print("Warning: MAZRAA_OFFLINE=1 is set, so live lookups are disabled. "
              "Unset it for this run or the packs will contain placeholders.")
    os.makedirs(OUT_DIR, exist_ok=True)

    index = []
    for pack_id, label, lat, lon in DEMO_LOCATIONS:
        print("Fetching %s (%.4f, %.4f) ..." % (label, lat, lon), flush=True)
        # force_live: packs always want the exact answer for this point, never
        # a nearby grid cell from offline_db and never an older cached value.
        site = data.get_site_info(lat, lon, force_live=True)

        pack = {
            "id": pack_id,
            "label": label,
            "lat": lat,
            "lon": lon,
            "downloaded_on": datetime.date.today().isoformat(),
            "site": site,
        }
        path = os.path.join(OUT_DIR, pack_id + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(pack, handle, indent=2, ensure_ascii=False, sort_keys=True)

        # Exactly the line the guide promises.
        print("Saved %s.json" % pack_id, flush=True)

        index.append({
            "id": pack_id,
            "file": pack_id + ".json",
            "label": label,
            "lat": lat,
            "lon": lon,
        })

    # The browser reads this list first, then downloads only the nearest pack.
    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as handle:
        json.dump({"packs": index}, handle, indent=2, ensure_ascii=False)

    print("Wrote index.json (and %d packs) to %s" % (len(index), OUT_DIR),
          flush=True)


# ---------------------------------------------------------------------------
# STAGE 2: WORLD BORDERS
# ---------------------------------------------------------------------------
def stage_countries():
    response = _http_get(COUNTRIES_URL, timeout=120)
    payload = response.json()
    count = len(payload.get("features", []))
    # Saved exactly as downloaded (compact, 0.25 MB).
    path = os.path.join(DB_DIR, "countries.geojson")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(response.text)
    os.replace(tmp, path)
    print("Saved countries.geojson (%d countries)" % count, flush=True)


def _require_countries():
    if _read_json(os.path.join(DB_DIR, "countries.geojson")) is None:
        print("countries.geojson missing -- downloading it first.")
        stage_countries()
    _LAND_RINGS["loaded"] = False   # in case it was attempted before


# ---------------------------------------------------------------------------
# STAGE 3: SOILGRIDS SOIL pH ON A 2.5 DEGREE GRID
# ---------------------------------------------------------------------------
def _fetch_soil_ph(lat, lon):
    """SoilGrids pH of the top 5 cm, or None when there is no data (sea)."""
    payload = _http_get_json(
        data.SOILGRIDS_URL,
        params={"lon": lon, "lat": lat, "property": "phh2o",
                "depth": "0-5cm", "value": "mean"},
        timeout=45,
    )
    for layer in payload.get("properties", {}).get("layers", []):
        if layer.get("name") != "phh2o":
            continue
        for depth in layer.get("depths", []):
            values = depth.get("values", {})
            raw = values.get("mean")
            if raw is None:
                raw = values.get("Q0.5")   # alternative column name
            if raw is None:
                continue
            number = float(raw)
            if number <= -900:             # SoilGrids' "no data" marker
                continue
            return round(number / 10.0, 1)   # they store pH * 10
    return None


def stage_soil():
    _require_countries()
    path = os.path.join(DB_DIR, "soil_grid.json")
    database = _read_json(path)
    if not isinstance(database, dict) or "cells" not in database:
        database = {"grid_step": data.SOIL_GRID_STEP, "cells": {}}
    cells = database["cells"]

    todo = [cell for cell in _land_cells(LAND_GRID_STEP)
            if data.cell_key(cell[0], cell[1], LAND_GRID_STEP) not in cells]
    print("soil: %d cells already stored, %d to fetch"
          % (len(cells), len(todo)), flush=True)
    if not todo:
        print("Saved soil_grid.json (%d cells)" % len(cells), flush=True)
        return

    fetched = 0
    failures = 0
    last_error = None

    def work(cell):
        lat, lon = cell
        return cell, _fetch_soil_ph(lat, lon)

    with ThreadPoolExecutor(max_workers=SOIL_WORKERS) as pool:
        futures = [pool.submit(work, cell) for cell in todo]
        for future in as_completed(futures):
            try:
                (lat, lon), ph = future.result()
            except Exception as exc:      # network hiccup: leave the cell out
                failures += 1             # so the next run retries it
                last_error = exc
                continue
            # ph may be None: "queried, no data here" -- stored so we never
            # ask about the same patch of sea twice.
            cells[data.cell_key(lat, lon, LAND_GRID_STEP)] = ph
            fetched += 1
            if fetched % 100 == 0:
                _write_json(path, database, pretty=False)
                print("soil: %d/%d fetched (%d failed so far)"
                      % (fetched, len(todo), failures), flush=True)

    _write_json(path, database, pretty=False)
    if failures:
        print("soil: %d cells failed (%s) -- re-run to retry them"
              % (failures, _short_error(last_error)), flush=True)
    print("Saved soil_grid.json (%d cells)" % len(cells), flush=True)


# ---------------------------------------------------------------------------
# STAGE 4: NASA POWER CLIMATE ON ITS NATIVE 0.625 DEGREE GRID
# ---------------------------------------------------------------------------
def _fetch_power_tile(lat0, lon0):
    """Download one 10x10 degree tile (one request per parameter) and return
    {cell_key: [temp_c, rain_mm_per_year, solar_kwh_m2_day]}."""
    cells = {}

    def slot(lon, lat):
        key = data.cell_key(lat, lon, data.POWER_GRID_STEP)
        if key not in cells:
            cells[key] = [None, None, None]
        return cells[key]

    for index, parameter in enumerate(NASA_PARAMETERS):
        payload = _http_get_json(
            NASA_REGIONAL_URL,
            params={
                "parameters": parameter,
                "community": "RE",
                "longitude-min": lon0,
                "longitude-max": lon0 + POWER_TILE_DEG,
                "latitude-min": lat0,
                "latitude-max": lat0 + POWER_TILE_DEG,
                "format": "JSON",
            },
            timeout=90,
        )
        for feature in payload.get("features", []):
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coordinates) < 2:
                continue
            lon, lat = float(coordinates[0]), float(coordinates[1])
            yearly = ((feature.get("properties") or {}).get("parameter") or
                      {}).get(parameter) or {}
            value = yearly.get("ANN")     # ANN = yearly average
            if value is None:
                continue
            value = float(value)
            if value <= -900:             # NASA's "no data" marker
                continue
            cell = slot(lon, lat)
            if index == 0:
                cell[0] = round(value, 1)              # Celsius
            elif index == 1:
                cell[1] = round(value * 365.0, 0)      # mm/day -> mm/year
            else:
                cell[2] = round(value, 2)              # kWh/m2/day
    return cells


def stage_power():
    _require_countries()
    path = os.path.join(DB_DIR, "power_grid.json")
    database = _read_json(path)
    if not isinstance(database, dict) or "cells" not in database:
        database = {"grid_step": data.POWER_GRID_STEP, "tiles_done": [],
                    "cells": {}}
    database.setdefault("tiles_done", [])
    cells = database["cells"]
    done = set(database["tiles_done"])

    # One tile per 10x10 degree square that contains at least one land cell.
    tiles = sorted({
        (int(math.floor(lat / POWER_TILE_DEG)) * POWER_TILE_DEG,
         int(math.floor(lon / POWER_TILE_DEG)) * POWER_TILE_DEG)
        for lat, lon in _land_cells(LAND_GRID_STEP)
    })
    todo = [tile for tile in tiles
            if "%s,%s" % tile not in done]
    print("power: %d/%d tiles already done, %d to fetch (%d requests)"
          % (len(done), len(tiles), len(todo), len(todo) * len(NASA_PARAMETERS)),
          flush=True)
    if not todo:
        print("Saved power_grid.json (%d cells)" % len(cells), flush=True)
        return

    finished = 0
    failures = 0
    last_error = None

    with ThreadPoolExecutor(max_workers=POWER_WORKERS) as pool:
        futures = {pool.submit(_fetch_power_tile, lat0, lon0): (lat0, lon0)
                   for lat0, lon0 in todo}
        for future in as_completed(futures):
            tile = futures[future]
            try:
                tile_cells = future.result()
            except Exception as exc:      # failed tiles stay "not done", so
                failures += 1             # the next run retries exactly them
                last_error = exc
                continue
            cells.update(tile_cells)
            database["tiles_done"].append("%s,%s" % tile)
            finished += 1
            if finished % 10 == 0:
                _write_json(path, database, pretty=False)
                print("power: %d/%d tiles done (%d failed so far)"
                      % (finished, len(todo), failures), flush=True)

    database["tiles_done"].sort()
    _write_json(path, database, pretty=False)
    if failures:
        print("power: %d tiles failed (%s) -- re-run to retry them"
              % (failures, _short_error(last_error)), flush=True)
    print("Saved power_grid.json (%d cells from %d tiles)"
          % (len(cells), len(database["tiles_done"])), flush=True)


# ---------------------------------------------------------------------------
# STAGE 5: FAOSTAT PRICES AND YIELDS
# ---------------------------------------------------------------------------
def _norm_m49(raw):
    """\"'004\" -> \"004\" (FAOSTAT quotes M49 codes as text for Excel)."""
    text = (raw or "").strip().lstrip("'")
    if text.isdigit():
        return str(int(text))   # normalises 004 and 4 to the same key
    return None


def _load_m49_map(path):
    """M49 (UN numeric) -> ISO3 (letters), e.g. 724 -> ESP."""
    mapping = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            m49 = _norm_m49(row.get("country-code"))
            iso3 = (row.get("alpha-3") or "").strip()
            if m49 and iso3:
                mapping[m49] = iso3
    return mapping


def _crop_for_item(item):
    """FAOSTAT item name -> one of our crop keys, or None."""
    name = (item or "").strip().lower()
    for crop, names in CROP_ITEMS.items():
        if name in names:
            return crop
    return None


def _read_faostat_prices(path):
    """(area, m49, crop) -> {price_year, price_usd_per_t} -- newest year wins;
    several months of the same year are averaged."""
    best = {}
    item_names = {}
    with zipfile.ZipFile(path) as archive:
        name = _normalized_csv(archive)
        with archive.open(name) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig",
                                                     newline=""))
            for row in reader:
                if row.get("Element") != "Producer Price (USD/tonne)":
                    continue
                months = (row.get("Months") or "").strip()
                if months and months != "Annual value":
                    continue
                crop = _crop_for_item(row.get("Item"))
                if crop is None:
                    continue
                try:
                    year = int(row.get("Year") or 0)
                    value = float(row.get("Value") or 0)
                except ValueError:
                    continue
                if year <= 0 or not (PRICE_MIN_USD_PER_T <= value
                                     <= PRICE_MAX_USD_PER_T):
                    continue
                item_names[crop] = (row.get("Item") or "").strip()
                key = (row.get("Area", "").strip(),
                       _norm_m49(row.get("Area Code (M49)")), crop)
                entry = best.get(key)
                if entry is None or year > entry["year"]:
                    best[key] = {"year": year, "sum": value, "n": 1}
                elif year == entry["year"]:
                    entry["sum"] += value
                    entry["n"] += 1
    rows = {}
    for key, entry in best.items():
        rows[key] = {"price_year": entry["year"],
                     "price_usd_per_t": round(entry["sum"] / entry["n"], 2)}
    return rows, item_names


def _read_faostat_yields(path):
    """(area, m49, crop) -> {yield_year, yield_tha} -- newest year wins."""
    best = {}
    with zipfile.ZipFile(path) as archive:
        name = _normalized_csv(archive)
        with archive.open(name) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig",
                                                     newline=""))
            for row in reader:
                if row.get("Element") != "Yield" or row.get("Unit") != "kg/ha":
                    continue
                crop = _crop_for_item(row.get("Item"))
                if crop is None:
                    continue
                try:
                    year = int(row.get("Year") or 0)
                    value = float(row.get("Value") or 0) / 1000.0  # kg/ha -> t/ha
                except ValueError:
                    continue
                if year <= 0 or not (YIELD_MIN_T_HA <= value
                                     <= YIELD_MAX_T_HA):
                    continue
                key = (row.get("Area", "").strip(),
                       _norm_m49(row.get("Area Code (M49)")), crop)
                entry = best.get(key)
                if entry is None or year > entry["yield_year"]:
                    best[key] = {"yield_year": year, "yield_tha": round(value, 2)}
    return best


def _normalized_csv(archive):
    """The main data CSV inside a FAOSTAT bulk zip (not the code lists)."""
    for name in archive.namelist():
        if name.endswith(".csv") and "All_Data" in name and "Normalized" in name:
            return name
    raise RuntimeError("unexpected FAOSTAT zip contents: %s" % archive.namelist())


def _read_faostat_production(path):
    """(area, m49, crop) -> {production_year, production_t} -- newest year.
    Used only as the weight when building the world price."""
    best = {}
    with zipfile.ZipFile(path) as archive:
        name = _normalized_csv(archive)
        with archive.open(name) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig",
                                                     newline=""))
            for row in reader:
                if row.get("Element") != "Production" or row.get("Unit") != "t":
                    continue
                crop = _crop_for_item(row.get("Item"))
                if crop is None:
                    continue
                try:
                    year = int(row.get("Year") or 0)
                    value = float(row.get("Value") or 0)
                except ValueError:
                    continue
                if year <= 0 or value <= 0:
                    continue
                key = (row.get("Area", "").strip(),
                       _norm_m49(row.get("Area Code (M49)")), crop)
                entry = best.get(key)
                if entry is None or year > entry["production_year"]:
                    best[key] = {"production_year": year,
                                 "production_t": value}
    return best


def _world_aggregates(price_rows, yield_rows, production_rows):
    """
    Per-crop WORLD price (and a yield fallback) built from the country rows.

    FAOSTAT does not publish a world-level producer price, so we compute one as
    a production-weighted average of the countries that do report prices:

        world price = sum(price_c * production_c) / sum(production_c)

    which is the honest "what does the world's wheat cost per tonne" number
    (a plain mean would let a tiny exporter move it as much as a giant one).
    The official FAOSTAT World YIELD rows, when present, win over the fallback.
    """
    weights = {(area, m49, crop): entry["production_t"]
               for (area, m49, crop), entry in production_rows.items()}
    out = {}

    by_crop = {}
    for (area, m49, crop), entry in price_rows.items():
        by_crop.setdefault(crop, []).append(
            (entry["price_usd_per_t"], entry["price_year"],
             weights.get((area, m49, crop))))
    for crop, rows in by_crop.items():
        weighted = [(price, weight) for price, _year, weight in rows if weight]
        if weighted:
            total_weight = sum(weight for _price, weight in weighted)
            price = sum(p * w for p, w in weighted) / total_weight
        else:
            price = sum(price for price, _year, _weight in rows) / len(rows)
        out.setdefault(crop, {}).update({
            "price_usd_per_t": round(price, 2),
            "price_year": max(year for _price, year, _weight in rows),
        })

    by_crop = {}
    for (area, m49, crop), entry in yield_rows.items():
        by_crop.setdefault(crop, []).append(
            (entry["yield_tha"], entry["yield_year"],
             weights.get((area, m49, crop))))
    for crop, rows in by_crop.items():
        weighted = [(value, weight) for value, _year, weight in rows if weight]
        if weighted:
            total_weight = sum(weight for _value, weight in weighted)
            value = sum(v * w for v, w in weighted) / total_weight
        else:
            value = sum(value for value, _year, _weight in rows) / len(rows)
        out.setdefault(crop, {}).setdefault("yield_tha", round(value, 2))
        out.setdefault(crop, {}).setdefault(
            "yield_year", max(year for _value, year, _weight in rows))
    return out


def stage_faostat():
    prices_zip = _ensure_download("prices.zip", FAOSTAT_PRICES_URL)
    crops_zip = _ensure_download("crops.zip", FAOSTAT_CROPS_URL)
    m49_csv = _ensure_download("m49.csv", M49_URL)
    iso3_by_m49 = _load_m49_map(m49_csv)

    print("faostat: scanning producer prices ...", flush=True)
    price_rows, item_names = _read_faostat_prices(prices_zip)
    print("faostat: %d price rows for our crops" % len(price_rows), flush=True)
    print("faostat: scanning yields ...", flush=True)
    yield_rows = _read_faostat_yields(crops_zip)
    print("faostat: %d yield rows for our crops" % len(yield_rows), flush=True)
    production_rows = _read_faostat_production(crops_zip)
    print("faostat: %d production rows (world-price weights)"
          % len(production_rows), flush=True)

    # Merge price + yield per (area, crop).
    areas = {}        # area name -> {crop: entry}
    m49_by_area = {}  # area name -> M49 code
    for rows in (price_rows, yield_rows):
        for (area, m49, crop), part in rows.items():
            entry = areas.setdefault(area, {})
            entry.setdefault(crop, {}).update(part)
            if m49:
                m49_by_area.setdefault(area, m49)

    world = areas.pop("World", None) or {}
    # FAOSTAT has no world-level price: build one (production-weighted).
    # Official World rows win where they exist; these fill the gaps.
    for crop, entry in _world_aggregates(price_rows, yield_rows,
                                         production_rows).items():
        merged = world.setdefault(crop, {})
        for key, value in entry.items():
            merged.setdefault(key, value)
    countries = {}
    area_names = {"WORLD": "World"}
    skipped = 0
    for area, crops in areas.items():
        iso3 = iso3_by_m49.get(m49_by_area.get(area))
        if not iso3:
            skipped += 1       # regional aggregates like "Africa", "EU" ...
            continue
        countries[iso3] = crops
        area_names[iso3] = area

    database = {
        "downloaded_on": datetime.date.today().isoformat(),
        "source": "FAOSTAT (FAO): Prices -> Producer Price (USD/tonne); "
                  "Production_Crops_Livestock -> Yield (kg/ha)",
        "items": {crop: item_names.get(crop, ITEM_BY_CROP[crop])
                  for crop in CROP_ITEMS},
        "world": world,
        "countries": countries,
        "area_names": area_names,
    }
    if not world:
        print("Warning: FAOSTAT had no 'World' rows; country data still works.")
    _write_json(os.path.join(DB_DIR, "faostat.json"), database, pretty=False)
    print("Saved faostat.json (%d countries, %d crops%s)"
          % (len(countries), len(CROP_ITEMS),
             ", %d aggregates skipped" % skipped if skipped else ""),
          flush=True)


# ---------------------------------------------------------------------------
# DRIVER
# ---------------------------------------------------------------------------
STAGE_FUNCTIONS = {
    "packs": stage_packs,
    "countries": stage_countries,
    "soil": stage_soil,
    "power": stage_power,
    "faostat": stage_faostat,
}


def _parse_args(argv):
    only = None
    skip = set()
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--help", "-h"):
            print(__doc__)
            print("Stages: %s" % ", ".join(STAGES))
            sys.exit(0)
        if arg in ("--only", "--skip") and index + 1 < len(argv):
            values = {value.strip() for value in argv[index + 1].split(",")
                      if value.strip()}
            unknown = values - set(STAGES)
            if unknown:
                print("Unknown stage(s): %s\nKnown stages: %s"
                      % (", ".join(sorted(unknown)), ", ".join(STAGES)))
                sys.exit(2)
            if arg == "--only":
                only = values
            else:
                skip |= values
            index += 2
            continue
        print("Unknown argument: %s\nUsage: python fetch_offline_data.py "
              "[--only a,b] [--skip c]" % arg)
        sys.exit(2)
    return only, skip


def main():
    only, skip = _parse_args(sys.argv[1:])
    os.makedirs(DB_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    failed = []
    for name in STAGES:
        if (only is not None and name not in only) or name in skip:
            print("=== skipping stage: %s ===" % name, flush=True)
            continue
        print("=== stage: %s ===" % name, flush=True)
        started = time.time()
        try:
            STAGE_FUNCTIONS[name]()
        except Exception as exc:
            failed.append(name)
            print("[stage %s] FAILED: %s" % (name, _short_error(exc)),
                  flush=True)
        else:
            print("=== stage %s finished in %.1fs ==="
                  % (name, time.time() - started), flush=True)

    if failed:
        print("\nFinished with failures: %s" % ", ".join(failed))
        print("Fix the error (or just re-run) -- completed work is kept, so "
              "the download resumes where it stopped.")
        return 1
    print("\nAll done.")
    print("  Offline database : %s" % DB_DIR)
    print("  Website packs    : %s" % OUT_DIR)
    print("Restart the backend so it picks the new files up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
