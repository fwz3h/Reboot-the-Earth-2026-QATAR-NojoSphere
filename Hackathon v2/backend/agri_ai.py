"""
AI AGRICULTURAL AUTONOMY PLATFORM -- AI/DATA CORE (single-file build) v2
=============================================================================
Vendored into AgriSense as backend/agri_ai.py. All reasoning logic, thresholds,
prompts, schemas and safety rules below are reproduced unchanged from the
supplied v2 source. Only three integration changes were made, each marked
[INTEGRATION]:

  [INTEGRATION 1] Optional imports. The original module imported torch, cv2 and
           numpy at the top level, so importing it at all required every heavy
           dependency. They are now guarded: `import backend.agri_ai` always
           succeeds, and the specific pieces that need a missing library raise
           AIDependencyError with the exact package name instead of an
           ImportError traceback. call capabilities() to see what is available.

  [INTEGRATION 2] Lazy model training. The original trained both networks at
           import time (STATE_NN_MODEL = _train_state_nn() at module scope),
           which blocks the importing process for the whole training run. Both
           are now built on first use through _ensure_state_model() /
           _ensure_weather_model(), behind a lock, and cached. Warm-up is
           triggered by the server off the request path (see ai_advisor.py).

  [INTEGRATION 3] Configurable paths. The SQLite file and the snapshot folder
           default to the project root instead of the module's own directory,
           and can be overridden with AGRI_AI_DB_PATH / AGRI_AI_CAPTURE_DIR.

Nothing else was altered: the numbered FIX behaviours, prompt text, JSON
schemas, crop knowledge base, irrigation safety rules and learning loop are
byte-for-byte the supplied logic.

Changes from v1 (see review notes -- each numbered fix below maps to the
numbered issue in the architecture review):

  [FIX 3]  Camera no longer skipped when soil moisture is "sufficient".
           It captures and runs the colour analyzer on every pipeline run.
  [FIX 4]  Weather Correction NN now has a real forecast-vs-actual history
           table + a retrain hook, so it can actually learn local bias over
           time instead of only being quick-trained on synthetic data once.
  [FIX 5]  Both NNs are still proxy-trained (documented honestly), but the
           training data generation is isolated so real GAEZ/SoilGrids/NASA
           datasets can be dropped in later without touching the rest of
           the pipeline (see PROXY_DATA_DISCLOSURE).
  [FIX 6]  Adds a real NASA POWER climatology call (free, no API key) so the
           external-data layer is Open-Meteo + SoilGrids + NASA POWER. FAO
           GAEZ/FAOSTAT are NOT point-queryable via a free/unauthenticated
           API, so they're still explicitly marked unavailable rather than
           faked -- this is disclosed instead of silently ignored.
  [FIX 7]  Crop knowledge base expanded (12 -> 21 crops) and given a
           "regions" tag so retrieval can be filtered by region as well as
           by climate/soil, moving toward the India+Africa+desert coverage
           the project targets.
  [FIX 8]  retrieve_relevant_crops() is explicitly reframed as CANDIDATE
           retrieval (it filters/ranks candidates for Qwen to reason over),
           not a final decision. Salinity tolerance is now part of the
           score when salinity data is available.
  [FIX 9]  soil_salinity / water_salinity are explicit "unavailable" fields
           (never fabricated), and Qwen is told to reduce confidence in
           salinity-sensitive crop calls when they're missing.
  [FIX 10] datetime / day_of_year / season are computed and passed into the
           structured intelligence layer.
  [FIX 11/12] Two separate Qwen prompts + schemas: FARM_DECISION (initial
           planning) and CROP_HEALTH (recurring disease/water-stress/harvest
           monitoring). They are never merged into one JSON shape.
  [FIX 13] Fallback plans/diagnoses are now flagged with an explicit
           ai_status field, and the dashboard "message" is prefixed loudly
           when a fallback was used, instead of just logging it quietly.
  [FIX 15] Sensor validation adds rate-of-change checks against the
           previous logged reading (via SQLite) -- an implausible jump is
           flagged rather than trusted.
  [FIX 16] Still exactly two NNs (Agricultural State NN, Weather Correction
           NN) + rule-based vision + Qwen + deterministic controller. No new
           NNs were added.

Intentionally OUT OF SCOPE for this pass (by request): the ultrasonic
reservoir sensor and physical pump control (review items 2 and 14). The
irrigation "action" is still only ever a recommendation string/log entry --
there is no code here that drives real hardware.

Standalone run (unchanged CLI, requires torch + opencv-python + openai, and
Ollama running locally with qwen3:14b pulled):
    python -m backend.agri_ai
=============================================================================
"""
import os
import json
import random
import sqlite3
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional, List

random.seed(42)


# =============================================================================
# 0. OPTIONAL DEPENDENCIES  [INTEGRATION 1]
# =============================================================================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AIDependencyError(RuntimeError):
    """Raised when a code path needs a library that is not installed.

    Carries `missing`, the pip package names, so the API layer can tell the
    operator exactly what to install instead of reporting a generic failure.
    """

    def __init__(self, message: str, missing: List[str]):
        super().__init__(message)
        self.missing = list(missing)


TORCH_IMPORT_ERROR: Optional[str] = None
try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except Exception as exc:                      # ImportError, OSError (broken wheels), ...
    torch = None
    nn = None
    TORCH_AVAILABLE = False
    TORCH_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

CV2_IMPORT_ERROR: Optional[str] = None
try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except Exception as exc:
    cv2 = None
    np = None
    CV2_AVAILABLE = False
    CV2_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def _require_torch() -> None:
    if not TORCH_AVAILABLE:
        raise AIDependencyError(
            "The neural-network layers of the AI core need PyTorch, which is not "
            f"installed ({TORCH_IMPORT_ERROR}). Install it with: pip install torch",
            ["torch"],
        )


def _require_vision() -> None:
    if not CV2_AVAILABLE:
        raise AIDependencyError(
            "Plant colour analysis and camera capture need OpenCV + NumPy, which are "
            f"not installed ({CV2_IMPORT_ERROR}). Install them with: "
            "pip install opencv-python numpy",
            ["opencv-python", "numpy"],
        )


def capabilities() -> Dict[str, Any]:
    """What this build can actually do right now, so the dashboard can state
    limitations plainly rather than implying the full stack is live."""
    missing = []
    if not TORCH_AVAILABLE:
        missing.append("torch")
    if not CV2_AVAILABLE:
        missing.extend(["opencv-python", "numpy"])
    try:
        import openai  # noqa: F401
        openai_available = True
    except Exception:
        openai_available = False
    return {
        "torch_available": TORCH_AVAILABLE,
        "torch_error": TORCH_IMPORT_ERROR,
        "vision_available": CV2_AVAILABLE,
        "vision_error": CV2_IMPORT_ERROR,
        "openai_client_available": openai_available,
        "missing_packages": missing,
        # The pipeline cannot produce its structured intelligence layer without
        # the state NN, so this is the real gate on whether it can run at all.
        "pipeline_runnable": TORCH_AVAILABLE and CV2_AVAILABLE,
        "runnable_without_reasoning_layer": TORCH_AVAILABLE and CV2_AVAILABLE,
        "notes": [
            "The Qwen reasoning layer is optional: if the `openai` client or the local "
            "Ollama server is unavailable, the pipeline returns a rule-based fallback "
            "flagged with ai_status='fallback' rather than pretending it reasoned.",
            "Training data for both NNs is synthetic proxy data -- see PROXY_DATA_DISCLOSURE.",
        ],
    }


# =============================================================================
# 1. CONFIG
# =============================================================================

QWEN_MODEL_PRIMARY = "qwen3:14b"     # per spec Section 10 -- do not silently substitute
QWEN_MODEL_FALLBACK = "qwen3:8b"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
# [INTEGRATION 3] project root by default, overridable.
DB_PATH = os.getenv("AGRI_AI_DB_PATH") or os.path.join(PROJECT_ROOT, "agri_ai.db")
CAPTURE_DIR = os.getenv("AGRI_AI_CAPTURE_DIR") or os.path.join(PROJECT_ROOT, "crop_snapshots")

# [FIX 5] Honest, single place documenting the current training status of
# both NNs so this doesn't get overstated in a demo/pitch.
PROXY_DATA_DISCLOSURE = (
    "Both the Agricultural State NN and the Weather Correction NN are trained on "
    "synthetic proxy datasets generated by transparent agronomic/meteorological rule "
    "functions in this file, NOT on real historical FAO GAEZ / FAOSTAT / NASA POWER / "
    "SoilGrids records. This is Version 1 (prototype, proves the architecture). "
    "Version 2 would replace _state_sample_row()/_state_label_row() and "
    "_weather_sample_row() with real historical training rows pulled from those "
    "sources; nothing else in the pipeline needs to change to make that swap."
)


# =============================================================================
# 2. REAL EXTERNAL DATA -- Open-Meteo + SoilGrids + NASA POWER
# =============================================================================
# [FIX 6] Added a real NASA POWER climatology call. FAO GAEZ and FAOSTAT do
# not expose a free, unauthenticated, point-based (lat/lon) query API --
# GAEZ is a portal/download product and FAOSTAT is country-level bulk data --
# so they remain explicitly "unavailable" below rather than faked or quietly
# dropped from the pipeline.

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
NASA_POWER_CLIMATOLOGY_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"


