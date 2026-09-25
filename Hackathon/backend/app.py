"""
app.py -- the Mazraa web server (Flask).

What it does
------------
  POST /recommend   the frontend sends {lat, lon, area, budget} and gets back a
                    ranked list of crop + farming-system options with the numbers
  POST /sensor      the ESP32 sends its latest soil moisture / temperature here
  GET  /sensor      check the latest stored reading
  GET  /health      a simple "is the server alive?" check used by the frontend

How to run it (from inside the backend/ folder):

    pip install -r requirements.txt
    python app.py

Then open ../frontend/index.html in your browser (see the README).
"""

import os
import sys
import time
import traceback

from flask import Flask, jsonify, request

# Windows terminals often use an old text encoding (cp1252), and printing Arabic,
# Hindi, Urdu or Swahili text to them would crash the server with a
# UnicodeEncodeError. Force UTF-8 output, replacing anything the console cannot
# draw, so the log lines can never take the server down.
try:  # pragma: no cover - depends on the machine running the server
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Importing Flask gives us CORS (Cross-Origin Resource Sharing). If it is not
# installed we add the headers by hand further down, so the app still works.
try:
    from flask_cors import CORS
except ImportError:  # pragma: no cover
    CORS = None

# Make sure the folder that contains this file is on Python's import path, so
# that `python app.py` works whether you start it from the repo root or from
# inside backend/.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data          # noqa: E402  (climate + soil lookup, with caching)
import engine        # noqa: E402  (crop/system tables + the maths)
import explain       # noqa: E402  (writes the explanation sentence)
import sensor_data   # noqa: E402  (stores the latest ESP32 reading)

# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------
# How many of the best options get an AI-written explanation. Every extra option
# costs one more call to the local language model (roughly a second each), so
# keep this small. All other options still get the template explanation.
TOP_N_EXPLANATIONS = 3

# Defaults used when the frontend leaves something out.
DEFAULT_AREA_HA = 1.0

app = Flask(__name__)

# Allow the browser to call this server from a different address (the frontend
# is usually opened as a file:// page or on port 8000, not on port 5000).
if CORS is not None:
    CORS(app)
else:
    print("[app.py] flask_cors is not installed; adding CORS headers by hand.")

    @app.after_request
    def _add_cors_headers(response):  # pragma: no cover
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response


# ---------------------------------------------------------------------------
# HEALTH CHECK
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    """Small endpoint the frontend can ping to show 'backend connected'."""
    return jsonify({
        "status": "ok",
        "service": "Mazraa backend",
        "crops_available": len(engine.CROPS),
        "systems_available": len(engine.SYSTEMS),
        "sensor_readings_received": sensor_data.count(),
        "languages": list(engine.SUPPORTED_LANGUAGES),
    })


# ---------------------------------------------------------------------------
# MAIN RECOMMENDATION ENDPOINT
# ---------------------------------------------------------------------------
@app.route("/recommend", methods=["POST"])
def recommend():
    """
    Body (JSON):
        {"lat": 24.71, "lon": 46.68, "area": 2.5, "budget": 150000,
         "lang": "ar"}

    "area" is in hectares, "budget" in US dollars (optional; leave it out or use
    0 to mean "no budget limit"). "lang" is optional; it decides the language of
    the crop/system names and of the explanation sentences (default "en").

    Answer (JSON):
        {
          "query": {...what you sent...},
          "site":  {...temperature, rainfall, soil pH, and where they came from...},
          "language": "ar",
          "count": 20,
          "options": [ {...best first...}, ... ]
        }
    """
    started = time.time()
    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return _error("Please send a JSON object like "
                      '{"lat": 24.7, "lon": 46.6, "area": 2, "budget": 50000}')

    # --- 1) Validate the input -------------------------------------------
    try:
        lat = float(body.get("lat"))
        lon = float(body.get("lon"))
    except (TypeError, ValueError):
        return _error("lat and lon must be numbers (decimal degrees). "
                      "Click on the map to choose a location.")

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return _error("lat must be between -90 and 90 and lon between -180 and 180.")

    try:
        area = float(body.get("area") or DEFAULT_AREA_HA)
    except (TypeError, ValueError):
        return _error("area must be a number in hectares.")
    if area <= 0:
        return _error("area must be greater than 0 hectares.")

    budget = body.get("budget")
    try:
        budget = float(budget) if budget not in (None, "") else 0.0
    except (TypeError, ValueError):
        return _error("budget must be a number in US dollars.")
    budget_limited = budget > 0  # a budget of 0 means "no limit"

    # The language the farmer reads. Anything unsupported becomes English, so a
    # strange value can never break the request.
    language = engine.normalize_language(body.get("lang"))

    # --- 2) Get the climate and soil for this point ----------------------
    # get_site_info() never raises: if the APIs are unreachable it returns
    # placeholder values and says so in "source"/"notes".
    site = data.get_site_info(lat, lon)

    # --- 3) Score every crop in every farming system ---------------------
    options = []
    for crop_key, crop in engine.CROPS.items():
        for system_key, system in engine.SYSTEMS.items():
            conditions = dict(site)
            conditions["system"] = system_key

            suitability = engine.score_suitability(crop_key, conditions)
            economics = engine.calculate_economics(crop_key, system_key, area, site)

            capex = economics["capex_usd"]
            within_budget = (not budget_limited) or (capex <= budget)
            profitable = economics["payback_years"] is not None

            option = {
                "crop": crop_key,
                # Translated name, e.g. "الطماطم" or "Nyanya".
                "crop_name": engine.crop_name(crop_key, language),
                # The English name is kept too, so the data stays usable for
                # exports, tables and debugging.
                "crop_name_en": crop["name"],
                "system": system_key,
                "system_name": engine.system_name(system_key, language),
                "system_name_en": system["label"],
                "area_ha": area,
                "suitability": suitability,
                # A simple 0-100 version, easier to show in the UI.
                "score_percent": round(suitability * 100.0, 1),
                **economics,
                "within_budget": within_budget,
                "budget_gap_usd": round(capex - budget, 2) if budget_limited else 0.0,
                "profitable": profitable,
                # "viable" = you can afford to build it AND it makes money.
                "viable": bool(within_budget and profitable),
            }
            options.append(option)

    # --- 4) Rank them -----------------------------------------------------
    # Order:
    #   1. options you can actually afford and that make money come first
    #   2. then the best agronomic match (suitability)
    #   3. then the quickest payback, then the least start-up money needed
    # `payback_years` can be None (never pays back), which cannot be sorted, so
    # we sort those to the end with a big number.
    options.sort(
        key=lambda o: (
            0 if o["viable"] else 1,
            -(o["suitability"] or 0.0),
            o["payback_years"] if o["payback_years"] is not None else 999.0,
            o["capex_usd"],
        )
    )
    for rank, option in enumerate(options, start=1):
        option["rank"] = rank

    # --- 5) Write an explanation for each option, in the chosen language --
    # The best few get a sentence from the local AI model; the rest get the
    # fast template so the request does not take minutes. Both are written in
    # the language the farmer asked for.
    for option in options:
        if option["rank"] <= TOP_N_EXPLANATIONS:
            option["explanation"] = explain.describe(option, language)
            option["explanation_source"] = "ai" if explain.USE_LLM else "template"
        else:
            option["explanation"] = explain.fallback_sentence(option, language)
            option["explanation_source"] = "template"

    elapsed = round(time.time() - started, 2)
    print("[app.py] /recommend %.4f, %.4f | %.2f ha | budget %s | lang %s "
          "-> %d options in %ss"
          % (lat, lon, area, budget if budget_limited else "none",
             language, len(options), elapsed))

    # --- 6) Send it back --------------------------------------------------
    return jsonify({
        "query": {
            "lat": lat,
            "lon": lon,
            "area_ha": area,
            "budget_usd": budget,
            "budget_limited": budget_limited,
            "lang": language,
        },
        "site": site,
        "language": language,
        "count": len(options),
        "elapsed_seconds": elapsed,
        "options": options,
    })


