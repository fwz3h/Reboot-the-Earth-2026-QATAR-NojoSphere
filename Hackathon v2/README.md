# AgriSense AI backend (V2)

A small local-LAN backend for ESP32 sensor readings and the AgriSense AI dashboard. It uses only the Python 3 standard library, SQLite persistence, REST endpoints, and a `/ws` WebSocket endpoint that serves both dashboard clients and ESP32 devices pushing readings. The included AI insight engine is explicitly rule-based, not a machine-learning model.

## Run

Requires Python 3.10 or newer; no package installation is needed.

From the project root, run the backend:

```powershell
py -3 -m backend.server
```

The server listens on `0.0.0.0:5000` by default, so devices on the local network can reach it. The SQLite database is created as `agrisense.sqlite3` in the current directory. Configure with environment variables:

Visit `http://localhost:5000/` while the backend is running (recommended for geolocation permissions), or open `frontend/index.html` directly (it connects to `http://localhost:5000`, but browser geolocation may be blocked on `file://`). The backend must remain running for sensor data and simulation controls to work. When opening the file directly, the browser has a `null` origin; this is permitted by the default CORS settings for local use. Set `ALLOWED_ORIGINS` explicitly for your deployment.

## Mandatory site/facility and crop-system suitability screening

The dashboard's **Site suitability** section implements the primary screening challenge. Enter or import summarized GIS layer outputs (solar exposure, rainfall, soil pH/salinity, wind/dust risk, water availability, temperature, slope, market distance), land area, budget, local farm-gate crop price, and per-hectare capex/opex for open-field, greenhouse, and hydroponic systems. Solar, rainfall, pH, salinity, water, and market distance are required; at least one of wind speed or wind/dust risk is also required. The UI ranks crop/system options by transparent generalized layer-fit scores; it shows budget fit/funding gap, yield/revenue scenarios, operating costs, estimated net operating return and payback, data coverage, and contributing factors. CSV/JSON summaries can be imported (multiple files supported); missing fields stay editable in the form.

### Opt-in offline/online site GPS

In Site suitability, **Use this device’s location** explicitly requests a one-time location fix using browser geolocation; there is no continuous/background tracking. The same site location is restored on a different device only if the user provides the same `site_id`; the dashboard’s default browser-generated ID is deliberately device-local. Location permission is controlled by the browser/operating system and usually requires HTTPS or localhost (the included backend URL is localhost). This works offline after the dashboard is loaded, but opening `frontend/index.html` as `file://` may cause some browsers to block geolocation; use the backend-hosted `http://localhost:5000/` page or HTTPS if location access is unavailable. GPS hardware can also use satellite signals offline; devices without GPS may provide only a less accurate cached/network location. The latest site fix is stored in this browser for offline viewing and queued locally if no connection is available, then synchronized to the backend when connectivity returns. A site ID in local browser storage lets this browser restore its synchronized site fix from the backend (it is not shared across browsers or devices). **Clear** removes the local copy and queues deletion of its backend record when offline. Coordinates can be opened in OpenStreetMap when online. `POST /api/site-location`, `GET /api/site-location/{siteId}`, and `DELETE /api/site-location/{siteId}` persist one latest location per browser site ID in SQLite. Local storage is browser/device-local and is not encrypted; avoid shared devices for sensitive coordinates. GPS coordinates are passed through with suitability submissions for site context only; they do not influence crop scores, trigger reverse geocoding, or fetch GIS layers.

- `GET /api/suitability/schema` — valid layer keys/ranges, required fields, example crops/systems, and limitations.
- `POST /api/suitability/recommendations` — screen one site using `{ "site": { "name": "North Field", "area_ha": 2.5 }, "layers": { "solar_kwh_m2_day": 6.2, "soil_ph": 6.4, "soil_salinity_ds_m": 1.2, "wind_kmh": 18, "water_availability": 75, "market_distance_km": 35 }, "economics": { "currency": "USD", "budget": 30000, "price_per_ton": 320, "systems": { "open_field": { "capex_per_ha": 1800, "opex_per_ha": 2200 }, "greenhouse": { "capex_per_ha": 18000, "opex_per_ha": 6800 }, "hydroponic": { "capex_per_ha": 24000, "opex_per_ha": 8500 } } } }`.