def _get_json(url: str, timeout: int = 8) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AgriAI/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [data fetch] '{url.split('?')[0]}' unavailable: {e}")
        return None


def fetch_weather(lat: float, lon: float) -> Dict[str, Any]:
    """Real current weather + short forecast from Open-Meteo. None fields
    (not invented values) if the call fails."""
    url = (f"{OPEN_METEO_URL}?latitude={lat}&longitude={lon}"
           f"&current=temperature_2m,relative_humidity_2m,precipitation,rain"
           f"&daily=precipitation_probability_max,precipitation_sum"
           f"&forecast_days=3&timezone=auto")
    data = _get_json(url)
    if data is None:
        return {"temperature_c": None, "humidity_pct": None, "rainfall_mm": None,
                "rain_probability_next_days": None, "source": "open-meteo (unavailable)"}
    current = data.get("current", {})
    daily = data.get("daily", {})
    precip_probs = daily.get("precipitation_probability_max", [])
    return {
        "temperature_c": current.get("temperature_2m"),
        "humidity_pct": current.get("relative_humidity_2m"),
        "rainfall_mm": current.get("rain", current.get("precipitation")),
        "rain_probability_next_days": (max(precip_probs) / 100.0) if precip_probs else None,
        "source": "open-meteo (live)",
    }


def fetch_soil(lat: float, lon: float) -> Dict[str, Any]:
    """Real SoilGrids properties. Only requests properties SoilGrids
    actually provides -- pH, organic carbon, texture. No NPK, no salinity."""
    url = (f"{SOILGRIDS_URL}?lon={lon}&lat={lat}"
           f"&property=phh2o&property=soc&property=sand&property=silt&property=clay"
           f"&depth=0-5cm&value=mean")
    data = _get_json(url, timeout=15)
    result = {"ph": None, "organic_carbon": None, "sand_pct": None,
              "silt_pct": None, "clay_pct": None, "source": "soilgrids (unavailable)"}
    if data is None:
        return result
    try:
        for layer in data["properties"]["layers"]:
            mean = layer["depths"][0]["values"].get("mean")
            if mean is None:
                continue
            if layer["name"] == "phh2o":
                result["ph"] = round(mean / 10.0, 2)
            elif layer["name"] == "soc":
                result["organic_carbon"] = round(mean / 10.0, 2)
            elif layer["name"] == "sand":
                result["sand_pct"] = round(mean / 10.0, 1)
            elif layer["name"] == "silt":
                result["silt_pct"] = round(mean / 10.0, 1)
            elif layer["name"] == "clay":
                result["clay_pct"] = round(mean / 10.0, 1)
        result["source"] = "soilgrids (estimated background value, not an exact field measurement)"
    except (KeyError, IndexError, TypeError):
        pass
    return result


def fetch_nasa_power_climatology(lat: float, lon: float) -> Dict[str, Any]:
    """[FIX 6] Real NASA POWER long-term monthly climatology -- free, no API
    key. Gives an actual climate-normal baseline (temperature, precipitation,
    solar radiation) to compare short-term forecasts against, which is a
    genuine (if partial) substitute for the FAO/NASA layer the architecture
    calls for. Returns None fields if the call fails."""
    params = "T2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN"
    url = (f"{NASA_POWER_CLIMATOLOGY_URL}?parameters={params}&community=AG"
           f"&longitude={lon}&latitude={lat}&format=JSON")
    data = _get_json(url, timeout=15)
    result = {
        "avg_temp_c_annual": None, "avg_precip_mm_month_annual": None,
        "avg_solar_radiation_annual": None, "month_used": None,
        "source": "nasa-power (unavailable)",
    }
    if data is None:
        return result
    try:
        month_key = f"{datetime.now().month:02d}"
        params_data = data["properties"]["parameter"]
        result["avg_temp_c_annual"] = params_data.get("T2M", {}).get(month_key)
        result["avg_precip_mm_month_annual"] = params_data.get("PRECTOTCORR", {}).get(month_key)
        result["avg_solar_radiation_annual"] = params_data.get("ALLSKY_SFC_SW_DWN", {}).get(month_key)
        result["month_used"] = month_key
        result["source"] = "nasa-power (live monthly climatology, long-term average not real-time)"
    except (KeyError, TypeError):
        pass
    return result


def fetch_online_location_data(lat: float, lon: float) -> Dict[str, Any]:
    """Combines real weather + real soil + real NASA POWER climatology.
    soil_n/soil_p/soil_k, satellite_ndvi, soil_salinity and water_salinity
    are explicitly None -- no free, unauthenticated API integrated here
    provides any of them; never invented. [FIX 9]"""
    weather = fetch_weather(lat, lon)
    soil = fetch_soil(lat, lon)
    climate = fetch_nasa_power_climatology(lat, lon)
    return {
        "weather": weather,
        "soil": soil,
        "climate_normal": climate,
        "soil_n": None,
        "soil_p": None,
        "soil_k": None,
        "satellite_ndvi": None,
        "soil_salinity": None,
        "water_salinity": None,
        "unavailable_notes": [
            "soil_n/soil_p/soil_k: no free, unauthenticated API integrated in this "
            "prototype provides field-level NPK -- would require a paid soil-test "
            "service or lab data; left unset rather than invented.",
            "satellite_ndvi: no free, unauthenticated satellite NDVI point-query API "
            "integrated in this prototype (e.g. Sentinel Hub requires an API key/auth) "
            "-- left unset rather than invented.",
            "soil_salinity / water_salinity: no sensor or free API integrated in this "
            "prototype measures either -- left unset rather than invented. Reduce "
            "confidence on salinity-sensitive crop recommendations until real readings "
            "or a legitimate salinity data source are available.",
            "FAO GAEZ / FAOSTAT: neither exposes a free, unauthenticated, point-based "
            "(lat/lon) query API (GAEZ is a portal/download product, FAOSTAT is "
            "country-level bulk data) -- not integrated in this prototype. NASA POWER "
            "climatology is used instead as a partial substitute for climate-normal data.",
        ],
    }


# =============================================================================
# 3. TIME / SEASON CONTEXT  [FIX 10]
# =============================================================================

def get_time_context(lat: float, when: Optional[datetime] = None) -> Dict[str, Any]:
    """Computes datetime/day-of-year/season for the structured intelligence
    layer. Season is a rough meteorological-season heuristic based on month
    and hemisphere (from latitude sign) -- not a substitute for a real local
    agricultural calendar, but far better than omitting time entirely."""
    when = when or datetime.now().astimezone()
    month = when.month
    northern = lat >= 0
    # meteorological seasons (Northern Hemisphere baseline, flipped for South)
    if month in (12, 1, 2):
        season = "winter" if northern else "summer"
    elif month in (3, 4, 5):
        season = "spring" if northern else "autumn"
    elif month in (6, 7, 8):
        season = "summer" if northern else "winter"
    else:
        season = "autumn" if northern else "spring"
    return {
        "datetime": when.isoformat(),
        "day_of_year": when.timetuple().tm_yday,
        "season": season,
        "hemisphere": "northern" if northern else "southern",
    }


# =============================================================================
# 4. AGRICULTURAL STATE NN -- actually trained, not random weights
# =============================================================================
# [FIX 5] Proxy-trained -- see PROXY_DATA_DISCLOSURE. LDR sensor: a LOWER
# percentage reading means MORE brightness on this hardware, so it's
# inverted before use.

STATE_NN_FEATURES = [
    "soil_moisture_pct", "air_temp_c", "air_humidity_pct", "ldr_brightness_norm",
    "soil_ph_norm", "organic_carbon_norm", "rainfall_norm",
]


if TORCH_AVAILABLE:
    class AgriStateNN(nn.Module):
        """3 continuous outputs (0-100): overall_suitability, water_stress_index,
        yield_potential."""
        def __init__(self, input_dim: int = len(STATE_NN_FEATURES), hidden_dim: int = 32):
            super().__init__()
            self.network = nn.Sequential(
                nn.Linear(input_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, 3),
                nn.Sigmoid(),
            )

        def forward(self, x):
            return self.network(x) * 100.0
else:
    class AgriStateNN:                        # placeholder, never constructed without torch
        def __init__(self, *args, **kwargs):
            _require_torch()


def _state_label_row(f: dict) -> list:
    """Transparent agronomic rule function -- proxy labels, not measured
    ground truth."""
    moisture, temp = f["soil_moisture_pct"], f["air_temp_c"]
    brightness, ph, oc, rain = (f["ldr_brightness_norm"], f["soil_ph_norm"],
                                 f["organic_carbon_norm"], f["rainfall_norm"])

    heat_penalty = max(0, temp - 30) * 2.5
    moisture_score = 100 - abs(moisture - 55) * 1.2
    suitability = max(0, min(100, 0.4 * moisture_score + 0.3 * (100 - heat_penalty)
                              + 0.15 * brightness * 100 + 0.15 * (100 - abs(ph - 6.5) * 15)))

    water_stress = max(0, min(100, (40 - moisture) * 2.5)) if moisture < 40 else 0.0

    yield_potential = max(0, min(100, 0.5 * suitability + 0.3 * (oc * 100) + 0.2 * (rain * 100)))
    return [suitability, water_stress, yield_potential]


