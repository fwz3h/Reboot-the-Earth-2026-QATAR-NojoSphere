/* ==========================================================================
   Mazraa -- frontend logic
   Plain JavaScript, no build step. Four jobs:
     1. show a map and remember the pin you clicked
     2. send {lat, lon, area, budget, lang} to the backend
     3. draw the returned recommendations as cards
     4. keep everything in the farmer's own language (see i18n.js)
   ========================================================================== */

/* Everything in this file is wrapped in a function so the names below (t, form,
   map, ...) are private to this file. Two <script> files share one global scope,
   and a clash there would stop the whole page from working. */
(function () {
  "use strict";

/* --------------------------------------------------------------------------
   1. CONFIGURATION
   -------------------------------------------------------------------------- */

// Where the Flask backend is running. If you change app.py's port, change it here.
const API_BASE = "http://localhost:5000";

// How many cards to show before the farmer presses "Show all options".
const CARDS_TO_PREVIEW = 6;

// Where the map opens. Change these two numbers to start over your own country.
const MAP_START = { lat: 26.0, lon: 42.0, zoom: 5 };

// Downloaded demo-location data (written by backend/fetch_offline_data.py).
// If the backend cannot be reached, the page computes the recommendations
// itself from the nearest pack + data/crops.json, so the demo still works
// with zero internet (that is what the APK ships with too).
const OFFLINE_DIR = "data/offline_packs/";
const CROPS_FILE = "data/crops.json";

/* --------------------------------------------------------------------------
   2. LANGUAGE HELPERS (all the text lives in i18n.js)
   -------------------------------------------------------------------------- */
const { t, getLocale, onLanguageChange, initI18n, getLanguage } = window.MazraaI18N;

/* --------------------------------------------------------------------------
   3. ELEMENTS FROM THE PAGE (looked up once, so the code stays tidy)
   -------------------------------------------------------------------------- */
const form = document.getElementById("recommend-form");
const areaInput = document.getElementById("area");
const budgetInput = document.getElementById("budget");
const submitButton = document.getElementById("submit-button");
const formMessage = document.getElementById("form-message");
const statusPill = document.getElementById("backend-status");
const locationReadout = document.getElementById("location-readout");
const resultsSection = document.getElementById("results-section");
const resultsGrid = document.getElementById("results");
const resultsCount = document.getElementById("results-count");
const siteSummary = document.getElementById("site-summary");
const toggleAllButton = document.getElementById("toggle-all");
const loadingBox = document.getElementById("loading");
const loadingText = document.getElementById("loading-text");

/* --------------------------------------------------------------------------
   4. STATE (things the page remembers between clicks)
   -------------------------------------------------------------------------- */
let map = null;             // the Leaflet map itself
let selectedPoint = null;   // {lat, lon} from the last map click, or null
let marker = null;          // the Leaflet pin
let areaCircle = null;      // the Leaflet circle showing the land size
let lastOptions = [];       // the options returned by the backend
let lastSite = null;        // the climate/soil summary returned by the backend
let showingAll = false;     // is the "show all" button currently expanded?
let offlineMeta = null;     // {place, distanceKm} when results came from an offline pack

/* --------------------------------------------------------------------------
   5. THE MAP
   -------------------------------------------------------------------------- */

/**
 * Start the map.
 *
 * This is wrapped in try/catch on purpose: Leaflet and the map tiles both come
 * from the internet, so on a farm with no signal the map cannot start. The rest
 * of the page must keep working (and stay translated) instead of going blank, so
 * we say what happened in the farmer's language.
 */
function initMap() {
  const mapBox = document.getElementById("map");
  try {
    map = L.map("map").setView([MAP_START.lat, MAP_START.lon], MAP_START.zoom);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);

    // Clicking anywhere on the map drops (or moves) the pin.
    map.on("click", (event) => {
      setLocation(event.latlng.lat, event.latlng.lng);
    });
  } catch (error) {
    console.error("Could not start the map:", error);
    map = null;
    mapBox.textContent = t("error.mapFailed");
    mapBox.classList.add("map-failed");
  }
}

/**
 * Remember the chosen point and draw the pin + the circle that stands for the
 * land area.
 */
function setLocation(lat, lon) {
  selectedPoint = { lat: lat, lon: lon };
  if (!map) return;   // the map failed to load; there is nothing to draw

  if (marker) {
    marker.setLatLng([lat, lon]);
  } else {
    marker = L.marker([lat, lon]).addTo(map);
  }

  updateAreaCircle();
  updateLocationReadout();
}

/** Draw a circle whose radius matches the area in hectares. */
function updateAreaCircle() {
  if (!selectedPoint || !map) return;

  const areaHa = parseFloat(areaInput.value);
  if (!(areaHa > 0)) return;

  // Area of a circle = pi * r^2, and 1 hectare = 10,000 square metres.
  const radiusMetres = Math.sqrt((areaHa * 10000) / Math.PI);

  if (areaCircle) {
    areaCircle.setLatLng([selectedPoint.lat, selectedPoint.lon]);
    areaCircle.setRadius(radiusMetres);
  } else {
    areaCircle = L.circle([selectedPoint.lat, selectedPoint.lon], {
      radius: radiusMetres,
      color: "#1d7a44",
      weight: 2,
      fillColor: "#1d7a44",
      fillOpacity: 0.12,
    }).addTo(map);
  }
}

function updateLocationReadout() {
  if (!selectedPoint) {
    locationReadout.textContent = t("location.none");
    return;
  }
  // The sentence itself comes from the translation file, with the numbers
  // dropped into {lat} and {lon}.
  locationReadout.textContent = t("location.selected", {
    lat: formatDecimal(selectedPoint.lat, 4),
    lon: formatDecimal(selectedPoint.lon, 4),
  });
}

// Redraw the circle whenever the area changes.
areaInput.addEventListener("input", updateAreaCircle);

/* --------------------------------------------------------------------------
   6. SMALL HELPERS (messages, formatting, DOM building)
   -------------------------------------------------------------------------- */
function showFormMessage(text, kind) {
  formMessage.textContent = text;
  formMessage.className = "form-message " + (kind || "info");
  formMessage.hidden = false;
}

function hideFormMessage() {
  formMessage.hidden = true;
  formMessage.textContent = "";
}

function showLoading(text) {
  loadingText.textContent = text || t("form.working");
  loadingBox.hidden = false;
}

function hideLoading() {
  loadingBox.hidden = true;
}

/* Number formatting follows the chosen language: a French farmer sees
   "1 234,5" and a Hindi farmer sees "1,23,456", because that is what they
   read every day. */

function isValidNumber(value) {
  return value !== null && value !== undefined && !Number.isNaN(Number(value));
}

/** 1234567 -> "1,234,567" */
function formatInt(value) {
  if (!isValidNumber(value)) return "—";
  return new Intl.NumberFormat(getLocale(), { maximumFractionDigits: 0 }).format(Number(value));
}

/** 1234.5 -> "1,234.5" (or "1 234,5" in French) */
function formatDecimal(value, decimals) {
  if (!isValidNumber(value)) return "—";
  return new Intl.NumberFormat(getLocale(), {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(Number(value));
}

/** 150000 -> "$150,000" (written the local way, always US dollars) */
function formatMoney(value) {
  if (!isValidNumber(value)) return "—";
  try {
    return new Intl.NumberFormat(getLocale(), {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    }).format(Number(value));
  } catch (error) {
    // Some browsers are picky about unusual locale tags; fall back to plain "$".
    return "$" + formatInt(value);
  }
}

/** Payback can be null, which means "never pays back with these numbers". */
function formatPayback(years) {
  if (years === null || years === undefined) return t("payback.never");
  if (years < 1) {
    const months = Math.max(1, Math.round(years * 12));
    return t("payback.months", { n: formatInt(months) });
  }
  return t("payback.years", { n: formatDecimal(years, 1) });
}

/** Create an element in one line: el("p", "crop-name", "Tomato"). */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  // textContent is used instead of innerHTML so that any text coming from the
  // backend (including the AI-written sentence) can never inject HTML.
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/* --------------------------------------------------------------------------
   7. TALKING TO THE BACKEND
   -------------------------------------------------------------------------- */

/** Ping /health so the header pill can say whether the server is up. */
async function checkBackend() {
  try {
    const response = await fetch(API_BASE + "/health");
    if (!response.ok) throw new Error("HTTP " + response.status);
    statusPill.textContent = t("status.ok");
    statusPill.className = "status-pill status-ok";
  } catch (error) {
    statusPill.textContent = t("status.offline");
    statusPill.className = "status-pill status-bad";
    console.warn("Could not reach the backend:", error);
  }
}

/** The main action: send the form (or just a language change) to the backend. */
async function loadRecommendations() {
  hideFormMessage();

  // --- Validate before spending time on a request ---
  if (!selectedPoint) {
    showFormMessage(t("error.pickLocation"), "error");
    return;
  }

  const area = parseFloat(areaInput.value);
  if (!(area > 0)) {
    showFormMessage(t("error.areaInvalid"), "error");
    return;
  }

  const budget = parseFloat(budgetInput.value) || 0;

  const payload = {
    lat: selectedPoint.lat,
    lon: selectedPoint.lon,
    area: area,
    budget: budget,
    // The backend writes its explanations in this language and translates the
    // crop and farming-system names for us.
    lang: getLanguage(),
  };

  // --- Show that something is happening ---
  submitButton.disabled = true;
  submitButton.textContent = t("form.working");
  showLoading(t("loading.lookup"));

  // "reachedBackend" separates "the server said no" (show its error) from
  // "we never got an answer" (fall back to the downloaded offline packs).
  let reachedBackend = false;
  try {
    const response = await fetch(API_BASE + "/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    reachedBackend = true;

    // The backend answers with {"error": "..."} for a bad request.
    const data = await response.json().catch(() => null);

    if (!response.ok) {
      const message = (data && data.error) ? data.error : "HTTP " + response.status;
      throw new Error(message);
    }
    if (!data || !Array.isArray(data.options)) {
      throw new Error(t("error.badAnswer"));
    }

    offlineMeta = null;
    lastOptions = data.options;
    lastSite = data.site;
    showingAll = false;
    renderSiteSummary();
    renderResults();
    resultsSection.hidden = false;
    checkBackend();
  } catch (error) {
    if (!reachedBackend) {
      // The backend is not running (or there is no network). Instead of
      // giving up, compute the same ranked list in the browser from the
      // nearest downloaded pack -- the demo loop keeps working.
      console.warn("Backend unreachable, falling back to offline packs:", error);
      try {
        await loadOfflineRecommendations(payload);
        checkBackend();   // flips the pill to "Backend offline"
      } catch (offlineError) {
        console.error(offlineError);
        showFormMessage(t("error.fetchFailed", { message: error.message }), "error");
      }
    } else {
      console.error(error);
      showFormMessage(t("error.fetchFailed", { message: error.message }), "error");
    }
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = t("form.submit");
    hideLoading();
  }
}

function handleSubmit(event) {
  event.preventDefault();
  loadRecommendations();
}

/* --------------------------------------------------------------------------
   7b. OFFLINE FALLBACK (no backend / no internet)

   backend/fetch_offline_data.py downloads one JSON pack per demo location
   into data/offline_packs/. When the backend cannot be reached, this section
   picks the pack nearest to the pin and runs the SAME maths as engine.py
   right here in the browser, using the hand-typed tables in data/crops.json.
   The numbers therefore match what the backend would have said (both are the
   same placeholders), and the core loop -- map -> budget -> ranked list with
   cost and payback -- never dead-ends.
   -------------------------------------------------------------------------- */

/** fetch() a JSON file, with a real error message if it is missing. */
async function loadJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(url + " -> HTTP " + response.status);
  return response.json();
}

/** Straight-line distance in km (used to pick the nearest offline pack). */
function haversineKm(lat1, lon1, lat2, lon2) {
  const toRad = (degrees) => (degrees * Math.PI) / 180;
  const R = 6371;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** Read data/offline_packs/index.json and return the pack closest to the pin. */
async function nearestOfflinePack(lat, lon) {
  const index = await loadJson(OFFLINE_DIR + "index.json");
  if (!index || !Array.isArray(index.packs) || !index.packs.length) {
    throw new Error("offline pack index is empty");
  }
  let best = null;
  let bestKm = Infinity;
  index.packs.forEach((entry) => {
    const km = haversineKm(lat, lon, entry.lat, entry.lon);
    if (km < bestKm) {
      bestKm = km;
      best = entry;
    }
  });
  const pack = await loadJson(OFFLINE_DIR + best.file);
  if (!pack.label) pack.label = best.label;
  return pack;
}

/**
 * The offline twin of engine.score_suitability(): how well does this crop fit
 * this place (and this farming system)? Same weights, same tolerance, same
 * climate-control blending as backend/engine.py.
 */
function offlineScore(crop, system, systemKey, site) {
  const rangeScore = (value, low, high) => {
    if (value === null || value === undefined) return 0.5;   // unknown = average
    if (low <= value && value <= high) return 1.0;
    const width = (high - low) > 0 ? (high - low) : 1.0;
    const distance = value < low ? low - value : value - high;
    return Math.max(0.0, 1.0 - distance / (width * 0.5));
  };

  const tempScore = rangeScore(site.avg_temp_c, crop.temp_range[0], crop.temp_range[1]);
  const rainScore = rangeScore(site.rainfall_mm, crop.rain_range[0], crop.rain_range[1]);
  const phScore = rangeScore(site.soil_ph, crop.ph_range[0], crop.ph_range[1]);

  let climate = 0.45 * tempScore + 0.35 * rainScore + 0.20 * phScore;

  // A controlled environment hides some of the weather.
  const control = system.climate_control;
  if (control > 0) {
    climate = (1 - control) * climate + control * system.control_quality;
  }

  const fit = crop.system_fit[systemKey] !== undefined ? crop.system_fit[systemKey] : 0.7;
  return Math.round(Math.max(0, Math.min(1, climate * fit)) * 10000) / 10000;
}

/** The offline twin of model.predict_yield_ml()'s placeholder rule. */
function offlineYieldFactor(features) {
  let factor = 0.90 + 0.20 * Number(features.suitability || 0.7);
  const temp = features.avg_temp_c;
  if (temp !== null && temp !== undefined && (temp > 35 || temp < 2)) factor -= 0.10;
  if ((features.area || 0) > 50) factor -= 0.03;
  return Math.round(Math.max(0.70, Math.min(1.20, factor)) * 1000) / 1000;
}

/** Round like Python's round(x, dp) for the display values. */
function round(value, dp) {
  const f = 10 ** dp;
  return Math.round(value * f) / f;
}

/**
 * The offline twin of engine.calculate_economics(): yield, water, energy and
 * the money numbers for one crop + system + area. Returns the same keys the
 * backend returns, so buildCard() needs no changes at all.
 */
function offlineEconomics(crop, system, systemKey, area, site, suitability, prices) {
  const siteFactor = 0.55 + 0.45 * suitability;
  const mlFactor = offlineYieldFactor({
    suitability: suitability,
    avg_temp_c: site.avg_temp_c,
    area: area,
  });

  const yieldPerHa = crop.base_yield * system.yield_mult * siteFactor * mlFactor;
  const yieldTonnes = yieldPerHa * area;

  // Free rain only helps open fields; the other systems recirculate.
  const cropWaterMm = crop.water_need / prices.m3_per_ha_per_mm;
  const rainfallMm = site.rainfall_mm || 0;
  const usefulRainMm = 0.7 * rainfallMm;
  const irrigationMm = systemKey === "open_field"
    ? Math.max(0.15 * cropWaterMm, cropWaterMm - usefulRainMm)
    : cropWaterMm;
  const waterM3 = irrigationMm * prices.m3_per_ha_per_mm * system.water_mult * area;

  const energyKwh = crop.energy_need * system.energy_mult * area;
  const capex = (crop.base_capex + system.capex_per_ha) * area;

  const seedCost = crop.seed_cost * area;
  const labourCost = crop.labour_cost * area * system.labour_mult;
  const energyCost = energyKwh * prices.energy_usd_per_kwh;
  const waterCost = waterM3 * prices.water_usd_per_m3;
  const maintenanceCost = capex * prices.maintenance_rate;
  const revenue = yieldTonnes * crop.price;
  const marketingCost = revenue * prices.marketing_rate;

  const opex = seedCost + labourCost + energyCost + waterCost
    + maintenanceCost + marketingCost;
  const annualProfit = revenue - opex;

  return {
    yield_per_ha: round(yieldPerHa, 2),
    yield_tonnes: round(yieldTonnes, 2),
    water_m3: round(waterM3, 1),
    energy_kwh: round(energyKwh, 1),
    capex_usd: round(capex, 2),
    opex_usd: round(opex, 2),
    revenue_usd: round(revenue, 2),
    annual_profit_usd: round(annualProfit, 2),
    payback_years: annualProfit > 0 ? round(capex / annualProfit, 2) : null,
  };
}

/**
 * Build the whole ranked list in the browser. Mirrors app.py: score every
 * crop in every system, mark budget/profit, then sort viable options first,
 * best suitability, quickest payback, cheapest build.
 */
async function loadOfflineRecommendations(payload) {
  const [pack, tables] = await Promise.all([
    nearestOfflinePack(payload.lat, payload.lon),
    loadJson(CROPS_FILE),
  ]);
  if (!pack.site) throw new Error("offline pack has no site data");

  const lang = getLanguage();
  const prices = tables.prices;
  const budgetLimited = payload.budget > 0;
  const options = [];

  Object.keys(tables.crops).forEach((cropKey) => {
    const crop = tables.crops[cropKey];
    Object.keys(tables.systems).forEach((systemKey) => {
      const system = tables.systems[systemKey];
      const suitability = offlineScore(crop, system, systemKey, pack.site);
      const eco = offlineEconomics(
        crop, system, systemKey, payload.area, pack.site, suitability, prices);

      const withinBudget = !budgetLimited || eco.capex_usd <= payload.budget;
      const profitable = eco.payback_years !== null;

      options.push({
        crop: cropKey,
        crop_name: (crop.names && crop.names[lang]) || crop.name,
        crop_name_en: crop.name,
        system: systemKey,
        system_name: (system.names && system.names[lang]) || system.label,
        system_name_en: system.label,
        area_ha: payload.area,
        suitability: suitability,
        score_percent: round(suitability * 100.0, 1),
        yield_per_ha: eco.yield_per_ha,
        yield_tonnes: eco.yield_tonnes,
        water_m3: eco.water_m3,
        energy_kwh: eco.energy_kwh,
        capex_usd: eco.capex_usd,
        opex_usd: eco.opex_usd,
        revenue_usd: eco.revenue_usd,
        annual_profit_usd: eco.annual_profit_usd,
        payback_years: eco.payback_years,
        within_budget: withinBudget,
        budget_gap_usd: budgetLimited ? round(eco.capex_usd - payload.budget, 2) : 0,
        profitable: profitable,
        viable: withinBudget && profitable,
      });
    });
  });

  // Exactly the ranking app.py uses.
  options.sort((a, b) => {
    if (a.viable !== b.viable) return a.viable ? -1 : 1;
    if (b.suitability !== a.suitability) return b.suitability - a.suitability;
    const pa = a.payback_years === null ? 999 : a.payback_years;
    const pb = b.payback_years === null ? 999 : b.payback_years;
    if (pa !== pb) return pa - pb;
    return a.capex_usd - b.capex_usd;
  });

  options.forEach((option, i) => {
    option.rank = i + 1;
    // Hand-written sentence in the farmer's language (no AI needed offline).
    option.explanation = t("offline.explanation", {
      crop: option.crop_name,
      system: option.system_name,
      score: formatDecimal(option.score_percent, 1),
      capex: formatMoney(option.capex_usd),
      payback: formatPayback(option.payback_years),
    });
    option.explanation_source = "template";
  });

  offlineMeta = {
    place: pack.label,
    distanceKm: Math.round(haversineKm(
      payload.lat, payload.lon, pack.lat || pack.site.lat, pack.lon || pack.site.lon)),
  };
  lastOptions = options;
  // Keep the pack's own coordinates in the summary: the climate numbers below
  // describe THAT point, not necessarily the exact pin.
  lastSite = pack.site;
  showingAll = false;
  renderSiteSummary();
  renderResults();
  resultsSection.hidden = false;
}

/* --------------------------------------------------------------------------
   8. RENDERING
   -------------------------------------------------------------------------- */

/** The row of chips showing the climate and soil data used. */
function renderSiteSummary() {
  siteSummary.innerHTML = "";
  const site = lastSite;
  if (!site) return;

  const chips = [
    { label: "site.coords", value: formatDecimal(site.lat, 4) + ", " + formatDecimal(site.lon, 4) },
    { label: "site.temp", value: formatDecimal(site.avg_temp_c, 1) + " °C" },
    { label: "site.rain", value: formatInt(site.rainfall_mm) + " mm" },
    { label: "site.solar", value: formatDecimal(site.solar_radiation, 2) + " kWh/m²" },
    { label: "site.ph", value: formatDecimal(site.soil_ph, 1) },
  ];

  chips.forEach((chip) => {
    const node = el("div", "site-chip");
    node.appendChild(el("span", null, t(chip.label)));
    node.appendChild(el("b", null, chip.value));
    siteSummary.appendChild(node);
  });

  // Say clearly when the numbers came from a downloaded pack instead of the
  // live backend, and which demo location it was, so nobody is misled.
  if (offlineMeta) {
    siteSummary.appendChild(el("p", "site-note", t("site.offlineNote", {
      place: offlineMeta.place,
      distance: formatInt(offlineMeta.distanceKm),
    })));
  }

  // Warn the farmer when the live APIs were unavailable, so nobody is misled.
  if (site.source === "fallback" || site.source === "mixed") {
    const messages = [t("site.placeholderNote")];
    // The API error text itself is technical and stays in English.
    if (site.notes && site.notes.length) messages.push(site.notes.join(" "));
    siteSummary.appendChild(el("p", "site-note", messages.join(" ")));
  } else if (site.source === "cache") {
    siteSummary.appendChild(el("p", "site-note", t("site.cacheNote")));
  }
}

/** Draw the cards: the best few, or all of them if "show all" was pressed. */
function renderResults() {
  resultsGrid.innerHTML = "";
  if (!lastOptions.length) return;

  const visible = showingAll ? lastOptions : lastOptions.slice(0, CARDS_TO_PREVIEW);
  visible.forEach((option) => {
    resultsGrid.appendChild(buildCard(option));
  });

  resultsCount.textContent = t("results.showing", {
    visible: formatInt(visible.length),
    total: formatInt(lastOptions.length),
  });

  // The toggle is only useful when there are more options than we preview.
  toggleAllButton.hidden = lastOptions.length <= CARDS_TO_PREVIEW;
  toggleAllButton.textContent = showingAll
    ? t("results.showTop", { n: formatInt(CARDS_TO_PREVIEW) })
    : t("results.showAll");
}

/** Build one result card. */
function buildCard(option) {
  const card = el("article", "result-card");
  if (option.rank === 1) card.classList.add("best");
  if (option.within_budget === false) card.classList.add("over-budget");

  // --- Header: rank badge + crop and system names ---
  // The names arrive already translated from the backend (see engine.py).
  const head = el("div", "card-head");
  const titleBox = el("div");
  titleBox.appendChild(el("h3", "crop-name", option.crop_name));
  titleBox.appendChild(el("p", "system-name",
    option.system_name + " · " + formatDecimal(option.area_ha, 2) + " ha"));
  head.appendChild(titleBox);
  head.appendChild(el("span", "rank-badge", "#" + formatInt(option.rank)));
  card.appendChild(head);

  // --- Suitability score bar ---
  const scoreRow = el("div", "score-row");
  const bar = el("div", "score-bar");
  const fill = el("div");
  fill.style.width = Math.max(0, Math.min(100, option.score_percent)) + "%";
  bar.appendChild(fill);
  scoreRow.appendChild(bar);
  scoreRow.appendChild(el("span", "score-value", formatDecimal(option.score_percent, 0) + "%"));
  card.appendChild(scoreRow);

  // --- The numbers ---
  const metrics = el("dl", "metrics");
  const rows = [
    ["metric.suitability", formatDecimal(option.suitability, 3)],
    ["metric.yield", formatDecimal(option.yield_tonnes, 1) + " " + t("unit.yield")],
    ["metric.water", formatInt(option.water_m3) + " " + t("unit.water")],
    ["metric.energy", formatInt(option.energy_kwh) + " " + t("unit.energy")],
    ["metric.capex", formatMoney(option.capex_usd)],
    ["metric.opex", formatMoney(option.opex_usd)],
    ["metric.revenue", formatMoney(option.revenue_usd)],
    ["metric.profit", formatMoney(option.annual_profit_usd)],
    ["metric.payback", formatPayback(option.payback_years)],
  ];
  rows.forEach(([labelKey, value]) => {
    const cell = el("div");
    cell.appendChild(el("dt", null, t(labelKey)));
    cell.appendChild(el("dd", null, value));
    metrics.appendChild(cell);
  });
  card.appendChild(metrics);

  // --- Badges: can you afford it, and does it make money? ---
  const badges = el("div", "badges");
  if (option.within_budget) {
    badges.appendChild(el("span", "badge good", t("badge.withinBudget")));
  } else {
    badges.appendChild(el("span", "badge warn",
      t("badge.overBudget", { amount: formatMoney(Math.abs(option.budget_gap_usd)) })));
  }
  if (option.profitable) {
    badges.appendChild(el("span", "badge good", t("badge.profitable")));
  } else {
    badges.appendChild(el("span", "badge bad", t("badge.losesMoney")));
  }
  if (option.explanation_source === "ai") {
    badges.appendChild(el("span", "badge good", t("badge.ai")));
  }
  card.appendChild(badges);

  // --- The explanation sentence, already in the chosen language ---
  card.appendChild(el("p", "explanation", option.explanation || ""));

  return card;
}

/* --------------------------------------------------------------------------
   9. WIRE UP THE PAGE
   -------------------------------------------------------------------------- */
form.addEventListener("submit", handleSubmit);

toggleAllButton.addEventListener("click", () => {
  showingAll = !showingAll;
  renderResults();
});

// When the farmer changes language, redraw everything that is not covered by
// the data-i18n attributes. If we already have results, we ask the backend
// again so the explanation sentences and the crop names match the new language.
onLanguageChange(() => {
  updateLocationReadout();
  hideFormMessage();
  // Re-check the backend so even the status pill is written in the new language.
  statusPill.textContent = t("status.checking");
  statusPill.className = "status-pill status-unknown";
  checkBackend();

  if (lastOptions.length) {
    renderSiteSummary();
    renderResults();
    if (selectedPoint) {
      loadRecommendations();
    }
  }
});

// Re-check the backend when the tab regains focus (handy while developing).
window.addEventListener("focus", checkBackend);

// Start up, in this order:
//  1. fill in all the translated text and pick the browser's language
//  2. then start the map (so a CDN failure cannot stop the text appearing)
//  3. then check the backend
initI18n();
initMap();
updateAreaCircle();
checkBackend();

})();