Scores use generalized crop/system suitability intervals, and confidence reflects GIS layer coverage rather than statistical certainty. Yields are illustrative reference assumptions. Conservative/base/optimistic scenarios scale the reference yield by 0.8/1.0/1.2; startup capex and yearly opex come from user inputs. Revenue is yield × the entered price; payback is capex divided by positive estimated annual operating returns. No external GIS files are fetched automatically, and no market, crop, yield, or payback result should be treated as a validated forecast. Verify GIS summary values, soil tests, local extension recommendations, water rights, market access, and vendor/labor costs before making investment decisions.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | Listen interface; use `127.0.0.1` to disable LAN access |
| `PORT` | `5000` | HTTP and WebSocket port |
| `DATABASE_URL` | `agrisense.sqlite3` | SQLite file path; `sqlite:///path/to/file.db` is also accepted |
| `DEVICE_TIMEOUT_MS` | `30000` | Preferred timeout, milliseconds without a reading before offline; `DEVICE_TIMEOUT_SECONDS` is retained as a legacy fallback |
| `ALLOWED_ORIGINS` | localhost ports 3000 and 5173, plus `null` for direct-file dashboard access | Comma-separated frontend origins; `*` allows all origins without credential support. The server's own origin (whatever `Host` a request carries) is always accepted, so the dashboard served from this process can open its own `/ws` and API on any port |
| `MAX_FUTURE_TIMESTAMP_SECONDS` | `300` | Maximum tolerated sensor timestamp clock skew into the future; old queued readings remain accepted |
| `SENSOR_SOIL_MOISTURE_MIN` / `_MAX` | `0` / `100` | Accepted soil-moisture percentage range |
| `SENSOR_TEMPERATURE_MIN` / `_MAX` | `-80` / `100` | Accepted environmental temperature range in °C |
| `SENSOR_HUMIDITY_MIN` / `_MAX` | `0` / `100` | Accepted relative-humidity percentage range |
| `SENSOR_SOIL_PH_MIN` / `_MAX` | `0` / `14` | Accepted pH range |
| `SENSOR_WATER_LEVEL_MIN` / `_MAX` | `0` / `100` | Accepted water-level percentage range |
| `SENSOR_BATTERY_MIN` / `_MAX` | `0` / `100` | Accepted battery percentage range |
| `SENSOR_LIGHT_MIN` / `_MAX` | `0` / `2000000` | Accepted light sensor numeric range |
| `SENSOR_PRESSURE_MIN` / `_MAX` | `100` / `2000` | Accepted pressure sensor range |
| `LOG_LEVEL` | `INFO` | Standard logging level |
| `AGRI_AI_DB_PATH` | `agri_ai.db` in the project root | SQLite file for the AI core's own run, forecast and sensor history (kept separate from `agrisense.sqlite3`) |
| `AGRI_AI_CAPTURE_DIR` | `crop_snapshots` in the project root | Where the AI core writes captured crop frames |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Local Ollama endpoint used by the AI reasoning layer |
| `ALERT_SOIL_MOISTURE_MIN` | `30` | Low-soil-moisture alert threshold |
| `ALERT_TEMPERATURE_MAX` | `35` | High-temperature alert threshold |
| `ALERT_TEMPERATURE_MIN` | `10` | Low-temperature alert threshold |
| `ALERT_WATER_LEVEL_MIN` | `20` | Low-water-level alert threshold |

For an ESP32, set the server's LAN IP in firmware, not `localhost`: `http://192.168.1.105:5000/api/readings`. The dashboard connects to `ws://192.168.1.105:5000/ws`. Ensure the host firewall allows this single port on the trusted LAN.

## Sensor data

Required fields are a device ID, soil moisture, and temperature. UTC ISO-8601 timestamps are accepted; missing timestamps are assigned by the backend. The backend validates all values, persists valid data, updates last-seen status, evaluates alerts and insights, then broadcasts the reading to dashboard clients.

```json
{
  "device": "ESP32-01",
  "soil_moisture": 50.0,
  "temperature": 28.0,
  "timestamp": "2026-09-25T17:45:45Z",
  "humidity": 62.5,
  "soil_ph": 6.4,
  "water_level": 74,
  "light": 820,
  "pressure": 1012,
  "battery": 94
}
```

`POST /api/readings` accepts this JSON. Configurable range checks reject impossible values, non-finite numbers, invalid JSON constants, and timestamps too far in the future; absent optional sensors remain absent. Unknown optional sensor fields are preserved when safely named and finite. Repeated device/timestamp pairs remain idempotent, but valid retransmission refreshes connection last-seen without duplicating stored readings or broadcasts. Simulated devices must use IDs beginning with `sim-`; ESP32 readings using that reserved prefix are rejected.