def _state_sample_row() -> dict:
    return {
        "soil_moisture_pct": random.uniform(2, 95),
        "air_temp_c": random.uniform(10, 48),
        "air_humidity_pct": random.uniform(5, 95),
        "ldr_brightness_norm": random.uniform(0.05, 1.0),
        "soil_ph_norm": random.uniform(4.5, 9.0),
        "organic_carbon_norm": random.uniform(0.02, 0.9),
        "rainfall_norm": random.uniform(0, 1),
    }


def _train_state_nn(n_rows: int = 2000, epochs: int = 150) -> "AgriStateNN":
    _require_torch()
    rows = [_state_sample_row() for _ in range(n_rows)]
    X = torch.tensor([[r[k] for k in STATE_NN_FEATURES] for r in rows], dtype=torch.float32)
    y = torch.tensor([_state_label_row(r) for r in rows], dtype=torch.float32)

    mean, std = X.mean(0), X.std(0)
    Xn = (X - mean) / std

    model = AgriStateNN()
    opt = torch.optim.Adam(model.parameters(), lr=1e-2)
    loss_fn = nn.MSELoss()
    for _ in range(epochs):
        opt.zero_grad()
        pred = model(Xn)
        loss = loss_fn(pred, y)
        loss.backward()
        opt.step()

    model.eval()
    model._input_mean = mean
    model._input_std = std
    print(f"[AgriStateNN] quick-trained on {n_rows} proxy rows, final MSE loss={loss.item():.2f}")
    return model


# [INTEGRATION 2] was: STATE_NN_MODEL = _train_state_nn() at import time.
STATE_NN_MODEL: Optional["AgriStateNN"] = None
_STATE_MODEL_LOCK = threading.Lock()


def _ensure_state_model() -> "AgriStateNN":
    """Trains the state NN once on first use, then caches it. Doing this at
    import time (as the standalone build did) blocked whichever process
    imported the module for the whole training run."""
    global STATE_NN_MODEL
    if STATE_NN_MODEL is None:
        with _STATE_MODEL_LOCK:
            if STATE_NN_MODEL is None:
                STATE_NN_MODEL = _train_state_nn()
    return STATE_NN_MODEL


def predict_state(soil_moisture_pct, air_temp_c, air_humidity_pct, ldr_pct,
                   soil_ph=6.5, organic_carbon_pct=None, rainfall_mm=0.0) -> dict:
    """ldr_pct: raw LDR sensor percentage reading -- LOWER percentage means
    MORE brightness on this hardware, inverted here to a brightness fraction."""
    model = _ensure_state_model()
    brightness = 1.0 - min(1.0, max(0.0, ldr_pct / 100.0))
    features = {
        "soil_moisture_pct": soil_moisture_pct,
        "air_temp_c": air_temp_c,
        "air_humidity_pct": air_humidity_pct,
        "ldr_brightness_norm": brightness,
        "soil_ph_norm": soil_ph if soil_ph is not None else 6.5,
        "organic_carbon_norm": min(1.0, (organic_carbon_pct or 5.0) / 20.0),
        "rainfall_norm": min(1.0, rainfall_mm / 50.0),
    }
    x = torch.tensor([[features[k] for k in STATE_NN_FEATURES]], dtype=torch.float32)
    xn = (x - model._input_mean) / model._input_std
    with torch.no_grad():
        out = model(xn)[0].tolist()
    return {
        "overall_suitability": round(out[0], 1),
        "water_stress_index": round(out[1], 1),
        "yield_potential": round(out[2], 1),
        "model_status": "quick-trained proxy model -- see PROXY_DATA_DISCLOSURE",
    }


# =============================================================================
# 5. WEATHER CORRECTION NN + LEARNING LOOP  [FIX 4]
# =============================================================================
# Local bias-correction layer on top of the raw Open-Meteo forecast. Never a
# replacement for professional meteorological forecasting. Starts
# quick-trained on a proxy dataset (see PROXY_DATA_DISCLOSURE), but now has
# a real forecast-vs-actual history table + a retrain hook so it genuinely
# improves on local data over time, which is what makes the "the system
# learns and improves its local predictions" claim legitimate.

WEATHER_NN_FEATURES = ["forecast_temp_c", "forecast_humidity_pct", "forecast_rain_prob", "recent_error_temp"]


if TORCH_AVAILABLE:
    class WeatherCorrectionNN(nn.Module):
        def __init__(self, input_dim: int = len(WEATHER_NN_FEATURES), hidden_dim: int = 16):
            super().__init__()
            self.net = nn.Sequential(nn.Linear(input_dim, hidden_dim), nn.ReLU())
            self.temp_correction = nn.Linear(hidden_dim, 1)
            self.humidity_correction = nn.Linear(hidden_dim, 1)
            self.rain_prob = nn.Linear(hidden_dim, 1)
            self.confidence = nn.Linear(hidden_dim, 1)

        def forward(self, x):
            h = self.net(x)
            return (self.temp_correction(h), self.humidity_correction(h),
                    torch.sigmoid(self.rain_prob(h)), torch.sigmoid(self.confidence(h)))
else:
    class WeatherCorrectionNN:                # placeholder, never constructed without torch
        def __init__(self, *args, **kwargs):
            _require_torch()


def _weather_sample_row():
    forecast_temp = random.uniform(10, 48)
    actual_temp = forecast_temp - max(0, forecast_temp - 35) * 0.15 + random.gauss(0, 1.2)
    forecast_humidity = random.uniform(5, 95)
    actual_humidity = forecast_humidity + random.gauss(0, 4)
    forecast_rain_prob = random.uniform(0, 1)
    actual_rain = 1.0 if random.random() < forecast_rain_prob * 0.85 else 0.0
    return {
        "forecast_temp_c": forecast_temp, "forecast_humidity_pct": forecast_humidity,
        "forecast_rain_prob": forecast_rain_prob, "recent_error_temp": random.gauss(0, 1),
        "actual_temp_c": actual_temp, "actual_humidity_pct": actual_humidity, "actual_rain": actual_rain,
    }


def _train_weather_nn(n_rows=1500, epochs=150, extra_rows: Optional[List[dict]] = None):
    _require_torch()
    rows = [_weather_sample_row() for _ in range(n_rows)]
    if extra_rows:
        # [FIX 4] Real logged forecast/actual pairs, once we have enough of
        # them, are blended in alongside the synthetic proxy rows so the
        # model starts anchoring to real local error patterns rather than
        # only the synthetic rule.
        rows.extend(extra_rows)
    X = torch.tensor([[r[k] for k in WEATHER_NN_FEATURES] for r in rows], dtype=torch.float32)
    mean, std = X.mean(0), X.std(0)
    Xn = (X - mean) / std
    t_temp = torch.tensor([[r["actual_temp_c"] - r["forecast_temp_c"]] for r in rows], dtype=torch.float32)
    t_hum = torch.tensor([[r["actual_humidity_pct"] - r["forecast_humidity_pct"]] for r in rows], dtype=torch.float32)
    t_rain = torch.tensor([[r["actual_rain"]] for r in rows], dtype=torch.float32)

    model = WeatherCorrectionNN()
    opt = torch.optim.Adam(model.parameters(), lr=1e-2)
    mse, bce = nn.MSELoss(), nn.BCELoss()
    for _ in range(epochs):
        opt.zero_grad()
        tc, hc, rp, conf = model(Xn)
        loss = mse(tc, t_temp) + mse(hc, t_hum) + bce(rp, t_rain)
        loss.backward()
        opt.step()
    model.eval()
    model._input_mean, model._input_std = mean, std
    print(f"[WeatherCorrectionNN] trained on {len(rows)} rows "
          f"({len(extra_rows) if extra_rows else 0} real) final loss={loss.item():.3f}")
    return model


# [INTEGRATION 2] was: WEATHER_NN_MODEL = _train_weather_nn() at import time.
WEATHER_NN_MODEL: Optional["WeatherCorrectionNN"] = None
_WEATHER_MODEL_LOCK = threading.Lock()


def _ensure_weather_model() -> "WeatherCorrectionNN":
    global WEATHER_NN_MODEL
    if WEATHER_NN_MODEL is None:
        with _WEATHER_MODEL_LOCK:
            if WEATHER_NN_MODEL is None:
                WEATHER_NN_MODEL = _train_weather_nn()
    return WEATHER_NN_MODEL


def correct_forecast(forecast_temp_c, forecast_humidity_pct, forecast_rain_prob, recent_error_temp=0.0) -> dict:
    if forecast_temp_c is None:
        return {"corrected_temp_c": None, "corrected_humidity_pct": None,
                "rain_probability": None, "confidence": 0.0,
                "note": "no forecast available to correct"}
    model = _ensure_weather_model()
    x = torch.tensor([[forecast_temp_c, forecast_humidity_pct or 50.0,
                        forecast_rain_prob or 0.0, recent_error_temp]], dtype=torch.float32)
    xn = (x - model._input_mean) / model._input_std
    with torch.no_grad():
        tc, hc, rp, conf = model(xn)
    heat_risk = "high" if (forecast_temp_c + tc.item()) > 38 else \
                ("moderate" if (forecast_temp_c + tc.item()) > 30 else "low")
    return {
        "corrected_temp_c": round(forecast_temp_c + tc.item(), 1),
        "corrected_humidity_pct": round((forecast_humidity_pct or 50.0) + hc.item(), 1),
        "rain_probability": round(rp.item(), 2),
        "heat_risk": heat_risk,
        "confidence": round(conf.item(), 2),
        "model_status": "see PROXY_DATA_DISCLOSURE -- local bias correction only, "
                         "not a substitute for professional forecasting",
    }


