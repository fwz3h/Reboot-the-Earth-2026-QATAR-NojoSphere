/*
 * ai-advisor.js — drives the AI advisor page.
 *
 * The AI core (backend/agri_ai.py) needs PyTorch plus OpenCV and can take
 * minutes per run, so the backend runs each request as a job on a worker
 * thread. This script submits a run, polls for the result, and renders it.
 *
 * It reports what the backend actually did — including when a dependency is
 * missing or when the reasoning layer fell back to rule-based output. Nothing
 * here renders a placeholder as if it were a real result.
 */
(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const FILE_ORIGIN = location.protocol === 'file:';
  const API = FILE_ORIGIN ? 'http://localhost:5000' : '';
  const POLL_INTERVAL_MS = 1500;
  const POLL_LIMIT_MS = 15 * 60 * 1000;

  const form = $('#ai-form');
  if (!form) return;

  let pollTimer = null;
  let pollDeadline = 0;

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]
  ));

  const show = (node, visible) => { if (node) node.hidden = !visible; };

  async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    });
    let body = null;
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok) {
      const message = body?.error?.message || body?.message || `Request failed (${response.status})`;
      const error = new Error(message);
      error.status = response.status;
      error.code = body?.error?.code || '';
      error.body = body;
      throw error;
    }
    return body;
  }

  // ---- number formatting -------------------------------------------------

  const num = (value, digits = 1) => (Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—');
  const orDash = (value) => (value === null || value === undefined || value === '' ? '—' : String(value));
  const yesNo = (value) => (value === true ? 'Yes' : value === false ? 'No' : '—');
  const list = (items, className = 'ai-list') => {
    if (!Array.isArray(items) || !items.length) return '<p class="ai-muted">None reported.</p>';
    return `<ul class="${className}">${items.map((item) => `<li>${escapeHtml(
      typeof item === 'string' ? item : JSON.stringify(item))}</li>`).join('')}</ul>`;
  };
  const card = (title, eyebrow, body, extraClass = '') => `
    <article class="panel glass-card ai-result-card ${extraClass}">
      <div class="panel-heading compact"><div><div class="eyebrow">${escapeHtml(eyebrow)}</div><h2>${escapeHtml(title)}</h2></div></div>
      <div class="ai-result-body">${body}</div>
    </article>`;

  // ---- status ------------------------------------------------------------

  function renderStatus(status) {
    const badge = $('#ai-status-badge');
    const panel = $('#ai-unavailable');
    const submit = $('#ai-run');
    const warm = $('#ai-warm');
    if (!status) return;

    const ready = Boolean(status.available);
    if (badge) {
      badge.className = `screening-badge ${ready ? 'ai-ready' : 'ai-blocked'}`;
      badge.innerHTML = ready
        ? `<span>✳</span> ${escapeHtml(status.model_state === 'ready' ? 'MODELS READY' : 'READY')}`
        : '<span>!</span> UNAVAILABLE';
    }
    if (submit) submit.disabled = !ready;
    if (warm) warm.disabled = !ready;

    if (ready) {
      show(panel, false);
      return;
    }
    if (panel) {
      const missing = (status.missing_packages || []).map(escapeHtml).join(' ');
      panel.innerHTML = `
        <b>The AI advisor can't run on this machine yet.</b>
        <p>${escapeHtml(status.reason || 'A required dependency is missing.')}</p>
        <ul class="ai-list">
          <li>PyTorch ${status.torch_available ? 'installed' : 'missing — required for both neural networks'}</li>
          <li>OpenCV + NumPy ${status.vision_available ? 'installed' : 'missing — required for plant colour analysis and camera capture'}</li>
          <li>Qwen reasoning client ${status.reasoning_client_available ? 'installed' : 'not installed — the pipeline still runs, flagged as fallback mode'}</li>
          <li>Ollama server ${status.reasoning_server_reachable ? 'reachable' : 'not reachable on localhost:11434 — the pipeline falls back to rule-based output'}</li>
        </ul>
        ${missing ? `<p class="ai-install">Install on the machine running this backend: <code>pip install ${missing}</code></p>` : ''}
        <p class="ai-muted">The rest of the dashboard keeps working normally without it.</p>`;
      show(panel, true);
    }
  }

  async function refreshStatus() {
    try {
      const status = await request('/api/ai/status');
      renderStatus(status);
      const disclosure = $('#ai-disclosure');
      if (disclosure && status.proxy_data_disclosure) {
        disclosure.textContent = status.proxy_data_disclosure;
      }
      const proxy = $('#ai-proxy-state');
      if (proxy) {
        proxy.textContent = status.model_state === 'ready'
          ? 'Both networks trained and cached in this backend process.'
          : status.model_state === 'training'
            ? 'Training the two proxy networks in the background — the first run may wait for this.'
            : 'Not trained yet. Training starts on the first run, or press “Train models now”.';
      }
    } catch (error) {
      renderStatus({
        available: false,
        reason: `The AI endpoints could not be reached: ${error.message}`,
        missing_packages: [], torch_available: false, vision_available: false,
        reasoning_client_available: false, reasoning_server_reachable: false,
      });
    }
  }

  // ---- run submission ----------------------------------------------------

  function readForm() {
    const value = (name) => form.elements[name]?.value?.trim() ?? '';
    if (!navigator.onLine) throw new Error('This device is offline — the AI run needs the backend and its live data sources.');

    const payload = {
      latitude: value('latitude'),
      longitude: value('longitude'),
      soil_moisture_pct: value('soil_moisture_pct'),
      air_temp_c: value('air_temp_c'),
      air_humidity_pct: value('air_humidity_pct'),
      ldr_pct: value('ldr_pct'),
      mode: value('mode') || 'plan',
    };
    const optional = ['budget', 'crop_moisture_threshold_pct', 'camera_index', 'region'];
    optional.forEach((name) => {
      const raw = value(name);
      if (raw !== '') payload[name] = name === 'camera_index' ? Number.parseInt(raw, 10) : raw;
    });
    return payload;
  }

  function setBusy(busy, message) {
    const progress = $('#ai-progress');
    const submit = $('#ai-run');
    if (submit) {
      submit.disabled = busy;
      submit.classList.toggle('running', busy);
    }
    if (progress) {
      progress.hidden = !busy;
      if (busy && message) $('#ai-progress-text').textContent = message;
    }
  }

  function clearPolling() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  }

  function showError(message, detail) {
    const node = $('#ai-error');
    if (!node) return;
    node.hidden = false;
    node.innerHTML = `<b>${escapeHtml(message)}</b>${detail ? `<p>${escapeHtml(detail)}</p>` : ''}`;
  }

  function clearError() {
    const node = $('#ai-error');
    if (node) { node.hidden = true; node.innerHTML = ''; }
  }

  async function startRun(event) {
    event.preventDefault();
    clearError();
    let payload;
    try {
      payload = readForm();
    } catch (error) {
      showError("The run wasn't started.", error.message);
      return;
    }
    setBusy(true, 'Submitting the run to the backend…');
    try {
      const job = await request('/api/ai/analyze', { method: 'POST', body: JSON.stringify(payload) });
      pollDeadline = Date.now() + POLL_LIMIT_MS;
      setBusy(true, 'Queued. Fetching live weather, soil and climate data…');
      poll(job.job_id);
    } catch (error) {
      setBusy(false);
      if (error.code === 'AI_UNAVAILABLE') {
        showError('The AI core is unavailable on this machine.', error.message);
        refreshStatus();
      } else if (error.code === 'AI_BUSY') {
        showError('The AI queue is full.', error.message);
      } else {
        showError("The run wasn't started.", error.message);
      }
    }
  }

  function poll(jobId) {
    clearPolling();
    pollTimer = setTimeout(async () => {
      if (Date.now() > pollDeadline) {
        setBusy(false);
        showError('The run is taking longer than expected and was left running on the backend.',
          'Check the backend log; the result will be recorded in its own database.');
        return;
      }
      try {
        const job = await request(`/api/ai/jobs/${encodeURIComponent(jobId)}`);
        if (job.status === 'queued') {
          setBusy(true, 'Queued — waiting for the worker to pick it up…');
          poll(jobId);
          return;
        }
        if (job.status === 'running') {
          setBusy(true, 'Running: external data, both neural networks, camera analysis and the reasoning layer…');
          poll(jobId);
          return;
        }
        setBusy(false);
        if (job.status === 'succeeded') renderResult(job);
        else {
          const missing = job.error?.missing_packages || [];
          showError(job.error?.message || 'The AI run failed.',
            missing.length ? `Missing on this machine: ${missing.join(', ')}` : 'See the backend log for the full traceback.');
        }
      } catch (error) {
        setBusy(false);
        showError('Lost track of the run.', error.message);
      }
    }, POLL_INTERVAL_MS);
  }

  async function warmup() {
    clearError();
    try {
      const result = await request('/api/ai/warmup', { method: 'POST' });
      const proxy = $('#ai-proxy-state');
      if (proxy) proxy.textContent = result.state === 'ready'
        ? 'Both networks were already trained in this process.'
        : 'Training started in the background. The first run will wait for it to finish.';
      const button = $('#ai-warm');
      if (button) {
        button.disabled = true;
        button.textContent = 'Training…';
        setTimeout(() => { button.disabled = false; button.textContent = 'Train models now'; refreshStatus(); }, 6000);
      }
    } catch (error) {
      if (error.code === 'AI_UNAVAILABLE') showError('Training cannot start.', error.message);
      else showError('Warm-up failed.', error.message);
    }
  }

  // ---- result rendering --------------------------------------------------

  function renderResult(job) {
    const result = job.result || {};
    const intel = result.structured_intelligence || {};
    const plant = intel.plant || {};
    const state = intel.site_state || {};
    const weather = intel.weather || {};
    const climate = intel.climate_normal || {};
    const soil = intel.soil || {};
    const irrigation = result.irrigation_decision || {};
    const fallback = result.ai_status === 'fallback';
    const container = $('#ai-results');
    if (!container) return;

    const banner = `
      <div class="ai-banner ${fallback ? 'ai-banner-warn' : 'ai-banner-ok'}">
        <span class="ai-banner-dot"></span>
        <div>
          <b>${fallback ? 'Rule-based fallback output' : `Reasoned by ${escapeHtml(result.farm_plan?._model_used || result.crop_health?._model_used || 'the local model')}`}</b>
          <p>${escapeHtml(result.message || '')}</p>
          <small>Run ${escapeHtml(job.job_id)} · ${escapeHtml(String(job.duration_seconds ?? '—'))}s · mode ${escapeHtml(job.params?.mode || '—')}</small>
        </div>
      </div>`;

    const irrigationCard = card('Irrigation decision', 'DETERMINISTIC CONTROLLER · FINAL AUTHORITY', `
      <div class="ai-decision ${irrigation.action === 'irrigate' ? 'ai-decision-go' : 'ai-decision-hold'}">
        <span>${irrigation.action === 'irrigate' ? '💧' : '✅'}</span>
        <div><b>${escapeHtml(String(irrigation.action || 'unknown').toUpperCase())}</b><p>${escapeHtml(irrigation.reason || '')}</p></div>
      </div>
      <p class="ai-muted">The controller never takes the reasoning layer's word for it, and is never
      driven by it. Recommendation and log entry only — no pump hardware is wired up.</p>`);

    const stateCard = card('Site state', 'AGRICULTURAL STATE NN · PROXY-TRAINED', `
      <div class="ai-stat-row">
        <div><span>SUITABILITY</span><b>${num(state.overall_suitability)}</b><i>/100</i></div>
        <div><span>WATER STRESS</span><b>${num(state.water_stress_index)}</b><i>/100</i></div>
        <div><span>YIELD POTENTIAL</span><b>${num(state.yield_potential)}</b><i>/100</i></div>
      </div>
      <p class="ai-muted">${escapeHtml(state.model_status || '')}</p>`);

    const plantCard = card('Plant condition', plant.capture_status === 'real_capture' ? 'CAMERA + RULE-BASED VISION' : 'RULE-BASED VISION · SIMULATED FRAME', `
      <p class="ai-muted">Capture: ${plant.capture_status === 'real_capture' ? 'live camera frame' : 'no camera available — a synthetic frame was analysed and this is flagged, not hidden'}</p>
      <div class="ai-stat-row">
        <div><span>GREEN</span><b>${num(plant.green_pct)}</b><i>%</i></div>
        <div><span>YELLOW</span><b>${num(plant.yellow_pct)}</b><i>%</i></div>
        <div><span>BROWN</span><b>${num(plant.brown_pct)}</b><i>%</i></div>
        <div><span>NECROTIC</span><b>${num(plant.necrotic_pct)}</b><i>%</i></div>
      </div>
      <div class="ai-kv">
        <span>Water stress suspicion<b>${num(plant.water_stress_suspicion, 2)}</b></span>
        <span>Heat stress suspicion<b>${num(plant.heat_stress_suspicion, 2)}</b></span>
        <span>Disease suspicion<b>${num(plant.disease_suspicion, 2)}</b></span>
        <span>Rule confidence<b>${num(plant.confidence, 2)}</b></span>
      </div>
      <p class="ai-muted">${escapeHtml(plant.source || '')} — these are threshold rules with an explicit uncertainty score, not a neural-network diagnosis.</p>`);

    const weatherCard = card('Weather, corrected', 'WEATHER CORRECTION NN + LIVE FORECAST', `
      <div class="ai-kv">
        <span>Open-Meteo temperature<b>${num(weather.corrected_temp_c)} °C</b></span>
        <span>Corrected humidity<b>${num(weather.corrected_humidity_pct)} %</b></span>
        <span>Rain probability<b>${weather.rain_probability === null || weather.rain_probability === undefined ? '—' : `${Math.round(Number(weather.rain_probability) * 100)}%`}</b></span>
        <span>Heat risk<b>${escapeHtml(orDash(weather.heat_risk))}</b></span>
        <span>Correction confidence<b>${num(weather.confidence, 2)}</b></span>
        <span>NASA POWER normal (T2M)<b>${num(climate.avg_temp_c_annual)} °C</b></span>
        <span>NASA POWER normal (rain)<b>${num(climate.avg_precip_mm_month_annual)} mm</b></span>
        <span>Source<b>${escapeHtml(orDash(weather.source))}</b></span>
      </div>
      <p class="ai-muted">${escapeHtml(weather.model_status || '')}</p>`);

    const soilCard = card('Soil background', 'SOILGRIDS ESTIMATE', `
      <div class="ai-kv">
        <span>pH<b>${num(soil.ph, 2)}</b></span>
        <span>Organic carbon<b>${num(soil.organic_carbon, 2)}</b></span>
        <span>Sand / silt / clay<b>${num(soil.sand_pct)} / ${num(soil.silt_pct)} / ${num(soil.clay_pct)} %</b></span>
        <span>Source<b>${escapeHtml(orDash(soil.source))}</b></span>
      </div>
      <p class="ai-muted">No NPK, NDVI or salinity layer is integrated — those arrive as explicit
      “unavailable” notes in the data-quality list below rather than as invented numbers.</p>`);

    const crops = intel.retrieved_crop_knowledge || [];
    const cropCard = card('Candidate crops', 'CROP KNOWLEDGE RETRIEVAL · CANDIDATES, NOT A DECISION', `
      ${crops.length ? `<div class="ai-table-wrap"><table><thead><tr><th>CROP</th><th>FIT</th><th>TEMP °C</th><th>WATER</th><th>SALINITY</th></tr></thead><tbody>
        ${crops.map((crop) => `<tr>
          <td>${escapeHtml(String(crop.crop || '').replaceAll('_', ' '))}</td>
          <td>${num(crop.candidate_fit_score, 2)}</td>
          <td>${escapeHtml((crop.temp_range_c || []).join('–'))}</td>
          <td>${escapeHtml(orDash(crop.water_requirement))}</td>
          <td>${escapeHtml(orDash(crop.salinity_tolerance))}</td>
        </tr>`).join('')}</tbody></table></div>` : '<p class="ai-muted">No candidates returned.</p>'}
      <p class="ai-muted">These are ranked candidates handed to the reasoning layer, not a final recommendation.</p>`);

    let planCard = '';
    if (result.farm_plan) {
      const plan = result.farm_plan;
      planCard = card('Farm plan', result.ai_status === 'fallback' ? 'FARM DECISION · FALLBACK OUTPUT' : 'FARM DECISION · MODEL OUTPUT', `
        <div class="ai-kv">
          <span>Growing system<b>${escapeHtml(orDash(plan.recommended_farming_system))}</b></span>
          <span>Confidence<b>${num(plan.confidence, 2)}</b></span>
          <span>Irrigation approach<b>${escapeHtml(orDash(plan.irrigation_strategy?.approach))}</b></span>
          <span>Frequency guidance<b>${escapeHtml(orDash(plan.irrigation_strategy?.frequency_guidance))}</b></span>
        </div>
        <h3 class="ai-subhead">Recommended crops</h3>${list((plan.recommended_crops || []).map((crop) => String(crop).replaceAll('_', ' ')))}
        <h3 class="ai-subhead">Crops to avoid</h3>${list(plan.crops_to_avoid)}
        <h3 class="ai-subhead">Reasoning</h3><p>${escapeHtml(plan.reasoning || '')}</p>
        <h3 class="ai-subhead">Growing strategy</h3>${list(plan.growing_strategy)}
        <h3 class="ai-subhead">Infrastructure</h3>${list(plan.infrastructure)}
        <h3 class="ai-subhead">Major risks</h3>${list(plan.major_risks)}
        <h3 class="ai-subhead">Economic viability</h3>
        <p>${escapeHtml(plan.economic_viability?.assessment || '')}</p>
        <p class="ai-muted">${escapeHtml(plan.economic_viability?.cost_estimate_notes || '')}</p>
        <h3 class="ai-subhead">Uncertainties</h3>${list(plan.uncertainties)}`);
    }

    let healthCard = '';
    if (result.crop_health) {
      const health = result.crop_health;
      healthCard = card('Crop health', result.ai_status === 'fallback' ? 'CROP HEALTH · FALLBACK OUTPUT' : 'CROP HEALTH · MODEL OUTPUT', `
        <div class="ai-stat-row">
          <div><span>STATUS</span><b class="ai-status-word">${escapeHtml(orDash(health.status_summary))}</b></div>
          <div><span>DISEASE</span><b>${escapeHtml(yesNo(health.disease_detected))}</b></div>
          <div><span>WATER STRESS</span><b>${escapeHtml(yesNo(health.water_stress_detected))}</b></div>
          <div><span>HARVEST READY</span><b>${escapeHtml(yesNo(health.harvest_ready))}</b></div>
        </div>
        <h3 class="ai-subhead">Preliminary assessment</h3><p>${escapeHtml(health.primary_diagnosis || '')}</p>
        <h3 class="ai-subhead">Recommended action</h3><p>${escapeHtml(health.recommended_action || '')}</p>
        <p class="ai-muted">Confidence ${num(health.confidence, 2)} — a visual/sensor-based assessment, not a lab-confirmed diagnosis.</p>
        <h3 class="ai-subhead">Uncertainties</h3>${list(health.uncertainties)}`);
    }

    const rate = intel.sensor_rate_of_change_check || {};
    const rateCard = card('Sensor sanity check', 'RATE-OF-CHANGE GUARD', `
      <p>${rate.suspicious
        ? '<b class="ai-warn-text">A jump larger than physically plausible was detected.</b>'
        : escapeHtml(rate.note || 'Change since the last logged reading is within plausible limits.')}</p>
      ${rate.flags && Object.keys(rate.flags).length ? `<div class="ai-kv">${Object.entries(rate.flags).map(([key, flag]) => `
        <span>${escapeHtml(key.replaceAll('_', ' '))}<b class="${flag.flagged ? 'ai-warn-text' : ''}">Δ${num(flag.delta, 2)} (max ${num(flag.max_plausible_delta, 2)})</b></span>`).join('')}</div>` : ''}
      ${rate.elapsed_seconds ? `<p class="ai-muted">Compared against the previous reading logged ${num(rate.elapsed_seconds)}s earlier.</p>` : ''}`);

    const notes = intel.data_quality_notes || [];
    const notesCard = card('What this run could not get', 'DATA-QUALITY DISCLOSURE', `
      ${list(notes)}
      <p class="ai-muted">These gaps are stated rather than filled in with estimates, and the reasoning
      layer is instructed to lower its confidence where they matter.</p>`);

    const time = intel.time_context || {};
    const contextCard = card('Run context', 'INPUTS AND TIME', `
      <div class="ai-kv">
        <span>Coordinates<b>${num(job.params?.latitude, 4)}, ${num(job.params?.longitude, 4)}</b></span>
        <span>Region filter<b>${escapeHtml(orDash(job.params?.region))}</b></span>
        <span>Season<b>${escapeHtml(orDash(time.season))} · day ${escapeHtml(orDash(time.day_of_year))}</b></span>
        <span>Budget<b>${num(job.params?.budget, 0)}</b></span>
        <span>Soil moisture in<b>${num(job.params?.soil_moisture_pct)} %</b></span>
        <span>Air temperature in<b>${num(job.params?.air_temp_c)} °C</b></span>
        <span>Humidity in<b>${num(job.params?.air_humidity_pct)} %</b></span>
        <span>LDR reading in<b>${num(job.params?.ldr_pct)} %</b></span>
      </div>`);

    container.innerHTML = `${banner}<div class="ai-result-grid">
      ${irrigationCard}${stateCard}${healthCard}${planCard}${plantCard}${weatherCard}${soilCard}${cropCard}${rateCard}${contextCard}${notesCard}
    </div>`;
    container.hidden = false;
    container.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ---- wiring ------------------------------------------------------------

  form.addEventListener('submit', startRun);
  $('#ai-warm')?.addEventListener('click', warmup);
  $('#ai-refresh-status')?.addEventListener('click', refreshStatus);
  form.addEventListener('reset', () => { clearError(); const results = $('#ai-results'); if (results) results.hidden = true; });

  $('#ai-use-location')?.addEventListener('click', () => {
    const status = $('#ai-location-status');
    if (!navigator.geolocation) {
      if (status) status.textContent = 'This browser does not offer geolocation.';
      return;
    }
    if (status) status.textContent = 'Requesting this device’s location…';
    navigator.geolocation.getCurrentPosition((position) => {
      form.elements.latitude.value = position.coords.latitude.toFixed(4);
      form.elements.longitude.value = position.coords.longitude.toFixed(4);
      if (status) status.textContent = `Using ${position.coords.latitude.toFixed(4)}, ${position.coords.longitude.toFixed(4)}.`;
    }, () => {
      if (status) status.textContent = 'Location was not shared. Enter coordinates manually.';
    }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 });
  });

  refreshStatus();
})();