## REST API

All API errors return JSON with `error.code`, `error.message`, and `error.timestamp` (plus a top-level `message` for existing frontend compatibility); invalid requests use HTTP 400, missing devices/routes use 404, and database/service failures use 503.

- `GET /api/health` — health, uptime, database/WebSocket state, online device count, WebSocket client count, simulation state, timeout configuration.
- `GET /api/devices` — `{ devices, count }`, including current status, first/last seen, reading count, IP when known, available sensors, uptime, and latest reading.
- `GET /api/devices/{deviceId}` — one device or 404.
- `GET /api/readings/latest?device=ESP32-01` — latest reading (omit `device` for latest overall).
- `GET /api/readings?device=ESP32-01&range=24h&limit=100&offset=0` — paginated history; ranges: `1h`, `6h`, `24h`, `7d`, `30d`.
- `GET /api/readings/{deviceId}?limit=100&offset=0` — device history.
- `GET /api/stats?device=ESP32-01&range=24h` — current, average, min, max, percentage change, trend, count, latest reading and uptime; aggregation executes in SQLite and ignores missing optional metrics.
- `GET /api/alerts?active=true&limit=100` — active alerts; `active=false` includes resolved alerts too.
- `GET /api/insights?device=ESP32-01` — rule-based sensor insight results, using each device's own latest samples.
- `GET /api/suitability/schema` and `POST /api/suitability/recommendations` — mandatory site/crop production-system screening contract (see above); optional `site.location` supports validated latitude, longitude and accuracy metadata.
- `POST /api/site-location` — validate and upsert one location record per `site_id`; `GET /api/site-location/{siteId}` — retrieve that record; `DELETE /api/site-location/{siteId}` — remove it.
- `POST /api/readings` — validate/store/broadcast real device reading.
- `POST /api/simulation/start` — start readings through the exact same ingestion pipeline; optional body `{ "device": "sim-01", "interval_seconds": 5 }`.
- `POST /api/simulation/stop` — stop simulation.
- `GET /api/ai/status` — whether the AI core can run on this machine, which packages are missing, model warm-up state, job capacity and the proxy-training disclosure.
- `POST /api/ai/analyze` — queue one AI run and answer `202` with a `job_id`; invalid input is `400`, a missing dependency is `503 AI_UNAVAILABLE`, and a full queue is `429 AI_BUSY`.
- `GET /api/ai/jobs/{jobId}` — run status, timing and result once it settles; `404` for unknown or pruned ids.
- `POST /api/ai/warmup` — train both networks in the background so the first run is faster.

Simulation progresses smoothly with small noise and bounded agricultural sensor values. It is off by default and can be started by the existing dashboard button or API.

## WebSocket contract

Connect to `/ws`. Each event is one JSON WebSocket text message:

```json
{"type":"connection_status","status":"connected","timestamp":"2026-09-25T17:45:45Z"}
{"type":"device_status","device":"ESP32-01","status":"ONLINE"}
{"type":"sensor_reading","data":{"device":"ESP32-01","soil_moisture":50.0,"temperature":28.0,"timestamp":"2026-09-25T17:45:45Z","source":"esp32"}}
{"type":"alert","data":{"type":"LOW_SOIL_MOISTURE","severity":"warning","device":"ESP32-01","message":"Soil moisture is below the configured threshold.","timestamp":"..."}}
{"type":"ai_insights","device":"ESP32-01","data":[{"type":"MOISTURE_STABLE","severity":"info","message":"Soil moisture is currently stable."}]}
```

Statuses are `ONLINE`, `OFFLINE`, `CONNECTING`, and `UNKNOWN`; a known device moves to `OFFLINE` after the configured timeout, not after an isolated missed sample. The server validates RFC6455 framing, enforces masked client messages and frame-size limits, replies to ping frames, cleans up disconnects, and continues broadcast delivery even if a client fails. For ESP32 deployments over an untrusted network, add authentication and TLS at a trusted reverse proxy before exposing the service beyond a private LAN.

## ESP32 over WebSocket (push instead of polling)

Firmware that would rather hold one socket open than POST every reading can connect to `ws://192.168.1.105:5000/ws?role=device` and send one JSON reading object per text frame. The device socket is a separate role from dashboard clients: it is never added to the browser broadcast peers, receives no dashboard events, and every accepted reading runs through the exact same validation, storage, alert and insight pipeline as `POST /api/readings` (with `source` recorded as `esp32`).