def log_forecast_for_later_verification(lat: float, lon: float, forecast: dict) -> int:
    """[FIX 4] Call this right after fetch_weather() at pipeline time. Stores
    the raw forecast so it can be compared against what actually happened
    once it's known (e.g. tomorrow), which is what the learning loop needs.
    Returns the row id to pass to record_actual_weather() later."""
    conn = _init_db()
    cur = conn.execute(
        "INSERT INTO weather_forecast_log "
        "(timestamp, latitude, longitude, forecast_temp_c, forecast_humidity_pct, "
        "forecast_rain_prob, resolved) VALUES (?,?,?,?,?,?,0)",
        (datetime.now(timezone.utc).isoformat(), lat, lon,
         forecast.get("temperature_c"), forecast.get("humidity_pct"),
         forecast.get("rain_probability_next_days")),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def record_actual_weather(log_row_id: int, actual_temp_c: float, actual_humidity_pct: float, actual_rain: bool) -> None:
    """[FIX 4] Called later (e.g. the next scheduled run) once the real
    outcome for a previously logged forecast is known -- typically the next
    run's own sensor/weather reading for the same site. This is what turns
    the log into real forecast-vs-actual training data."""
    conn = _init_db()
    conn.execute(
        "UPDATE weather_forecast_log SET actual_temp_c=?, actual_humidity_pct=?, "
        "actual_rain=?, resolved=1 WHERE id=?",
        (actual_temp_c, actual_humidity_pct, int(actual_rain), log_row_id),
    )
    conn.commit()
    conn.close()


def retrain_weather_nn_from_history(min_resolved_rows: int = 30) -> bool:
    """[FIX 4] Pulls every resolved forecast/actual pair logged so far and
    retrains the Weather Correction NN with them blended into the proxy
    dataset. Returns False (no-op) until there's enough real history to be
    worth it, so a fresh install doesn't retrain on 2 noisy rows."""
    conn = _init_db()
    cur = conn.execute(
        "SELECT forecast_temp_c, forecast_humidity_pct, forecast_rain_prob, "
        "actual_temp_c, actual_humidity_pct, actual_rain FROM weather_forecast_log "
        "WHERE resolved=1 AND forecast_temp_c IS NOT NULL AND actual_temp_c IS NOT NULL"
    )
    rows = cur.fetchall()
    conn.close()
    if len(rows) < min_resolved_rows:
        print(f"[WeatherCorrectionNN] only {len(rows)} resolved real rows logged "
              f"(need {min_resolved_rows}) -- skipping retrain for now.")
        return False

    extra_rows = [{
        "forecast_temp_c": r[0], "forecast_humidity_pct": r[1] or 50.0,
        "forecast_rain_prob": r[2] or 0.1, "recent_error_temp": 0.0,
        "actual_temp_c": r[3], "actual_humidity_pct": r[4] or 50.0, "actual_rain": float(r[5] or 0),
    } for r in rows]

    global WEATHER_NN_MODEL
    WEATHER_NN_MODEL = _train_weather_nn(extra_rows=extra_rows)
    print(f"[WeatherCorrectionNN] retrained using {len(extra_rows)} real logged forecast/actual pairs.")
    return True


# =============================================================================
# 6. CROP KNOWLEDGE BASE (RAG, dependency-free)  [FIX 7, FIX 8]
# =============================================================================
# Expanded coverage + a "regions" tag per crop so retrieval can filter by
# region as well as climate/soil/salinity. Still a manually curated fact
# sheet, not a vector database -- facts drawn from well-established,
# publicly known agronomic guidance (FAO-style crop requirement ranges).
# retrieve_relevant_crops() is a CANDIDATE filter/ranker for Qwen to reason
# over, not the final crop decision.

CROP_KNOWLEDGE_BASE: Dict[str, Dict[str, Any]] = {
    "pearl_millet": {
        "temp_range_c": (25, 40), "water_requirement": "low", "soil_ph_range": (5.5, 8.0),
        "salinity_tolerance": "high", "light_requirement": "full sun",
        "planting_season": "start of rains / hot season", "drought_tolerant": True,
        "regions": ["sahel", "west_africa", "south_asia", "desert"],
        "common_diseases": ["downy mildew", "ergot"], "common_stresses": ["bird damage", "heat stress"],
        "harvest_indicators": "grain heads dry and hard, birds start feeding heavily",
    },
    "sorghum": {
        "temp_range_c": (25, 38), "water_requirement": "low-moderate", "soil_ph_range": (5.5, 8.5),
        "salinity_tolerance": "moderate-high", "light_requirement": "full sun",
        "planting_season": "start of rains", "drought_tolerant": True,
        "regions": ["sahel", "east_africa", "south_asia", "desert"],
        "common_diseases": ["anthracnose", "grain mold"], "common_stresses": ["waterlogging in heavy soils"],
        "harvest_indicators": "grain hard, moisture below ~20%",
    },
    "cowpea": {
        "temp_range_c": (20, 35), "water_requirement": "low-moderate", "soil_ph_range": (5.5, 7.5),
        "salinity_tolerance": "moderate", "light_requirement": "full sun",
        "planting_season": "after last frost / start of warm season", "drought_tolerant": True,
        "regions": ["west_africa", "east_africa", "south_asia"],
        "common_diseases": ["bacterial blight", "cowpea mosaic virus"], "common_stresses": ["aphids", "pod borers"],
        "harvest_indicators": "pods dry and brittle, seeds rattle inside",
    },
    "date_palm": {
        "temp_range_c": (25, 45), "water_requirement": "low (deep roots)", "soil_ph_range": (6.0, 8.5),
        "salinity_tolerance": "high", "light_requirement": "full sun",
        "planting_season": "spring", "drought_tolerant": True,
        "regions": ["desert", "middle_east", "north_africa"],
        "common_diseases": ["bayoud disease", "red palm weevil"], "common_stresses": ["frost (young palms)"],
        "harvest_indicators": "fruit color change and softening, 5-7 months after pollination",
        "note": "long-term investment, several years before first significant harvest",
    },
    "chickpea": {
        "temp_range_c": (15, 30), "water_requirement": "low", "soil_ph_range": (6.0, 8.0),
        "salinity_tolerance": "low-moderate", "light_requirement": "full sun",
        "planting_season": "cool season", "drought_tolerant": True,
        "regions": ["south_asia", "middle_east", "mediterranean"],
        "common_diseases": ["ascochyta blight", "fusarium wilt"], "common_stresses": ["frost sensitivity"],
        "harvest_indicators": "leaves yellow/drop, pods dry",
    },
    "tomato": {
        "temp_range_c": (18, 30), "water_requirement": "high, consistent", "soil_ph_range": (6.0, 6.8),
        "salinity_tolerance": "low", "light_requirement": "full sun",
        "planting_season": "after last frost", "drought_tolerant": False,
        "regions": ["temperate", "mediterranean", "south_asia"],
        "common_diseases": ["blossom end rot (calcium/water irregularity)", "early blight", "fusarium wilt"],
        "common_stresses": ["heat stress above ~35C reduces fruit set", "irregular watering"],
        "harvest_indicators": "fruit fully colored, slightly firm",
    },
    "okra": {
        "temp_range_c": (22, 35), "water_requirement": "moderate", "soil_ph_range": (6.0, 7.5),
        "salinity_tolerance": "moderate", "light_requirement": "full sun",
        "planting_season": "warm season", "drought_tolerant": False,
        "regions": ["south_asia", "west_africa", "middle_east"],
        "common_diseases": ["powdery mildew", "yellow vein mosaic virus"], "common_stresses": ["heat above 40C stunts growth"],
        "harvest_indicators": "pods 7-10cm, still tender, harvest every 2 days",
    },
    "olive": {
        "temp_range_c": (15, 35), "water_requirement": "low once established", "soil_ph_range": (6.0, 8.5),
        "salinity_tolerance": "high", "light_requirement": "full sun",
        "planting_season": "spring or autumn", "drought_tolerant": True,
        "regions": ["mediterranean", "middle_east", "north_africa"],
        "common_diseases": ["olive knot", "verticillium wilt"], "common_stresses": ["needs winter chill for fruit set"],
        "harvest_indicators": "fruit color shift green to purple/black depending on variety",
        "note": "long-term investment, 3-5+ years to significant production",
    },
    "watermelon": {
        "temp_range_c": (22, 35), "water_requirement": "high at fruiting", "soil_ph_range": (6.0, 7.0),
        "salinity_tolerance": "low-moderate", "light_requirement": "full sun",
        "planting_season": "warm season", "drought_tolerant": False,
        "regions": ["desert", "south_asia", "mediterranean"],
        "common_diseases": ["fusarium wilt", "powdery mildew"], "common_stresses": ["irregular watering causes fruit splitting"],
        "harvest_indicators": "tendril near fruit browns/dries, dull skin, hollow sound when tapped",
    },
    "quinoa": {
        "temp_range_c": (10, 30), "water_requirement": "low", "soil_ph_range": (6.0, 8.5),
        "salinity_tolerance": "high", "light_requirement": "full sun",
        "planting_season": "cool-mild season", "drought_tolerant": True,
        "regions": ["andes", "temperate", "desert"],
        "common_diseases": ["downy mildew"], "common_stresses": ["poor germination in very hot soil"],
        "harvest_indicators": "seed heads dry, seeds resist thumbnail dent",
    },
    "spinach": {
        "temp_range_c": (10, 24), "water_requirement": "moderate, consistent", "soil_ph_range": (6.0, 7.5),
        "salinity_tolerance": "low", "light_requirement": "partial shade tolerant",
        "planting_season": "cool season", "drought_tolerant": False,
        "regions": ["temperate", "south_asia"],
        "common_diseases": ["downy mildew"], "common_stresses": ["bolts (flowers early) in heat"],
        "harvest_indicators": "leaves reach full size before bolting begins",
    },
    "lettuce": {
        "temp_range_c": (8, 22), "water_requirement": "moderate, consistent", "soil_ph_range": (6.0, 7.0),
        "salinity_tolerance": "low", "light_requirement": "partial shade in heat",
        "planting_season": "cool season", "drought_tolerant": False,
        "regions": ["temperate", "mediterranean"],
        "common_diseases": ["downy mildew", "tip burn (calcium/water irregularity)"],
        "common_stresses": ["bolts and turns bitter above ~24C"],
        "harvest_indicators": "head firm to touch or outer leaves large enough to harvest",
    },
    "maize": {
        "temp_range_c": (18, 35), "water_requirement": "moderate-high", "soil_ph_range": (5.5, 7.5),
        "salinity_tolerance": "low-moderate", "light_requirement": "full sun",
        "planting_season": "start of rains / warm season", "drought_tolerant": False,
        "regions": ["east_africa", "west_africa", "south_asia", "temperate"],
        "common_diseases": ["maize streak virus", "grey leaf spot"], "common_stresses": ["drought during tasseling"],
        "harvest_indicators": "husks dry and brown, kernels hard and dented",
    },
    "rice": {
        "temp_range_c": (20, 37), "water_requirement": "very high (paddy)", "soil_ph_range": (5.0, 7.0),
        "salinity_tolerance": "low (except specific saline-tolerant varieties)", "light_requirement": "full sun",
        "planting_season": "monsoon / wet season", "drought_tolerant": False,
        "regions": ["south_asia", "southeast_asia"],
        "common_diseases": ["rice blast", "bacterial leaf blight"], "common_stresses": ["water shortage during flowering"],
        "harvest_indicators": "80-85% of grains golden yellow, panicles droop",
    },
    "cassava": {
        "temp_range_c": (20, 38), "water_requirement": "low", "soil_ph_range": (5.0, 7.5),
        "salinity_tolerance": "low", "light_requirement": "full sun",
        "planting_season": "start of rains", "drought_tolerant": True,
        "regions": ["west_africa", "east_africa", "southeast_asia"],
        "common_diseases": ["cassava mosaic disease", "cassava brown streak"], "common_stresses": ["poor growth in waterlogged soil"],
        "harvest_indicators": "leaves yellow and drop, roots reach size 8-18 months after planting",
    },
    "groundnut": {
        "temp_range_c": (22, 33), "water_requirement": "low-moderate", "soil_ph_range": (5.5, 7.0),
        "salinity_tolerance": "low", "light_requirement": "full sun",
        "planting_season": "start of rains", "drought_tolerant": True,
        "regions": ["west_africa", "south_asia", "east_africa"],
        "common_diseases": ["leaf spot", "aflatoxin (post-harvest storage risk)"], "common_stresses": ["drought during pod-fill"],
        "harvest_indicators": "leaves yellowing, pods have dark veining inside the shell",
    },
    "sesame": {
        "temp_range_c": (25, 40), "water_requirement": "low", "soil_ph_range": (5.5, 8.0),
        "salinity_tolerance": "moderate", "light_requirement": "full sun",
        "planting_season": "warm/dry season", "drought_tolerant": True,
        "regions": ["sahel", "east_africa", "south_asia", "desert"],
        "common_diseases": ["phytophthora blight", "leaf spot"], "common_stresses": ["waterlogging"],
        "harvest_indicators": "lower leaves yellow, capsules turn brown and start to split",
    },
    "banana": {
        "temp_range_c": (20, 35), "water_requirement": "high, consistent", "soil_ph_range": (5.5, 7.0),
        "salinity_tolerance": "low", "light_requirement": "full sun",
        "planting_season": "any season with irrigation", "drought_tolerant": False,
        "regions": ["southeast_asia", "east_africa", "tropical"],
        "common_diseases": ["panama disease", "black sigatoka"], "common_stresses": ["wind damage", "drought stress"],
        "harvest_indicators": "fruit fingers fill out and round, slight yellow-green color break",
        "note": "long-term investment, ~9-12 months to first harvest after planting",
    },
    "barley": {
        "temp_range_c": (12, 28), "water_requirement": "low-moderate", "soil_ph_range": (6.0, 8.5),
        "salinity_tolerance": "high", "light_requirement": "full sun",
        "planting_season": "cool season", "drought_tolerant": True,
        "regions": ["middle_east", "mediterranean", "temperate", "desert"],
        "common_diseases": ["powdery mildew", "net blotch"], "common_stresses": ["lodging in heavy rain"],
        "harvest_indicators": "grain hard, straw and heads golden",
    },
    "sunflower": {
        "temp_range_c": (18, 35), "water_requirement": "moderate", "soil_ph_range": (6.0, 8.0),
        "salinity_tolerance": "moderate-high", "light_requirement": "full sun",
        "planting_season": "warm season", "drought_tolerant": True,
        "regions": ["temperate", "mediterranean", "east_africa"],
        "common_diseases": ["downy mildew", "sclerotinia head rot"], "common_stresses": ["bird damage at maturity"],
        "harvest_indicators": "back of head turns brown/yellow, seeds firm and striped",
    },
    "cotton": {
        "temp_range_c": (20, 38), "water_requirement": "moderate-high", "soil_ph_range": (5.5, 8.0),
        "salinity_tolerance": "moderate-high", "light_requirement": "full sun",
        "planting_season": "warm season", "drought_tolerant": True,
        "regions": ["south_asia", "west_africa", "desert"],
        "common_diseases": ["bacterial blight", "verticillium wilt"], "common_stresses": ["boll rot in high humidity"],
        "harvest_indicators": "bolls split open, fibers fluffy and white",
    },
    "mango": {
        "temp_range_c": (24, 40), "water_requirement": "low once established", "soil_ph_range": (5.5, 7.5),
        "salinity_tolerance": "moderate", "light_requirement": "full sun",
        "planting_season": "start of rains", "drought_tolerant": True,
        "regions": ["south_asia", "southeast_asia", "east_africa", "desert"],
        "common_diseases": ["anthracnose", "powdery mildew"], "common_stresses": ["fruit drop under water stress at flowering"],
        "regions_note": "tree crop",
        "common_diseases_note": "",
        "harvest_indicators": "fruit shoulders fill out, skin color shifts, slight aroma at stem end",
        "note": "long-term investment, 3-5+ years to significant production",
    },
}


def retrieve_relevant_crops(avg_temp_c: float, water_availability: str,
                             soil_ph: float = None, region: Optional[str] = None,
                             soil_salinity: Optional[str] = None,
                             top_n: int = 5) -> List[Dict[str, Any]]:
    """[FIX 8] CANDIDATE retrieval, not a final decision -- filters/ranks
    crops for Qwen to reason over on top of. If `region` is given, crops not
    tagged with it are excluded before scoring; otherwise all crops are
    considered (keeps this usable globally, not just for the regions
    currently in the KB). Salinity tolerance is folded into the score only
    when we actually have a salinity reading -- unmeasured salinity should
    not silently bias the ranking."""
    water_rank = {"low": 0, "moderate": 1, "high": 2}
    current_water_rank = water_rank.get(water_availability, 1)
    salinity_rank = {"low": 0, "moderate": 1, "moderate-high": 2, "high": 2}

    pool = CROP_KNOWLEDGE_BASE.items()
    if region:
        pool = [(n, i) for n, i in pool if region in i.get("regions", [])]
        if not pool:
            pool = CROP_KNOWLEDGE_BASE.items()  # region had no matches -- fall back to full KB

    scored = []
    for name, info in pool:
        tmin, tmax = info["temp_range_c"]
        temp_fit = 1.0 if tmin <= avg_temp_c <= tmax else max(0.0, 1.0 - abs(avg_temp_c - (tmin if avg_temp_c < tmin else tmax)) / 15)

        crop_water_need = info["water_requirement"]
        crop_water_rank = 0 if "low" in crop_water_need else (2 if "high" in crop_water_need else 1)
        water_fit = 1.0 - abs(crop_water_rank - current_water_rank) / 2.0

        ph_fit = 1.0
        if soil_ph is not None:
            pmin, pmax = info["soil_ph_range"]
            ph_fit = 1.0 if pmin <= soil_ph <= pmax else max(0.0, 1.0 - abs(soil_ph - (pmin if soil_ph < pmin else pmax)) / 2.0)

        if soil_salinity is not None:
            crop_sal_rank = salinity_rank.get(info.get("salinity_tolerance", "low"), 0)
            target_sal_rank = salinity_rank.get(soil_salinity, 0)
            salinity_fit = 1.0 - max(0, target_sal_rank - crop_sal_rank) / 2.0
            score = 0.40 * temp_fit + 0.30 * water_fit + 0.15 * ph_fit + 0.15 * salinity_fit
        else:
            score = 0.45 * temp_fit + 0.35 * water_fit + 0.20 * ph_fit

        scored.append((score, name, info))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [{"crop": name, "candidate_fit_score": round(score, 2), **info} for score, name, info in scored[:top_n]]


# =============================================================================
# 7. DETERMINISTIC IRRIGATION CONTROLLER + SAFETY LAYER  [FIX 15]
# =============================================================================
# Qwen NEVER calls this directly, and this NEVER trusts Qwen's text as the
# final word. Per current scope, this only produces a recommendation/log
# entry -- there is intentionally no ultrasonic reservoir sensor and no pump
# control wired up here.

IRRIGATION_VALID_RANGES = {
    "soil_moisture_pct": (0, 100),
    "air_temp_c": (-10, 60),
    "air_humidity_pct": (0, 100),
}

# [FIX 15] Maximum plausible change per second for each sensor, used to flag
# implausible jumps (e.g. a soil moisture sensor reading 42% then 97% thirty
# seconds later with no irrigation event in between).
MAX_PLAUSIBLE_RATE_PER_SEC = {
    "soil_moisture_pct": 0.5,   # %/sec -- generous upper bound for real soil
    "air_temp_c": 0.05,         # C/sec
    "air_humidity_pct": 0.5,    # %/sec
}


def validate_sensors(readings: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """A None reading or an out-of-range reading is marked invalid -- never
    substituted with a guessed value."""
    validity = {}
    for key, (lo, hi) in IRRIGATION_VALID_RANGES.items():
        value = readings.get(key)
        validity[key] = value is not None and lo <= value <= hi
    return {"validity": validity, "all_critical_valid": validity.get("soil_moisture_pct", False)}


def check_sensor_rate_of_change(current: Dict[str, Optional[float]],
                                 previous: Optional[Dict[str, Optional[float]]],
                                 elapsed_seconds: Optional[float]) -> Dict[str, Any]:
    """[FIX 15] Flags a reading that changed faster than physically
    plausible given how much time passed since the last reading. Returns
    per-field flags plus an overall `suspicious` bool. Never blocks the
    pipeline by itself -- the caller decides how to react (e.g. treat as
    invalid, log a warning, request a re-read)."""
    if not previous or not elapsed_seconds or elapsed_seconds <= 0:
        return {"suspicious": False, "flags": {}, "note": "no prior reading to compare against"}

    flags = {}
    for key, max_rate in MAX_PLAUSIBLE_RATE_PER_SEC.items():
        cur_val, prev_val = current.get(key), previous.get(key)
        if cur_val is None or prev_val is None:
            continue
        max_allowed_delta = max_rate * elapsed_seconds
        actual_delta = abs(cur_val - prev_val)
        flags[key] = {
            "delta": round(actual_delta, 2),
            "max_plausible_delta": round(max_allowed_delta, 2),
            "flagged": actual_delta > max_allowed_delta,
        }
    suspicious = any(f["flagged"] for f in flags.values())
    return {"suspicious": suspicious, "flags": flags, "elapsed_seconds": elapsed_seconds}


def irrigation_decision(
    soil_moisture_pct: Optional[float],
    crop_moisture_threshold_pct: float,
    rain_probability: Optional[float],
    qwen_irrigation_strategy: Optional[dict] = None,
    rate_of_change_check: Optional[dict] = None,
) -> Dict[str, Any]:
    """Deterministic irrigation logic. Final authority -- Qwen's strategy is
    advisory input only, never executed directly. 'message' is a short
    human-readable line for a dashboard/notification. If the rate-of-change
    check flags the soil moisture reading, irrigation is skipped even if the
    raw value is technically in-range, per the [FIX 15] safety rule."""
    validation = validate_sensors({"soil_moisture_pct": soil_moisture_pct})

    if not validation["all_critical_valid"]:
        return {"action": "skip",
                "reason": "invalid or missing soil moisture reading -- safety rule: "
                          "never irrigate on unverified data",
                "message": "⚠️ Irrigation skipped — soil moisture sensor reading is missing or invalid.",
                "validation": validation}

    if rate_of_change_check and rate_of_change_check.get("suspicious") and \
            rate_of_change_check["flags"].get("soil_moisture_pct", {}).get("flagged"):
        return {"action": "skip",
                "reason": "soil moisture reading changed faster than physically plausible "
                          "since the last reading -- treated as unverified, not irrigated on",
                "message": "⚠️ Irrigation skipped — soil moisture reading looks implausible "
                           "(sensor fault suspected). Re-check the sensor.",
                "validation": validation, "rate_of_change_check": rate_of_change_check}

    if soil_moisture_pct >= crop_moisture_threshold_pct:
        if rain_probability is not None and rain_probability > 0.5:
            return {"action": "skip",
                     "reason": f"soil moisture {soil_moisture_pct}% already at/above threshold "
                               f"{crop_moisture_threshold_pct}% and rain likely ({rain_probability:.0%}) "
                               f"-- skip to avoid overwatering",
                     "message": f"✅ No irrigation needed — soil moisture is sufficient ({soil_moisture_pct}%) "
                                f"and rain is likely.",
                     "validation": validation}
        return {"action": "skip",
                 "reason": f"soil moisture {soil_moisture_pct}% at/above threshold "
                           f"{crop_moisture_threshold_pct}% -- no irrigation needed",
                 "message": f"✅ No irrigation needed — soil moisture is sufficient ({soil_moisture_pct}%).",
                 "validation": validation}

    if rain_probability is not None and rain_probability > 0.7:
        return {"action": "skip",
                 "reason": f"soil moisture {soil_moisture_pct}% below threshold, but rain probability "
                           f"{rain_probability:.0%} is high -- skip and re-check after rain",
                 "message": f"⏳ Irrigation held off — moisture is low ({soil_moisture_pct}%) but rain is "
                            f"likely ({rain_probability:.0%}), rechecking after.",
                 "validation": validation}

    return {"action": "irrigate",
             "reason": f"soil moisture {soil_moisture_pct}% below threshold {crop_moisture_threshold_pct}%, "
                       f"rain unlikely -- safe to irrigate",
             "message": f"💧 Irrigation needed — soil moisture is low ({soil_moisture_pct}%, below the "
                        f"{crop_moisture_threshold_pct}% threshold). Starting irrigation.",
             "validation": validation,
             "qwen_strategy_considered": qwen_irrigation_strategy}


# =============================================================================
# 8. PLANT COLOUR ANALYZER (rule-based, NOT a neural network) + CAMERA
# =============================================================================
# [FIX 3] The camera is no longer skipped based on soil moisture. It
# captures and analyzes an image on every pipeline run -- a plant can have
# disease, heat damage, or nutrient problems while soil moisture looks
# normal, so gating the camera on moisture was hiding real symptoms.

class PlantPixelAnalyzer:
    def __init__(self, grid_size: int = 16):
        self.grid_size = grid_size

    def analyze(self, image_bgr) -> Dict[str, float]:
        _require_vision()
        h, w, _ = image_bgr.shape
        small = cv2.resize(image_bgr, (self.grid_size, self.grid_size), interpolation=cv2.INTER_LINEAR)
        pixelated = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        hsv = cv2.cvtColor(pixelated, cv2.COLOR_BGR2HSV)
        total_px = hsv.shape[0] * hsv.shape[1]

        green = (np.sum(cv2.inRange(hsv, np.array([35, 40, 40]), np.array([85, 255, 255])) > 0) / total_px) * 100
        yellow = (np.sum(cv2.inRange(hsv, np.array([20, 40, 40]), np.array([34, 255, 255])) > 0) / total_px) * 100
        brown = (np.sum(cv2.inRange(hsv, np.array([10, 40, 20]), np.array([19, 255, 200])) > 0) / total_px) * 100
        necrotic = (np.sum(cv2.inRange(hsv, np.array([0, 0, 0]), np.array([180, 255, 30])) > 0) / total_px) * 100
        other = max(0.0, 100 - (green + yellow + brown + necrotic))

        return {"green_pct": round(green, 1), "yellow_pct": round(yellow, 1),
                "brown_pct": round(brown, 1), "necrotic_pct": round(necrotic, 1),
                "other_pct": round(other, 1)}


def plant_stress_rules(vision: dict, soil_moisture_pct: float, air_temp_c: float) -> dict:
    """Uncertainty-scored stress rules -- rule-based, never presented as a
    neural-network prediction."""
    water_stress = 0.0
    if vision["yellow_pct"] > 15 and soil_moisture_pct < 35:
        water_stress = min(1.0, 0.4 + (35 - soil_moisture_pct) / 50 + vision["yellow_pct"] / 100)

    heat_stress = 0.0
    if vision["brown_pct"] > 10 and air_temp_c > 35:
        heat_stress = min(1.0, 0.3 + (air_temp_c - 35) / 30 + vision["brown_pct"] / 100)

    disease_suspicion = 0.0
    unexplained_color = vision["necrotic_pct"] > 8 or (vision["yellow_pct"] > 20 and soil_moisture_pct >= 35 and air_temp_c <= 35)
    if unexplained_color:
        disease_suspicion = min(1.0, 0.3 + vision["necrotic_pct"] / 50)

    confidence = 0.5 + 0.1 * sum([water_stress > 0, heat_stress > 0, disease_suspicion > 0])
    return {
        "water_stress_suspicion": round(water_stress, 2),
        "heat_stress_suspicion": round(heat_stress, 2),
        "disease_suspicion": round(disease_suspicion, 2),
        "confidence": round(min(confidence, 0.9), 2),
        "source": "rule_based_estimate_not_neural_network",
    }


class Camera:
    def __init__(self, camera_index: int = 0):
        self.camera_index = camera_index

    def capture_frame(self, save_dir: Optional[str] = None) -> Tuple[Any, str, bool]:
        """Returns (frame, path, is_real_capture). is_real_capture is False
        for the synthetic fallback -- never silently identical to a real photo."""
        _require_vision()
        save_dir = save_dir or CAPTURE_DIR
        cap = cv2.VideoCapture(self.camera_index)
        os.makedirs(save_dir, exist_ok=True)
        filepath = os.path.join(save_dir, f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg")

        if not cap.isOpened():
            img = np.zeros((300, 300, 3), dtype=np.uint8)
            img[:] = (35, 180, 40)
            cv2.imwrite(filepath, img)
            return img, filepath, False

        for _ in range(15):
            cap.grab()
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            img = np.zeros((300, 300, 3), dtype=np.uint8)
            img[:] = (35, 180, 40)
            cv2.imwrite(filepath, img)
            return img, filepath, False

        cv2.imwrite(filepath, frame)
        return frame, filepath, True


# =============================================================================
# 9. SQLITE LOGGING
# =============================================================================

def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            farm_input_json TEXT NOT NULL,
            structured_intelligence_json TEXT NOT NULL,
            qwen_output_json TEXT NOT NULL,
            irrigation_decision_json TEXT NOT NULL,
            qwen_fallback_used INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS weather_forecast_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            forecast_temp_c REAL,
            forecast_humidity_pct REAL,
            forecast_rain_prob REAL,
            actual_temp_c REAL,
            actual_humidity_pct REAL,
            actual_rain INTEGER,
            resolved INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sensor_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            soil_moisture_pct REAL,
            air_temp_c REAL,
            air_humidity_pct REAL
        );
    """)
    conn.commit()
    return conn


def log_run(farm_input, structured_intel, qwen_output, irrigation, fallback_used: bool):
    conn = _init_db()
    conn.execute(
        "INSERT INTO runs (timestamp, farm_input_json, structured_intelligence_json, "
        "qwen_output_json, irrigation_decision_json, qwen_fallback_used) VALUES (?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), json.dumps(farm_input),
         json.dumps(structured_intel), json.dumps(qwen_output), json.dumps(irrigation),
         int(fallback_used)),
    )
    conn.commit()
    conn.close()


def log_sensor_reading(lat: float, lon: float, soil_moisture_pct: float,
                        air_temp_c: float, air_humidity_pct: float) -> None:
    """[FIX 15] Stores every reading so the NEXT run can look back at the
    previous one for the rate-of-change check."""
    conn = _init_db()
    conn.execute(
        "INSERT INTO sensor_readings (timestamp, latitude, longitude, soil_moisture_pct, "
        "air_temp_c, air_humidity_pct) VALUES (?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), lat, lon, soil_moisture_pct, air_temp_c, air_humidity_pct),
    )
    conn.commit()
    conn.close()


def get_last_sensor_reading(lat: float, lon: float) -> Tuple[Optional[dict], Optional[float]]:
    """[FIX 15] Returns (previous_reading_dict, elapsed_seconds) for the
    most recent reading logged for this site, or (None, None) if there
    isn't one yet (e.g. first-ever run)."""
    conn = _init_db()
    cur = conn.execute(
        "SELECT timestamp, soil_moisture_pct, air_temp_c, air_humidity_pct FROM sensor_readings "
        "WHERE latitude=? AND longitude=? ORDER BY id DESC LIMIT 1",
        (lat, lon),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None, None
    prev_time = datetime.fromisoformat(row[0])
    elapsed = (datetime.now(timezone.utc) - prev_time).total_seconds()
    return {"soil_moisture_pct": row[1], "air_temp_c": row[2], "air_humidity_pct": row[3]}, elapsed


# =============================================================================
# 10. QWEN CLIENT -- two separate prompts/schemas  [FIX 11, FIX 12]
# =============================================================================
# Mode 1: initial farm planning (site + budget + climate -> crop/system plan)
# Mode 2: recurring crop health monitoring (existing plant + vision + weather
#         + irrigation history -> disease/water-stress/harvest status)
# These are deliberately never merged into one JSON schema.

FARM_DECISION_SYSTEM_PROMPT = """You are an agricultural planning reasoning engine. You receive structured
agricultural intelligence (site suitability, corrected weather, climate normals, soil estimate,
retrieved candidate crops, salinity data-availability notes, time/season, budget) and must produce
a farm plan.

Rules:
- Never claim guaranteed profit, guaranteed yield, or a definite disease diagnosis.
- Base recommended_crops and crops_to_avoid on the retrieved candidate crops you were given, not
  on outside assumptions. The retrieved list is a candidate pool for you to evaluate, not a ranking
  you must follow -- use the fit scores as one input among several (budget, season, salinity risk).
- If soil_salinity or water_salinity is reported unavailable, explicitly reduce confidence for any
  salinity-sensitive crop recommendation and say so in uncertainties.
- Use the season/time context to judge whether now is an appropriate planting window.
- Explicitly list uncertainties where the data is incomplete (e.g. missing soil nutrients, no NDVI,
  no salinity reading).
- Output ONLY raw JSON, no markdown fences, no surrounding text, matching exactly this schema:

{
  "recommended_crops": ["string"],
  "crops_to_avoid": ["string"],
  "recommended_farming_system": "string (open field | greenhouse | hydroponics | shade structure | container farming)",
  "reasoning": "string",
  "growing_strategy": ["string"],
  "irrigation_strategy": {"approach": "string", "frequency_guidance": "string"},
  "infrastructure": ["string"],
  "major_risks": ["string"],
  "resource_requirements": {"estimated_setup_notes": "string"},
  "economic_viability": {"assessment": "string", "cost_estimate_notes": "string"},
  "confidence": 0.0,
  "uncertainties": ["string"]
}"""


CROP_HEALTH_SYSTEM_PROMPT = """You are a recurring crop-health monitoring reasoning engine. You receive
an EXISTING crop's current state: rule-based colour/vision analysis, soil moisture, weather, recent
irrigation decisions, and time/season context. You must assess the plant's current condition.

Rules:
- Never claim a certain/lab-confirmed diagnosis -- this is a preliminary visual/sensor-based
  assessment, always phrase disease findings as suspected, not confirmed.
- Ground every judgment in the vision/sensor data you were given -- do not invent symptoms that
  aren't reflected in the colour analysis or stress-rule outputs.
- harvest_ready should reference the crop's known harvest_indicators if they were provided.
- Output ONLY raw JSON, no markdown fences, no surrounding text, matching exactly this schema:

{
  "disease_detected": false,
  "water_stress_detected": false,
  "harvest_ready": false,
  "status_summary": "string (e.g. HEALTHY | MONITOR | AT_RISK)",
  "primary_diagnosis": "string",
  "recommended_action": "string",
  "confidence": 0.0,
  "uncertainties": ["string"]
}"""


def _local_fallback_plan(structured_intel: dict) -> dict:
    """Used only if Ollama is unreachable or returns unparseable output.
    Explicitly disclosed as a fallback -- never presented as a real model
    response. [FIX 13]"""
    top_crops = [c["crop"] for c in structured_intel["retrieved_crop_knowledge"][:3]]
    return {
        "recommended_crops": top_crops,
        "crops_to_avoid": [],
        "recommended_farming_system": "open field",
        "reasoning": "FALLBACK PLAN -- the local Qwen model was unreachable or returned invalid "
                     "JSON. This is a coarse rule-based substitute using only the top-ranked "
                     "candidate crops from the retrieved knowledge base, not full reasoning.",
        "growing_strategy": ["consult the knowledge base entries directly for planting season and spacing"],
        "irrigation_strategy": {"approach": "moderate, monitor soil moisture", "frequency_guidance": "unknown -- fallback mode"},
        "infrastructure": [],
        "major_risks": ["this plan was generated by the fallback path, not by the LLM -- verify manually"],
        "resource_requirements": {"estimated_setup_notes": "not assessed in fallback mode"},
        "economic_viability": {"assessment": "not assessed in fallback mode", "cost_estimate_notes": ""},
        "confidence": 0.2,
        "uncertainties": ["entire plan generated by fallback logic, not the LLM reasoning layer"],
        "ai_status": "fallback",
    }


def _local_fallback_health(structured_intel: dict) -> dict:
    """[FIX 13] Fallback for crop-health mode -- purely rule-based off the
    already-computed vision/stress numbers, explicitly flagged as such."""
    stress = structured_intel["plant"]
    disease = stress.get("disease_suspicion", 0) > 0.5
    water_stress = stress.get("water_stress_suspicion", 0) > 0.5
    status = "AT_RISK" if (disease or water_stress) else "HEALTHY"
    return {
        "disease_detected": disease,
        "water_stress_detected": water_stress,
        "harvest_ready": False,
        "status_summary": status,
        "primary_diagnosis": "FALLBACK DIAGNOSIS -- Qwen unreachable/invalid output. Based only on "
                              "rule-based colour-analysis thresholds, not full reasoning.",
        "recommended_action": "Verify manually; re-run once the Qwen service is available for a full assessment.",
        "confidence": 0.2,
        "uncertainties": ["entire diagnosis generated by fallback logic, not the LLM reasoning layer"],
        "ai_status": "fallback",
    }


def _call_qwen(system_prompt: str, payload: dict, fallback_fn) -> Tuple[dict, bool]:
    """Shared Qwen-calling machinery for both prompt modes."""
    try:
        from openai import OpenAI
    except ImportError:
        return fallback_fn(payload), True

    client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
    user_payload = json.dumps(payload, indent=2)

    for model_name in (QWEN_MODEL_PRIMARY, QWEN_MODEL_FALLBACK):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.2,
            )
            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(content)
            result["_model_used"] = model_name
            result["ai_status"] = "model"
            return result, False
        except Exception as e:
            print(f"  [qwen] {model_name} failed: {e}")
            continue

    result = fallback_fn(payload)
    return result, True


def get_farm_plan(structured_intel: dict) -> tuple:
    """Mode 1: initial planning. Returns (plan_dict, fallback_used: bool)."""
    return _call_qwen(FARM_DECISION_SYSTEM_PROMPT, structured_intel, _local_fallback_plan)


def get_crop_health_diagnosis(structured_intel: dict) -> tuple:
    """Mode 2: recurring health monitoring. Returns (diagnosis_dict, fallback_used: bool)."""
    return _call_qwen(CROP_HEALTH_SYSTEM_PROMPT, structured_intel, _local_fallback_health)


# =============================================================================
# 11. MAIN PIPELINE
# =============================================================================

def run_pipeline(latitude: float, longitude: float, budget: float,
                  soil_moisture_pct: float, air_temp_c: float, air_humidity_pct: float,
                  ldr_pct: float,
                  crop_moisture_threshold_pct: float = 40.0,
                  camera_index: int = 0,
                  mode: str = "plan",
                  region: Optional[str] = None) -> dict:
    """ldr_pct: raw LDR sensor percentage reading -- on this hardware a
    LOWER percentage means MORE brightness (inverted inside predict_state).

    `mode`:
      "plan"   -- Qwen Mode 1, initial farm planning (use for a new/empty plot)
      "health" -- Qwen Mode 2, recurring crop-health monitoring (use once a
                  crop is already planted and established)
      "both"   -- runs both Qwen prompts

    Pump/reservoir hardware is intentionally NOT part of this build; the
    irrigation section only produces a recommendation/log entry."""

    _require_torch()
    _require_vision()

    print("1/7  Fetching real external data (weather + soil + NASA POWER climatology)...")
    online = fetch_online_location_data(latitude, longitude)
    time_context = get_time_context(latitude)

    print("2/7  Checking sensor validity + rate-of-change vs last reading...")
    previous_reading, elapsed_seconds = get_last_sensor_reading(latitude, longitude)
    current_reading = {"soil_moisture_pct": soil_moisture_pct, "air_temp_c": air_temp_c,
                        "air_humidity_pct": air_humidity_pct}
    rate_check = check_sensor_rate_of_change(current_reading, previous_reading, elapsed_seconds)
    if rate_check["suspicious"]:
        print(f"  [sensor] rate-of-change check flagged a suspicious jump: {rate_check['flags']}")
    log_sensor_reading(latitude, longitude, soil_moisture_pct, air_temp_c, air_humidity_pct)

    print("3/7  Running Agricultural State NN (proxy-trained -- see PROXY_DATA_DISCLOSURE)...")
    state = predict_state(
        soil_moisture_pct=soil_moisture_pct, air_temp_c=air_temp_c, air_humidity_pct=air_humidity_pct,
        ldr_pct=ldr_pct,
        soil_ph=online["soil"]["ph"] or 6.5, organic_carbon_pct=online["soil"]["organic_carbon"],
        rainfall_mm=online["weather"]["rainfall_mm"] or 0.0,
    )

    print("4/7  Running Weather Correction NN + logging forecast for future learning...")
    weather_corrected = correct_forecast(
        forecast_temp_c=online["weather"]["temperature_c"] or air_temp_c,
        forecast_humidity_pct=online["weather"]["humidity_pct"] or air_humidity_pct,
        forecast_rain_prob=online["weather"]["rain_probability_next_days"] or 0.1,
    )
    forecast_log_id = log_forecast_for_later_verification(latitude, longitude, online["weather"])

    print("5/7  Plant colour analysis (camera runs every time -- never bypassed on moisture)...")
    camera = Camera(camera_index)
    analyzer = PlantPixelAnalyzer()
    frame, image_path, is_real = camera.capture_frame()
    vision = analyzer.analyze(frame)
    vision["capture_status"] = "real_capture" if is_real else "SIMULATED_camera_unavailable"
    stress = plant_stress_rules(vision, soil_moisture_pct, air_temp_c)

    print("6/7  Retrieving candidate crops (RAG)...")
    retrieved_crops = retrieve_relevant_crops(
        avg_temp_c=weather_corrected.get("corrected_temp_c") or air_temp_c,
        water_availability=("low" if state["water_stress_index"] > 50 else "moderate"),
        soil_ph=online["soil"]["ph"],
        region=region,
        soil_salinity=online.get("soil_salinity"),
    )

    structured_intel = {
        "location": {"latitude": latitude, "longitude": longitude, "region": region},
        "time_context": time_context,
        "budget": budget,
        "site_state": state,
        "weather": weather_corrected,
        "climate_normal": online["climate_normal"],
        "soil": online["soil"],
        "soil_salinity": online["soil_salinity"],
        "water_salinity": online["water_salinity"],
        "plant": {**vision, **stress},
        "retrieved_crop_knowledge": retrieved_crops,
        "data_quality_notes": online["unavailable_notes"],
        "sensor_rate_of_change_check": rate_check,
    }

    result = {
        "structured_intelligence": structured_intel,
        "image_path": image_path,
    }

    any_fallback = False

    if mode in ("plan", "both"):
        print("7/7  Querying Qwen -- Mode 1: farm plan...")
        farm_plan, fallback_used = get_farm_plan(structured_intel)
        any_fallback = any_fallback or fallback_used
        result["farm_plan"] = farm_plan

    if mode in ("health", "both"):
        print("7/7  Querying Qwen -- Mode 2: crop health diagnosis...")
        health, fallback_used = get_crop_health_diagnosis(structured_intel)
        any_fallback = any_fallback or fallback_used
        result["crop_health"] = health

    print("     Running deterministic irrigation controller (final safety authority)...")
    qwen_irrigation_strategy = result.get("farm_plan", {}).get("irrigation_strategy") if "farm_plan" in result else None
    irrigation = irrigation_decision(
        soil_moisture_pct=soil_moisture_pct,
        crop_moisture_threshold_pct=crop_moisture_threshold_pct,
        rain_probability=weather_corrected.get("rain_probability"),
        qwen_irrigation_strategy=qwen_irrigation_strategy,
        rate_of_change_check=rate_check,
    )
    result["irrigation_decision"] = irrigation

    # [FIX 13] Loud, unmissable fallback flag for the dashboard -- not just a
    # quiet DB column.
    dashboard_message = irrigation["message"]
    if any_fallback:
        dashboard_message = "⚠️ AI reasoning unavailable — fallback mode. " + dashboard_message
    result["message"] = dashboard_message
    result["qwen_fallback_used"] = any_fallback
    result["ai_status"] = "fallback" if any_fallback else "model"
    result["_forecast_log_id"] = forecast_log_id  # pass to record_actual_weather() on the next run

    farm_input = {"latitude": latitude, "longitude": longitude, "budget": budget,
                  "soil_moisture_pct": soil_moisture_pct, "air_temp_c": air_temp_c,
                  "air_humidity_pct": air_humidity_pct, "ldr_pct": ldr_pct, "mode": mode}
    log_run(farm_input, structured_intel,
            {"farm_plan": result.get("farm_plan"), "crop_health": result.get("crop_health")},
            irrigation, any_fallback)

    return result


# =============================================================================
# 12. CLI ENTRY POINT -- type your inputs
# =============================================================================

if __name__ == "__main__":
    print("=== AI Agricultural Autonomy Platform -- type your inputs ===\n")
    lat = float(input("Latitude: ") or 24.86)
    lon = float(input("Longitude: ") or 46.72)
    budget = float(input("Budget (USD): ") or 750)
    soil_moisture = float(input("Soil moisture (%): ") or 38.0)
    temp = float(input("Air temperature (C): ") or 33.2)
    humidity = float(input("Air humidity (%): ") or 45.0)
    ldr_pct = float(input("LDR sensor reading (%, lower = brighter): ") or 25.0)
    mode = (input("Mode [plan/health/both] (default plan): ") or "plan").strip().lower()

    result = run_pipeline(lat, lon, budget, soil_moisture, temp, humidity, ldr_pct, mode=mode)
    print("\n" + "=" * 60)
    print(json.dumps(result, indent=2, default=str))
    print("\nMessage for your dashboard:", result["message"])

    # Optionally, once you know what the weather actually did (e.g. on the
    # NEXT run for the same site), call:
    #   record_actual_weather(result["_forecast_log_id"], actual_temp_c=...,
    #                          actual_humidity_pct=..., actual_rain=...)
    # and periodically call retrain_weather_nn_from_history() to let the
    # Weather Correction NN actually learn local bias over time. [FIX 4]