# ---------------------------------------------------------------------------
# SENSOR ENDPOINTS (ESP32 over local WiFi)
# ---------------------------------------------------------------------------
@app.route("/sensor", methods=["POST"])
def post_sensor():
    """
    Receive a reading from the ESP32 and remember it.

    Body (JSON), extra keys are fine:
        {"device": "esp32-01", "soil_moisture": 42.5, "temperature": 27.8}

    On the ESP32 side (Arduino/C++) a request looks like:

        HTTPClient http;
        http.begin("http://192.168.1.50:5000/sensor");
        http.addHeader("Content-Type", "application/json");
        http.POST("{\\"soil_moisture\\":42.5,\\"temperature\\":27.8}");
        http.end();

    Use your computer's local IP address (not "localhost") and make sure the
    Flask server is started with host="0.0.0.0" -- see the bottom of this file.
    """
    body = request.get_json(silent=True)

    # If the ESP32 sends form data or plain text instead of JSON, still try to
    # be helpful rather than throwing an error at it.
    if body is None:
        body = request.form.to_dict() or None
    if body is None and request.data:
        try:
            import json as _json
            body = _json.loads(request.data.decode("utf-8", "ignore"))
        except ValueError:
            body = None

    if not isinstance(body, dict) or not body:
        return _error("No reading received. Send JSON such as "
                      '{"soil_moisture": 42.5, "temperature": 27.8}', status=400)

    try:
        record = sensor_data.save(body)
    except ValueError as exc:
        return _error(str(exc), status=400)

    print("[app.py] /sensor reading #%s: %s" % (record.get("id"), record))
    return jsonify({"status": "stored", "reading": record}), 201


@app.route("/sensor", methods=["GET"])
def get_sensor():
    """
    Return the latest stored reading.

    If nothing has arrived yet the answer is {"latest": null}, which is easier to
    handle in the browser than an error page.
    """
    latest = sensor_data.get_latest()
    return jsonify({
        "latest": latest,
        "count": sensor_data.count(),
        "has_reading": latest is not None,
    })


# ---------------------------------------------------------------------------
# ERROR HANDLING
# ---------------------------------------------------------------------------
def _error(message, status=400):
    """Every error answer looks the same, so the frontend can show it."""
    return jsonify({"error": message}), status


@app.errorhandler(404)
def handle_not_found(_err):
    return _error("Unknown endpoint. Try /recommend, /sensor or /health.", status=404)


@app.errorhandler(500)
def handle_server_error(err):
    traceback.print_exc()
    return _error("Something went wrong on the server: %s" % err, status=500)


# ---------------------------------------------------------------------------
# START THE SERVER
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # host="0.0.0.0" means "listen on every network interface", which is what
    # lets the ESP32 reach this server from the same WiFi network.
    # port 5000 is the address the frontend is hard-coded to call.
    print("Mazraa backend starting on http://localhost:5000")
    print("   Try:  curl http://localhost:5000/health")
    app.run(host="0.0.0.0", port=5000, debug=True)