Send:

```json
{"device":"ESP32-01","soil_moisture":42,"temperature":26.5,"humidity":61,"timestamp":"2026-09-26T09:00:00Z"}
```

Receive exactly one reply per frame:

```json
{"type":"welcome","role":"device","server_time":"2026-09-26T09:00:00.000Z","hint":"send one JSON reading object per text frame"}
{"type":"ack","device":"ESP32-01","timestamp":"2026-09-26T09:00:00Z","duplicate":false,"server_time":"2026-09-26T09:00:00.000Z"}
{"type":"error","code":"INVALID_READING","message":"missing required field: soil_moisture"}
```

`error.code` is `INVALID_JSON`, `INVALID_READING`, or `STORAGE_UNAVAILABLE`; a rejected frame never closes the socket, so firmware can keep sending. `ack.duplicate` is `true` when the device/timestamp pair was already stored, which makes retry-after-reconnect safe. Server-initiated ping frames and the 1 MiB frame limit from the browser contract still apply.

## AI advisor (`backend/agri_ai.py`)

The **AI advisor** tab runs the agricultural AI core as a self-contained module: a real external-data
layer (Open-Meteo weather, SoilGrids soil properties, NASA POWER monthly climatology), two small
neural networks, rule-based plant colour analysis, crop-knowledge candidate retrieval, a Qwen
reasoning layer, and a deterministic irrigation controller that has final authority. Each run is
also logged to its own SQLite file.

The module is vendored unchanged in its logic; the only edits are that its heavy imports are now
guarded, its model training is lazy instead of running at import time, and its database and capture
paths are configurable (see the environment table above).

### Optional dependencies

The core is imported lazily, so this backend still starts and serves every other page on a machine
without it. A run needs `pip install torch opencv-python numpy`; the reasoning layer additionally
needs `pip install openai` and Ollama running locally with `qwen3:14b` pulled.

When something is missing, `/api/ai/status` and the AI page state exactly what is missing and how to
install it, and `POST /api/ai/analyze` answers `503 AI_UNAVAILABLE`. The pipeline is never run in a
degraded form that would present invented numbers as a result. Without `openai`/Ollama the pipeline
does still run, but each plan and diagnosis comes back with `ai_status: "fallback"` and the
dashboard message is prefixed to say so.

### Runs are jobs

A run takes seconds to minutes (three external HTTP calls, two network trainings, camera capture),
so it executes on a single background worker instead of inline in the request. `POST /api/ai/analyze`
returns `202` with a `job_id`, and the client polls `GET /api/ai/jobs/{jobId}` until the status is
`succeeded` or `failed`. At most four runs may be queued at once; further submissions get
`429 AI_BUSY`. The most recent 25 finished runs are retained in memory. Models are trained once per
process on first use and cached; `POST /api/ai/warmup` starts that training ahead of the first run.

### What it does not do — stated, not hidden

- Both networks are trained on **synthetic proxy data** generated by transparent rule functions in
the file, not on real GAEZ/FAOSTAT/NASA/SoilGrids historical records. The exact disclosure is
returned by `/api/ai/status` and shown on the page.
- NPK, NDVI and salinity are reported as **unavailable** rather than estimated, because no free,
unauthenticated source for them is integrated. The reasoning layer is instructed to lower its
confidence where those gaps matter.
- The irrigation output is a **recommendation and a log entry only**. There is no pump, valve or
reservoir-sensor control anywhere in this code.
- Plant stress and disease findings are threshold rules carrying an explicit uncertainty score,
phrased as suspicions rather than lab-confirmed diagnoses.
- A run is supplementary to local agronomic advice, never a substitute for it.

## Dashboard navigation

The dashboard is split into separate tabs instead of one long page. Each view has its own URL hash (`#overview`, `#crop-library`, `#suitability`, `#monitoring`, `#insights`, `#devices`, `#alerts`, `#ai-advisor`), so tabs are deep-linkable, work with back/forward, and reload to the same view. Blocks in `index.html` declare membership with `data-view="..."` and `views.js` shows only the active view. The GPS map remains its own dedicated page at `?view=map`, with all of the other blocks hidden by the backend-independent map mode in `app.js`. A view that contains a single panel (Devices or Alerts) expands it to the full width.

## Tests

Run the standard-library API, persistence, simulation, validation, alert, statistics, CORS, and WebSocket integration tests:

```powershell
py -3 -m unittest discover -s backend -v
```
