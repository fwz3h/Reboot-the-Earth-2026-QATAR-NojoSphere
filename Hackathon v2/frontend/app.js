(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const FILE_ORIGIN = location.protocol === 'file:';
  const BACKEND_ORIGIN = 'http://localhost:5000';
  const API = FILE_ORIGIN ? BACKEND_ORIGIN : '';
  const history = [];
  const series = { soil_moisture: [], temperature: [], humidity: [], water_level: [] };
  const state = { range: '24h', device: '', socket: null, reconnectDelay: 1000, reconnectTimer: null, simulation: false, devices: [], alerts: [], insightsByDevice: new Map(), toastTimer: null, dialogTrigger: null, demoFilter: null };
  const RANGE_LABELS = { '1h': 'Last hour', '6h': 'Last 6 hours', '24h': 'Last 24 hours', '7d': 'Last 7 days', '30d': 'Last 30 days' };
  const METRICS = [
    { key: 'soil_moisture', value: '#soil-value', trend: '#soil-trend', condition: '#soil-condition', spark: '#soil-spark', unit: '%', color: '#a5e66b' },
    { key: 'temperature', value: '#temp-value', trend: '#temp-trend', condition: '#temp-condition', spark: '#temp-spark', unit: '°C', color: '#e9c46a' },
    { key: 'humidity', value: '#humidity-value', trend: '#humidity-trend', progress: '#humidity-progress', condition: '#humidity-condition', spark: '#humidity-spark', unit: '%', color: '#79bed0' },
    { key: 'water_level', value: '#water-value', trend: '#water-trend', progress: '#water-progress', condition: '#water-condition', spark: '#water-spark', unit: '%', color: '#72d7ae' },
  ];

  function showToast(message) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.classList.add('visible');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => toast.classList.remove('visible'), 2600);
  }

  const METRIC_INFO = {
    soil_moisture: { label: 'Soil moisture', unit: '%', target: '30–70% screening range', guidance: 'Values below 30% trigger the configured low-soil alert; check crop and soil-specific moisture targets.' },
    temperature: { label: 'Air temperature', unit: '°C', target: '10–35°C screening range', guidance: 'Values outside the configured range trigger temperature alerts.' },
    humidity: { label: 'Air humidity', unit: '%', target: '35–80% reference range', guidance: 'Humidity is a contextual reading; use crop-specific humidity guidance.' },
    water_level: { label: 'Water reserve', unit: '%', target: '20–100% reference range', guidance: 'Values below 20% trigger the configured low-water alert.' },
  };

  function openDialog(title, eyebrow, content, onReady) {
    const backdrop = $('#action-dialog');
    $('#dialog-title').textContent = title;
    $('#dialog-eyebrow').textContent = eyebrow;
    $('#dialog-content').innerHTML = content;
    state.dialogTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (state.dialogTrigger?.hasAttribute('aria-haspopup')) state.dialogTrigger.setAttribute('aria-expanded', 'true');
    backdrop.hidden = false;
    document.body.classList.add('dialog-open');
    backdrop.querySelector('.action-dialog').focus();
    if (onReady) onReady();
  }

  function closeDialog() {
    const backdrop = $('#action-dialog');
    if (backdrop.hidden) return;
    backdrop.hidden = true;
    document.body.classList.remove('dialog-open');
    const trigger = state.dialogTrigger;
    state.dialogTrigger = null;
    if (trigger?.hasAttribute('aria-haspopup')) trigger.setAttribute('aria-expanded', 'false');
    if (trigger?.isConnected) trigger.focus({ preventScroll: true });
  }

  function showMetricDetails(key, trigger) {
    const info = METRIC_INFO[key];
    if (!info) return;
    const readings = visibleReadings().filter((reading) => Number.isFinite(Number(reading[key])));
    const values = readings.map((reading) => Number(reading[key]));
    const current = values.at(-1);
    const average = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    const reading = readings.at(-1);
    const stat = (value) => value == null ? '—' : `${value.toFixed(1)} ${info.unit}`;
    const subtitle = reading ? `${escapeHtml(reading.device)} · ${relativeTime(reading.timestamp)}` : 'No readings recorded for this field yet';
    const currentValue = current == null ? '—' : `${current.toFixed(1)} ${info.unit}`;
    openDialog(info.label, 'SENSOR DETAILS', `
      <p class="dialog-subtitle">${subtitle}</p>
      <div class="detail-hero"><span>Latest value</span><strong>${currentValue}</strong><small>${reading ? `Received ${formatTime(reading.timestamp)}` : 'Connect a sensor or run the simulation to begin.'}</small></div>
      <div class="detail-stats"><div><span>RECENT AVERAGE</span><b>${stat(average)}</b></div><div><span>RECENT LOW</span><b>${stat(values.length ? Math.min(...values) : null)}</b></div><div><span>RECENT HIGH</span><b>${stat(values.length ? Math.max(...values) : null)}</b></div><div><span>SAMPLES SHOWN</span><b>${values.length}</b></div></div>
      <div class="detail-guidance"><b>Reference</b><span>${escapeHtml(info.target)}</span><p>${escapeHtml(info.guidance)}</p></div>
      <button type="button" class="dialog-action" data-action="view-history">View sensor trend <span>↗</span></button>
    `, () => {
      $('#dialog-content [data-action="view-history"]').addEventListener('click', () => {
        closeDialog();
        $('#monitoring').scrollIntoView({ behavior: 'smooth', block: 'start' });
        trigger?.focus({ preventScroll: true });
      });
    });
  }

  function showDeviceDetails() {
    const device = state.devices.find((item) => item.device === $('#active-device').textContent) || state.devices[0];
    const reading = device?.latest_reading || visibleReadings().at(-1);
    const connected = device?.status === 'ONLINE';
    const signal = connected ? 'Connected' : device ? 'Not responding' : 'No device found';
    const details = reading ? `
      <div class="detail-stats device-detail-stats">
        <div><span>CONNECTION</span><b>${signal}</b></div>
        <div><span>LAST READING</span><b>${relativeTime(reading.timestamp)}</b></div>
        <div><span>SOIL MOISTURE</span><b>${Number.isFinite(Number(reading.soil_moisture)) ? `${Number(reading.soil_moisture).toFixed(1)}%` : '—'}</b></div>
        <div><span>TEMPERATURE</span><b>${Number.isFinite(Number(reading.temperature)) ? `${Number(reading.temperature).toFixed(1)}°C` : '—'}</b></div>
        <div><span>AIR HUMIDITY</span><b>${Number.isFinite(Number(reading.humidity)) ? `${Number(reading.humidity).toFixed(1)}%` : 'Not reported'}</b></div>
        <div><span>WATER LEVEL</span><b>${Number.isFinite(Number(reading.water_level)) ? `${Number(reading.water_level).toFixed(1)}%` : 'Not reported'}</b></div>
      </div>
      <p class="dialog-footnote">Sensor ${escapeHtml(device?.device || reading.device)} · readings arrive through the live field connection.</p>
    ` : '<p class="dialog-subtitle">No sensor readings yet. Check that the ESP32 is powered and connected, or try demo readings.</p>';
    openDialog('Field sensor', 'ESP32 CONNECTION', `${details}<button type="button" class="dialog-action" data-action="view-devices">View all devices <span>↗</span></button>`);
  }

  function showWorkspaceDialog() {
    const name = localStorage.getItem('agrisense-workspace-name') || 'North Field';
    openDialog('Farm workspace', 'WORKSPACE SETTINGS', `
      <p class="dialog-subtitle">This local label helps organize your dashboard view. It does not change sensor IDs or backend data.</p>
      <form id="workspace-form" class="dialog-form"><label for="workspace-name">Workspace label</label><input id="workspace-name" maxlength="60" required value="${escapeHtml(name)}"><button class="button button-primary" type="submit">Save workspace name</button></form>
      <p class="dialog-footnote">Saved in this browser only.</p>
    `, () => {
      $('#workspace-form').addEventListener('submit', (event) => {
        event.preventDefault();
        const value = $('#workspace-name').value.trim();
        if (!value) return;
        localStorage.setItem('agrisense-workspace-name', value);
        $('.workspace b').textContent = value;
        $('.workspace-icon').textContent = value.trim().charAt(0).toUpperCase();
        $('#hero-field-name').textContent = value;
        $('#hero-field-label').textContent = value;
        closeDialog();
        showToast('Workspace label saved');
      });
      $('#workspace-name').focus();
      $('#workspace-name').select();
    });
  }

  function exportReadingsCsv() {
    const readings = visibleReadings();
    if (!readings.length) { showToast('There are no readings to export yet'); return; }
    const columns = ['device', 'timestamp', 'soil_moisture', 'temperature', 'humidity', 'soil_ph', 'water_level', 'light', 'pressure', 'battery'];
    const csvValue = (value) => `"${String(value ?? '').replaceAll('"', '""')}"`;
    const csv = [columns.join(','), ...readings.map((reading) => columns.map((key) => csvValue(reading[key])).join(','))].join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `agrisense-readings-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast(`Exported ${readings.length} readings`);
  }

  function showAccountActions() {
    openDialog('Dashboard actions', 'QUICK ACTIONS', `
      <p class="dialog-subtitle">Shortcuts for this AgriSense session.</p>
      <div class="dialog-actions-list">
        <button type="button" class="dialog-action" data-action="suitability">Open site suitability <span>↗</span></button>
        <button type="button" class="dialog-action" data-action="devices-refresh">Refresh device status <span>↻</span></button>
        <button type="button" class="dialog-action" data-action="export">Export visible readings as CSV <span>↓</span></button>
      </div>
      <p class="dialog-footnote">CSV export uses the currently loaded sensor history and selected device filter.</p>
    `);
  }

  function formatTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function relativeTime(value) {
    if (!value) return 'No readings yet';
    const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
    if (!Number.isFinite(seconds)) return '—';
    if (seconds < 5) return 'Just now';
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  }

  async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
    let body;
    try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) throw new Error(body.message || body.error || `Request failed (${response.status})`);
    return body;
  }

  function visibleReadings() {
    return state.device ? history.filter((reading) => reading.device === state.device) : history;
  }

  function updateTrend(metric, readings) {
    const values = readings.filter((reading) => Number.isFinite(Number(reading[metric.key]))).map((reading) => Number(reading[metric.key]));
    const node = $(metric.trend);
    if (!node) return;
    node.classList.remove('positive', 'negative');
    if (values.length < 2) { node.textContent = values.length ? 'First reading' : 'Awaiting data'; return; }
    const delta = values[values.length - 1] - values[values.length - 2];
    if (Math.abs(delta) < 0.05) node.textContent = 'Stable';
    else {
      node.textContent = `${delta > 0 ? '↗' : '↘'} ${Math.abs(delta).toFixed(1)}${metric.unit}`;
      node.classList.add(metric.key === 'soil_moisture' ? (delta > 0 ? 'positive' : 'negative') : (delta < 0 ? 'positive' : 'negative'));
    }
  }

  function updateSparkline(metric, readings) {
    const node = $(metric.spark);
    if (!node) return;
    const values = readings.slice(-12).map((reading) => Number(reading[metric.key])).filter(Number.isFinite);
    if (values.length < 2) { node.innerHTML = ''; return; }
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const points = values.map((value, index) => `${(index / (values.length - 1)) * 120},${31 - ((value - min) / span) * 25}`).join(' ');
    node.innerHTML = `<path d="M${points.replaceAll(' ', ' L')}"/>`;
  }

  function updateMetric(metric, reading, readings, animate = false) {
    const value = reading[metric.key];
    const valueNode = $(metric.value);
    if (value === undefined || value === null || !Number.isFinite(Number(value))) {
      valueNode.textContent = '—';
      if (metric.progress) $(metric.progress).style.width = '0%';
      if (metric.condition) $(metric.condition).textContent = 'No sensor data available';
      updateTrend(metric, readings);
      updateSparkline(metric, readings);
      return;
    }
    const number = Number(value);
    const nextText = number.toFixed(1);
    const changed = valueNode.textContent !== nextText;
    valueNode.textContent = nextText;
    if (animate && changed && !matchMedia('(prefers-reduced-motion: reduce)').matches && valueNode.animate) {
      valueNode.animate([
        { transform: 'translateY(3px) scale(.96)', opacity: .48, filter: 'blur(2px)' },
        { transform: 'translateY(0) scale(1)', opacity: 1, filter: 'blur(0)' },
      ], { duration: 360, easing: 'cubic-bezier(.2,.8,.2,1)' });
    }
    if (metric.progress) $(metric.progress).style.width = `${Math.max(0, Math.min(100, number))}%`;
    if (metric.condition) {
      if (metric.key === 'soil_moisture') $(metric.condition).textContent = number < 30 ? 'Below optimal · irrigation may help' : number > 75 ? 'High soil saturation' : 'Within a healthy growing range';
      else if (metric.key === 'temperature') $(metric.condition).textContent = number > 35 ? 'Above configured field threshold' : number < 10 ? 'Below configured field threshold' : 'Within a comfortable field range';
      else if (metric.key === 'humidity') $(metric.condition).textContent = number < 35 ? 'Air is relatively dry' : number > 80 ? 'Elevated relative humidity' : 'Balanced relative humidity';
      else $(metric.condition).textContent = number < 20 ? 'Low reserve · check water supply' : 'Water reserve available';
    }
    updateTrend(metric, readings);
    updateSparkline(metric, readings);
  }

  function renderMetrics(animate = false) {
    const readings = visibleReadings();
    const reading = readings[readings.length - 1];
    if (!reading) {
      METRICS.forEach((metric) => updateMetric(metric, {}, readings));
      $('#hero-soil-state').textContent = 'Waiting for readings';
      $('#hero-field-name').textContent = 'North Field';
      $('#hero-field-label').textContent = 'North Field';
      $('#hero-reading-device').textContent = 'your field sensor';
      $('#hero-reading-device-id').textContent = '';
      $('#network-latest-label').textContent = state.devices.length ? 'No readings received yet' : 'No field sensor connected';
      $('#insight-status').textContent = 'WAITING FOR FIELD DATA';
      return;
    }
    METRICS.forEach((metric) => updateMetric(metric, reading, readings, animate));
    const soil = Number(reading.soil_moisture);
    const ring = $('#soil-ring-progress');
    const ringCircumference = 2 * Math.PI * 48;
    ring.style.strokeDashoffset = String(ringCircumference * (1 - Math.max(0, Math.min(100, soil)) / 100));
    ring.style.stroke = soil < 30 || soil > 75 ? '#d2b17b' : '#b7d78f';
    $('#hero-soil-state').textContent = soil < 30 ? 'Low · check irrigation' : soil > 75 ? 'Very wet' : 'Optimal';
    const selectedDevice = state.device ? state.devices.find((item) => item.device === state.device) : null;
    const farmName = localStorage.getItem('agrisense-workspace-name') || 'North Field';
    $('#hero-field-name').textContent = farmName;
    $('#hero-field-label').textContent = farmName;
    $('#hero-reading-device').textContent = selectedDevice?.device || reading.device || 'your field sensor';
    $('#hero-reading-device-id').textContent = state.device ? 'FILTERED SENSOR' : '';
    $('#network-latest-label').textContent = `${relativeTime(reading.timestamp)} · latest field reading`;
    $('#insight-status').textContent = soil < 30 ? 'IRRIGATION MAY BE NEEDED' : 'NO ACTION NEEDED';
    $('#insight-status').classList.toggle('attention', soil < 30 || soil > 75);
    $('#active-device').textContent = reading.device || '—';
    $('#last-reading').textContent = `${formatTime(reading.timestamp)} · ${relativeTime(reading.timestamp)}`;
    $('#hero-last-reading').textContent = `${formatTime(reading.timestamp)} · ${relativeTime(reading.timestamp)}`;
    const temp = Number(reading.temperature);
    $('#temp-marker').style.left = `${Math.max(0, Math.min(100, ((temp - 10) / 25) * 100))}%`;
  }

  function chartCoordinates(values, min, max, yTop = 34, yBottom = 234) {
    const span = max - min || 1;
    return values.map((value, index) => ({ x: 48 + (index / Math.max(values.length - 1, 1)) * 742, y: yBottom - ((value - min) / span) * (yBottom - yTop) }));
  }

  function renderChart() {
    const readings = visibleReadings().slice(-40);
    const chartEmpty = $('#chart-empty');
    $('#chart-count').textContent = readings.length ? `${readings.length} readings` : 'No readings yet';
    $('#chart-range-label').textContent = RANGE_LABELS[state.range];
    chartEmpty.classList.toggle('hidden', readings.length > 0);
    const seriesNode = $('#chart-series');
    const labelNode = $('#chart-x-labels');
    if (!readings.length) { seriesNode.innerHTML = ''; labelNode.innerHTML = ''; return; }

    const soilReadings = readings.filter((reading) => Number.isFinite(Number(reading.soil_moisture)));
    const tempReadings = readings.filter((reading) => Number.isFinite(Number(reading.temperature)));
    const soilPoints = chartCoordinates(soilReadings.map((reading) => Number(reading.soil_moisture)), 0, 100);
    const temps = tempReadings.map((reading) => Number(reading.temperature));
    const tempMin = Math.min(0, ...temps), tempMax = Math.max(40, ...temps);
    const tempPoints = chartCoordinates(temps, tempMin, tempMax, 34, 234);
    const curve = (points) => {
      if (!points.length) return '';
      if (points.length === 1) return `M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`;
      let path = `M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`;
      for (let i = 0; i < points.length - 1; i += 1) {
        const previous = points[Math.max(0, i - 1)];
        const current = points[i];
        const next = points[i + 1];
        const after = points[Math.min(points.length - 1, i + 2)];
        const c1x = current.x + (next.x - previous.x) / 6;
        const c1y = current.y + (next.y - previous.y) / 6;
        const c2x = next.x - (after.x - current.x) / 6;
        const c2y = next.y - (after.y - current.y) / 6;
        path += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${next.x.toFixed(1)},${next.y.toFixed(1)}`;
      }
      return path;
    };
    // Hold at most one outgoing layer. Every clone carries the drop-shadowed series line,
    // so a stream of readings repainting faster than the 330ms fade would otherwise pile
    // up dozens of animated filter layers and drag the whole page down.
    const releaseExitLayer = () => {
      if (!chartExitLayer) return;
      chartExitLayer.remove();
      chartExitLayer = null;
    };
    releaseExitLayer();
    const previousSeries = seriesNode.childElementCount ? seriesNode.cloneNode(true) : null;
    if (previousSeries) {
      previousSeries.removeAttribute('id');
      previousSeries.classList.add('chart-series-exit');
      previousSeries.setAttribute('aria-hidden', 'true');
      seriesNode.parentNode.insertBefore(previousSeries, seriesNode);
      chartExitLayer = previousSeries;
    }
    const soilPath = curve(soilPoints);
    const area = soilPoints.length ? `${soilPath} L${soilPoints.at(-1).x.toFixed(1)},234 L${soilPoints[0].x.toFixed(1)},234 Z` : '';
    const latestPoint = soilPoints.at(-1);
    const marks = soilPoints.filter((_, index) => index === soilPoints.length - 1 || (soilPoints.length > 6 && index % Math.ceil(soilPoints.length / 6) === 0))
      .map((point) => `<circle class="series-point" cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="3"/>`).join('');
    seriesNode.innerHTML = `${area ? `<path class="series-area" d="${area}"/>` : ''}${soilPath ? `<path class="series-line" d="${soilPath}"/>` : ''}${tempPoints.length ? `<path class="series-temp" d="${curve(tempPoints)}"/>` : ''}${marks}${latestPoint ? `<circle class="series-live-point" cx="${latestPoint.x.toFixed(1)}" cy="${latestPoint.y.toFixed(1)}" r="4"/>` : ''}`;
    if (previousSeries && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const retire = () => { if (chartExitLayer === previousSeries) chartExitLayer = null; previousSeries.remove(); };
      previousSeries.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 330, easing: 'ease-out' }).finished.then(retire).catch(retire);
      seriesNode.animate([{ opacity: .22 }, { opacity: 1 }], { duration: 420, easing: 'cubic-bezier(.2,.75,.25,1)' });
    } else if (previousSeries) {
      if (chartExitLayer === previousSeries) chartExitLayer = null;
      previousSeries.remove();
    }
    const labelIndexes = [...new Set([0, Math.floor((readings.length - 1) / 3), Math.floor(2 * (readings.length - 1) / 3), readings.length - 1])];
    labelNode.innerHTML = labelIndexes.map((index) => `<text x="${48 + index / Math.max(readings.length - 1, 1) * 742}" y="249">${formatTime(readings[index].timestamp)}</text>`).join('');
    const chart = $('#trend-chart');
    chart.setAttribute('aria-label', `Soil moisture and temperature trends; latest soil moisture ${Number(soilReadings.at(-1)?.soil_moisture).toFixed(1)} percent${Number.isFinite(Number(tempReadings.at(-1)?.temperature)) ? ` and temperature ${Number(tempReadings.at(-1).temperature).toFixed(1)} degrees Celsius` : ''}`);
  }

  function renderDevices() {
    const devices = state.devices;
    const active = devices.find((item) => item.device === $('#active-device').textContent);
    $('#hero-device-label').textContent = active?.type ? `${String(active.type).toUpperCase()} SENSOR` : 'ESP32 SENSOR';
    $('#device-count').textContent = String(devices.length);
    $('#coverage-devices').textContent = String(devices.length);
    $('#coverage-online').textContent = String(devices.filter((item) => item.status === 'ONLINE').length);
    $('#coverage-readings').textContent = String(history.length);
    $('#coverage-summary').textContent = devices.length ? `${devices.length} sensor${devices.length === 1 ? '' : 's'} reporting` : 'Listening for field devices';
    if (!history.length) $('#network-latest-label').textContent = devices.length ? `${devices.filter((item) => item.status === 'ONLINE').length} online · waiting for data` : 'No field sensor connected';
    const select = $('#device-select');
    const current = state.device;
    select.innerHTML = '<option value="">All devices</option>' + devices.map((device) => `<option value="${escapeHtml(device.device)}">${escapeHtml(device.device)}</option>`).join('');
    select.value = current;
    const body = $('#device-rows');
    if (!devices.length) { body.innerHTML = '<tr><td colspan="4" class="table-empty">No sensor devices detected yet.</td></tr>'; return; }
    body.innerHTML = devices.map((device) => {
      const latest = device.latest_reading || {};
      const isOnline = device.status === 'ONLINE';
      return `<tr><td><div class="device-name-cell"><span class="device-chip">⌁</span><span><span class="device-id">${escapeHtml(device.device)}</span><small class="device-subtitle">ESP32 field sensor</small></span></div></td><td><span class="status-pill ${isOnline ? 'online' : 'offline'}"><i></i>${isOnline ? 'Online' : 'Offline'}</span></td><td>${escapeHtml(relativeTime(device.last_seen))}</td><td><span class="cell-value">${latest.soil_moisture == null ? '—' : `${Number(latest.soil_moisture).toFixed(1)}%`}</span></td></tr>`;
    }).join('');
  }

  function renderAlerts() {
    const alerts = state.alerts;
    $('#alert-count').textContent = String(alerts.length);
    $('#alerts-total').textContent = `${alerts.length} ACTIVE`;
    $('#notification-dot').classList.toggle('visible', alerts.length > 0);
    const list = $('#alert-list');
    if (!alerts.length) {
      list.innerHTML = '<div class="alert-empty"><span>✓</span><div><b>All clear for now</b><small>New field alerts will appear here.</small></div></div>';
      return;
    }
    list.innerHTML = alerts.slice(0, 6).map((alert) => `<div class="alert-item"><i class="alert-marker ${alert.severity === 'critical' ? 'critical' : ''}"></i><div><b>${escapeHtml(alert.type.replaceAll('_', ' '))} · ${escapeHtml(alert.device)}</b><p>${escapeHtml(alert.message)}</p></div><time>${escapeHtml(relativeTime(alert.timestamp))}</time></div>`).join('');
  }

  function renderInsights() {
    const groups = state.device
      ? [state.insightsByDevice.get(state.device) || []]
      : [...state.insightsByDevice.values()];
    const items = groups.flat();
    if (!items.length) {
      $('#insight-label').textContent = state.device ? 'DEVICE INSIGHT' : 'MONITORING INSIGHT';
      $('#insight-message').textContent = state.device
        ? `Waiting for readings from ${state.device} before generating a field insight.`
        : 'Insights will appear when sensor readings are available.';
      return;
    }
    const preferred = items.find((item) => item.severity === 'warning') || items[0];
      const deviceLabel = preferred.device && !state.device ? ` · ${preferred.device}` : '';
      const insightType = String(preferred.type || 'FIELD INSIGHT').replaceAll('_', ' ');
      $('#insight-label').textContent = `${insightType}${deviceLabel}`;
      $('#insight-message').textContent = preferred.message || 'Monitoring field conditions.';
      const needsAttention = preferred.severity === 'warning' || /LOW_|HIGH_|IRRIGATION|RISING/.test(preferred.type || '');
      $('#insight-status').textContent = needsAttention ? 'CHECK THIS FIELD' : 'NO ACTION NEEDED';
      $('#insight-status').classList.toggle('attention', needsAttention);
  }

  function setInsights(items, device = null) {
    if (device) {
      state.insightsByDevice.set(device, Array.isArray(items) ? items : []);
    } else {
      state.insightsByDevice.clear();
      for (const insight of Array.isArray(items) ? items : []) {
        if (!insight.device) continue;
        const existing = state.insightsByDevice.get(insight.device) || [];
        existing.push(insight);
        state.insightsByDevice.set(insight.device, existing);
      }
    }
    renderInsights();
  }

  function updateConnection(connected, message) {
    const pulse = $('#connection-pulse');
    pulse.classList.toggle('online', connected);
    pulse.classList.toggle('offline', !connected && message.toLowerCase().includes('offline'));
    $('#connection-label').textContent = connected ? 'Field network connected' : message;
    $('#connection-detail').textContent = connected ? 'Receiving updates from the AgriSense backend' : 'Reconnecting automatically';
    const liveLabel = $('#live-badge-label');
    liveLabel.textContent = connected ? 'LIVE' : 'OFFLINE';
    liveLabel.parentElement.classList.toggle('offline', !connected);
    $('#hero-network').classList.toggle('disconnected', !connected);
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }

  // Live readings arrive in bursts: several devices ticking together, a fleet
  // reconnecting, a sensor flushing the buffer it collected while offline. Coalescing
  // to one repaint per animation frame turns such a burst into a single chart rebuild
  // instead of one full rebuild per message.
  const HISTORY_LIMIT = 1000;
  const SIDE_DATA_INTERVAL_MS = 1500;
  const pendingRender = { devices: false, metrics: false, chart: false, frame: 0 };
  // rAF is the smooth path, but it does not fire while the page is hidden and a stalled
  // compositor can starve it entirely. This watchdog guarantees readings still reach the
  // screen on time instead of the dashboard freezing on stale numbers.
  const RENDER_WATCHDOG_MS = 250;
  let renderWatchdog = 0;
  let chartExitLayer = null;
  let sideDataTimer = 0;

  // Cached numeric timestamp, so ordering never allocates a Date per comparison.
  function readingTime(reading) {
    let value = reading.__time;
    if (typeof value !== 'number') {
      value = Date.parse(reading.timestamp) || 0;
      Object.defineProperty(reading, '__time', { value, enumerable: false, writable: true, configurable: true });
    }
    return value;
  }

  // Inserts in timestamp order. Readings normally arrive in order, so the backwards
  // scan below walks zero steps in the common case; a device replaying an offline
  // buffer still lands in the right slot instead of costing a full sort.
  function insertReading(reading) {
    const time = readingTime(reading);
    let index = history.length;
    while (index > 0 && readingTime(history[index - 1]) > time) index -= 1;
    // `history` stays ordered, so an already-stored copy of this reading shares its
    // timestamp and therefore sits directly before the insertion point.
    for (let i = index - 1; i >= 0 && readingTime(history[i]) === time; i -= 1) {
      if (history[i].device === reading.device && history[i].timestamp === reading.timestamp) {
        history[i] = reading;
        return;
      }
    }
    history.splice(index, 0, reading);
    if (history.length > HISTORY_LIMIT) history.splice(0, history.length - HISTORY_LIMIT);
  }

  function flushPendingRender() {
    if (pendingRender.frame) { cancelAnimationFrame(pendingRender.frame); pendingRender.frame = 0; }
    clearTimeout(renderWatchdog);
    renderWatchdog = 0;
    if (pendingRender.devices) { pendingRender.devices = false; renderDevices(); }
    if (pendingRender.metrics) { pendingRender.metrics = false; renderMetrics(true); }
    if (pendingRender.chart) { pendingRender.chart = false; renderChart(); }
  }

  function scheduleRender(flags) {
    Object.assign(pendingRender, flags);
    // Schedule, never reschedule: the watchdog is a latency ceiling (no reading waits
    // longer than RENDER_WATCHDOG_MS to appear) while animation frames keep a busy
    // stream coalesced to one repaint per frame.
    if (!pendingRender.frame) pendingRender.frame = requestAnimationFrame(flushPendingRender);
    if (!renderWatchdog) renderWatchdog = setTimeout(flushPendingRender, RENDER_WATCHDOG_MS);
  }

  // Alerts and insights are aggregate views rather than per-reading data, so fetching
  // them on every packet only queued redundant round-trips behind the reading stream.
  function refreshSideData() {
    // Skip when one is already pending. Resetting here instead would mean a continuous
    // stream of readings starves the refresh forever, which is how this panel ended up
    // running its own request loop in the first place.
    if (sideDataTimer) return;
    sideDataTimer = setTimeout(() => {
      sideDataTimer = 0;
      request('/api/alerts?active=true&limit=30').then((body) => { state.alerts = body.alerts || []; renderAlerts(); }).catch(() => {});
      request(`/api/insights${state.device ? `?device=${encodeURIComponent(state.device)}` : ''}`).then((body) => setInsights(body.insights, body.device)).catch(() => {});
    }, SIDE_DATA_INTERVAL_MS);
  }

  function upsertReading(reading) {
    if (!reading || !reading.device || !Number.isFinite(Number(reading.soil_moisture)) || !Number.isFinite(Number(reading.temperature))) return;
    reading.timestamp = reading.timestamp || new Date().toISOString();
    insertReading(reading);
    scheduleRender({ metrics: true, chart: true, devices: true });
    refreshSideData();
  }

  function handleMessage(event) {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === 'connection_status') updateConnection(message.status === 'connected', 'Disconnected');
    else if (message.type === 'sensor_reading') upsertReading(message.data);
    else if (message.type === 'device_status') {
      const device = state.devices.find((item) => item.device === message.device);
      if (device) device.status = message.status;
      if (message.status === 'OFFLINE') showToast(`${message.device} is offline`);
      renderDevices();
    } else if (message.type === 'alert' && message.data) {
      if (!state.alerts.some((alert) => alert.type === message.data.type && alert.device === message.data.device)) state.alerts.unshift(message.data);
      renderAlerts();
    } else if (message.type === 'ai_insights') {
      // The socket already carries the insight payload, so re-fetching it over HTTP here
      // meant one redundant round-trip (and a second insights repaint) per reading. When
      // no device filter is active the pushed payload only covers one device, so ask for
      // the aggregated view on the shared debounce instead of on every message.
      setInsights(message.data, message.device);
      if (!state.device) refreshSideData();
    }
  }

  function connectWebSocket() {
    clearTimeout(state.reconnectTimer);
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const websocketBase = FILE_ORIGIN ? 'ws://localhost:5000' : `${scheme}//${location.host}`;
    const socket = new WebSocket(`${websocketBase}/ws`);
    state.socket = socket;
    socket.addEventListener('open', () => { state.reconnectDelay = 1000; updateConnection(true, 'Connected'); });
    socket.addEventListener('message', handleMessage);
    socket.addEventListener('error', () => socket.close());
    socket.addEventListener('close', () => {
      updateConnection(false, 'Field network offline');
      state.reconnectTimer = setTimeout(connectWebSocket, state.reconnectDelay);
      state.reconnectDelay = Math.min(15000, state.reconnectDelay * 1.7);
    });
  }

  async function loadInitialData() {
    try {
      const [deviceBody, alertBody] = await Promise.all([
        request('/api/devices'), request('/api/alerts?active=true&limit=30'),
      ]);
      state.devices = deviceBody.devices || [];
      state.alerts = alertBody.alerts || [];
      if (state.device && !state.devices.some((device) => device.device === state.device)) state.device = '';
      const query = new URLSearchParams({ range: state.range, limit: '300' });
      if (state.device) query.set('device', state.device);
      const readingBody = await request(`/api/readings?${query}`);
      history.length = 0;
      history.push(...(readingBody.readings || []).reverse());
      renderDevices(); renderAlerts(); renderMetrics(); renderChart();
      const insightQuery = state.device ? `?device=${encodeURIComponent(state.device)}` : '';
      const insightBody = await request(`/api/insights${insightQuery}`);
      setInsights(insightBody.insights, insightBody.device);
      $('#device-select').value = state.device;
      const simulation = await request('/api/health');
      setSimulationButton(Boolean(simulation.simulation && simulation.simulation.running));
    } catch (error) {
      updateConnection(false, 'Backend unavailable');
      showToast(`Could not load field data: ${error.message}`);
    }
  }

  function setSimulationButton(running) {
    state.simulation = running;
    const button = $('#simulate-button');
    button.classList.toggle('running', running);
    $('#simulate-label').textContent = running ? 'Stop demo readings' : 'Try demo readings';
    $('#simulate-button').setAttribute('aria-pressed', String(running));
    $('.button-symbol').textContent = running ? '■' : '▶';
  }

  async function toggleSimulation() {
    const button = $('#simulate-button');
    button.disabled = true;
    try {
      const result = await request(`/api/simulation/${state.simulation ? 'stop' : 'start'}`, { method: 'POST', body: state.simulation ? undefined : JSON.stringify({ device: 'sim-01', interval_seconds: 5 }) });
      setSimulationButton(Boolean(result.running));
      if (result.running) {
        // Demo readings are stored under the simulator's own device id. A device filter
        // left on a real sensor therefore hides every one of them, so the button looked
        // like it did nothing while readings were in fact arriving and being stored.
        await showDemoReadings(result.device || 'sim-01');
        updateConnection(true, 'Connected');
      } else {
        await restoreUserFilter();
        showToast('Demo readings stopped');
      }
    } catch (error) { showToast(`Simulation could not be updated: ${error.message}`); }
    finally { button.disabled = false; }
  }

  // Point the dashboard at the simulator so demo readings are actually visible, and
  // remember the user's own filter so stopping the demo puts it back.
  async function showDemoReadings(simDevice) {
    try {
      const body = await request('/api/devices');
      state.devices = body.devices || [];
      renderDevices();
    } catch { /* The filter below still works from the existing device list. */ }
    // "All devices" already includes the demo readings, so only move the filter when it is
    // pointing at some other device and would hide them.
    if (!state.device || state.device === simDevice) {
      showToast('Demo readings started');
      return;
    }
    const target = state.devices.some((device) => device.device === simDevice) ? simDevice : '';
    state.demoFilter = { restoreTo: state.device, applied: target };
    await applyDeviceFilter(target);
    showToast(`Demo readings started — now showing ${target || 'all devices'} so you can see them`);
  }

  async function restoreUserFilter() {
    const demoFilter = state.demoFilter;
    state.demoFilter = null;
    // Only restore when the filter is still the one the demo set, so a deliberate
    // change the user made while the demo was running is never clobbered.
    if (demoFilter && state.device === demoFilter.applied && demoFilter.restoreTo !== state.device) {
      await applyDeviceFilter(demoFilter.restoreTo);
    }
  }

  async function applyDeviceFilter(device) {
    state.device = device;
    await refreshHistory();
    $('#device-select').value = device;
    renderMetrics();
    renderChart();
  }

  async function refreshHistory() {
    try {
      const query = new URLSearchParams({ range: state.range, limit: '300' });
      if (state.device) query.set('device', state.device);
      const body = await request(`/api/readings?${query}`);
      history.length = 0;
      history.push(...(body.readings || []).reverse());
      renderMetrics(); renderChart();
    } catch (error) { showToast(`History could not be loaded: ${error.message}`); }
  }

  $('#simulate-button').addEventListener('click', toggleSimulation);
  $('#notifications-button').addEventListener('click', () => $('#alerts').scrollIntoView({ behavior: 'smooth', block: 'center' }));
  $('#device-status-trigger').addEventListener('click', showDeviceDetails);
  $('#device-status-button').addEventListener('click', showDeviceDetails);
  document.querySelectorAll('[data-metric]').forEach((button) => button.addEventListener('click', () => showMetricDetails(button.dataset.metric, button)));
  $('#workspace-button').addEventListener('click', showWorkspaceDialog);
  $('#account-menu-button').addEventListener('click', showAccountActions);
  $('#dialog-close').addEventListener('click', closeDialog);
  $('#action-dialog').addEventListener('click', (event) => { if (event.target === $('#action-dialog')) closeDialog(); });
  if (new URLSearchParams(location.search).get('view') === 'map') {
    document.body.classList.add('map-view');
    document.querySelector('.page-heading').hidden = true;
    document.querySelectorAll('.hero-showcase, .hero-reading-meta, .suitability-section, .metric-grid, .content-grid, .bottom-grid, .page-footer').forEach((section) => { section.hidden = true; });
    document.querySelector('.breadcrumbs span:first-child').textContent = 'Field tools';
    document.querySelector('.help-card').hidden = true;
    const dashboardUrl = new URL(location.href);
    dashboardUrl.searchParams.delete('view');
    dashboardUrl.hash = '#overview';
    document.querySelector('.brand').href = dashboardUrl.href;
    document.querySelector('.nav-list').setAttribute('aria-label', 'Map navigation');
    document.querySelectorAll('.nav-link[href*="#"], .mobile-nav-link[href*="#"]').forEach((link) => {
      const sectionId = link.getAttribute('href').split('#')[1];
      if (!['overview', 'map-view'].includes(sectionId)) link.hidden = true;
      if (sectionId === 'overview') {
        link.href = dashboardUrl.href;
        link.removeAttribute('target');
        link.dataset.mapMode = 'keep';
      } else if (sectionId === 'map-view') {
        link.href = '#map-view';
        link.removeAttribute('target');
        link.dataset.mapMode = 'keep';
      }
    });
    document.title = 'AgriSense AI — GPS map';
    document.querySelector('.workspace').hidden = true;
    document.querySelector('.profile').hidden = true;
    document.querySelector('.nav-label').hidden = true;
    document.querySelector('.nav-list').style.gap = '6px';
    document.querySelector('.map-section').style.marginTop = '20px';
    document.querySelectorAll('.nav-link.active').forEach((link) => link.classList.remove('active'));
    const mapLink = document.querySelector('[data-map-link]');
    if (mapLink) { mapLink.setAttribute('aria-current', 'page'); mapLink.classList.add('active'); }
    document.querySelectorAll('.mobile-nav-link[href="#map-view"]').forEach((link) => { link.classList.add('active'); link.setAttribute('aria-current', 'page'); });
    $('#breadcrumb-current').textContent = 'GPS map';
  }
  document.addEventListener('keydown', (event) => {
    const dialog = $('#action-dialog');
    if (dialog.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeDialog(); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])')]
      .filter((element) => !element.hidden && element.getClientRects().length);
    if (!focusable.length) { event.preventDefault(); dialog.querySelector('.action-dialog').focus(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    const activeIsOutside = !dialog.contains(document.activeElement);
    const dialogContainer = dialog.querySelector('.action-dialog');
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogContainer || activeIsOutside)) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogContainer || activeIsOutside)) {
      event.preventDefault(); first.focus();
    }
  });
  $('#dialog-content').addEventListener('click', async (event) => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (!action) return;
    if (action === 'suitability') { closeDialog(); $('#suitability').scrollIntoView({ behavior: 'smooth' }); }
    else if (action === 'view-devices') { closeDialog(); $('#devices').scrollIntoView({ behavior: 'smooth' }); }
    else if (action === 'export') { closeDialog(); exportReadingsCsv(); }
    else if (action === 'devices-refresh') {
      try {
        const body = await request('/api/devices');
        state.devices = body.devices || [];
        renderDevices();
        closeDialog();
        showToast('Device status refreshed');
      } catch (error) { showToast(`Devices could not be refreshed: ${error.message}`); }
    }
  });

  const savedWorkspace = localStorage.getItem('agrisense-workspace-name');
  if (savedWorkspace) {
    $('.workspace b').textContent = savedWorkspace;
    $('.workspace-icon').textContent = savedWorkspace.trim().charAt(0).toUpperCase();
  }
  const navLabels = new Map([...document.querySelectorAll('.nav-link[href*="#"]')].map((link) => [link.getAttribute('href').split('#')[1], link.querySelector('span:not(.nav-icon)')?.textContent.trim() || 'Overview']));
  navLabels.set('map-view', 'GPS map');
  const updateActiveNavigation = () => {
    if (new URLSearchParams(location.search).get('view') === 'map') return;
    const sections = [...navLabels.keys()].map((id) => document.getElementById(id)).filter((section) => section && !section.hidden && section.getClientRects().length);
    const activeView = document.body.dataset.view;
    const preferred = activeView ? document.getElementById(activeView) : null;
    const current = (preferred && !preferred.hidden && preferred.getClientRects().length ? preferred : null)
      || sections.filter((section) => section.getBoundingClientRect().top <= 150).at(-1) || sections[0];
    if (!current) return;
    document.querySelectorAll('.nav-link.active, .mobile-nav-link.active').forEach((link) => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
    document.querySelectorAll(`.nav-link[href*="#${current.id}"], .mobile-nav-link[href*="#${current.id}"]`).forEach((link) => { link.classList.add('active'); link.setAttribute('aria-current', 'location'); });
    $('#breadcrumb-current').textContent = navLabels.get(current.id) || 'Overview';
  };
  document.querySelectorAll('.nav-link[href*="#"], .mobile-nav-link[href*="#"]').forEach((link) => link.addEventListener('click', () => {
    if (link.target === '_blank') return;
    const sectionId = link.getAttribute('href').split('#')[1];
    document.querySelectorAll('.nav-link.active, .mobile-nav-link.active').forEach((active) => { active.classList.remove('active'); active.removeAttribute('aria-current'); });
    document.querySelectorAll(`.nav-link[href*="#${sectionId}"], .mobile-nav-link[href*="#${sectionId}"]`).forEach((active) => { active.classList.add('active'); active.setAttribute('aria-current', 'location'); });
    $('#breadcrumb-current').textContent = navLabels.get(sectionId) || 'Overview';
  }));
  window.addEventListener('scroll', updateActiveNavigation, { passive: true });
  window.addEventListener('hashchange', updateActiveNavigation);
  updateActiveNavigation();
  window.addEventListener('agrisense-location-state', () => {
    if (location.hash === '#map-view') updateActiveNavigation();
  });
  const chartWrap = $('#chart-wrap');
  chartWrap.addEventListener('pointermove', (event) => {
    const rect = chartWrap.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const readings = visibleReadings().slice(-40);
    if (!readings.length) return;
    const reading = readings[Math.round(fraction * (readings.length - 1))];
    const tooltip = $('#chart-tooltip');
    tooltip.innerHTML = `<b>${escapeHtml(relativeTime(reading.timestamp))}</b><span>Soil ${Number.isFinite(Number(reading.soil_moisture)) ? `${Number(reading.soil_moisture).toFixed(1)}%` : '—'}${Number.isFinite(Number(reading.temperature)) ? ` · Air ${Number(reading.temperature).toFixed(1)}°C` : ''}</span>`;
    tooltip.style.left = `${Math.max(78, Math.min(rect.width - 78, fraction * rect.width))}px`;
    tooltip.hidden = false;
  });
  chartWrap.addEventListener('pointerleave', () => { $('#chart-tooltip').hidden = true; });
  chartWrap.addEventListener('focusin', () => { $('#chart-tooltip').hidden = true; });
  $('#device-select').addEventListener('change', async (event) => {
    state.device = event.target.value;
    await refreshHistory();
    renderMetrics(); renderChart();
    try { const body = await request(`/api/insights${state.device ? `?device=${encodeURIComponent(state.device)}` : ''}`); setInsights(body.insights, body.device); } catch { /* Existing insight remains visible while reconnecting. */ }
  });
  document.querySelectorAll('.range-tabs button').forEach((button) => button.addEventListener('click', async () => {
    document.querySelector('.range-tabs .selected')?.classList.remove('selected');
    button.classList.add('selected');
    document.querySelectorAll('.range-tabs button').forEach((tab) => tab.setAttribute('aria-pressed', String(tab === button)));
    state.range = button.dataset.range;
    await refreshHistory();
  }));
  $('#refresh-devices').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try { const body = await request('/api/devices'); state.devices = body.devices || []; renderDevices(); showToast('Device list refreshed'); }
    catch (error) { showToast(`Devices could not be refreshed: ${error.message}`); }
    finally { button.disabled = false; }
  });

  function tick() {
    $('#clock').textContent = `${new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' }).format(new Date())} · ${new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date())}`;
    const reading = visibleReadings().at(-1);
    if (reading) $('#last-reading').textContent = `${formatTime(reading.timestamp)} · ${relativeTime(reading.timestamp)}`;
  }
  tick();
  setInterval(tick, 1000);
  setInterval(async () => {
    try { const body = await request('/api/devices'); state.devices = body.devices || []; renderDevices(); } catch { /* WebSocket reconnect state is the primary connection indicator. */ }
  }, 10000);
  loadInitialData().finally(connectWebSocket);
})();
