(() => {
  'use strict';
  const FILE_ORIGIN = location.protocol === 'file:';
  const API_ORIGIN = FILE_ORIGIN ? 'http://localhost:5000' : '';
  const form = document.querySelector('#suitability-form');
  if (!form) return;
  const fields = ['solar_kwh_m2_day', 'annual_rainfall_mm', 'soil_ph', 'soil_salinity_ds_m', 'wind_kmh', 'dust_risk', 'water_availability', 'mean_temp_c', 'slope_pct', 'market_distance_km'];
  const formFields = ['site_name', 'area_ha', ...fields, 'budget', 'currency', 'price_per_ton', 'open_field_capex', 'open_field_opex', 'greenhouse_capex', 'greenhouse_opex', 'hydroponic_capex', 'hydroponic_opex'];
  const formNode = (name) => form.elements.namedItem(name);
  const errorNode = document.querySelector('#suitability-error');
  const resultsNode = document.querySelector('#suitability-results');
  const fileStatus = document.querySelector('#gis-file-status');
  const locationStatus = document.querySelector('#location-status');
  const locationStorageKey = 'agrisense-site-location-v1';
  const locationIdKey = 'agrisense-site-location-id-v1';
  const locationDeleteKey = 'agrisense-site-location-delete-v1';
  let savedLocation = null;
  let locationRevision = 0;
  let locationSyncing = false;
  let locationRetryTimer = null;
  let durableLocationStorage = true;
  let mapZoom = 15;
  let mapOnlineEnabled = false;
  let mapRequestNumber = 0;
  let mapLoadTimer = null;
  let mapFrameLoaded = false;
  const temporaryLocationStorage = new Map();
  function readLocationStorage(key) {
    if (!durableLocationStorage) return temporaryLocationStorage.get(key) ?? null;
    try {
      const value = window.localStorage.getItem(key);
      if (value !== null) temporaryLocationStorage.set(key, value);
      return value;
    } catch { durableLocationStorage = false; return temporaryLocationStorage.get(key) ?? null; }
  }
  function writeLocationStorage(key, value) {
    temporaryLocationStorage.set(key, value);
    try { window.localStorage.setItem(key, value); }
    catch { durableLocationStorage = false; }
  }
  function deleteLocationStorage(key) {
    temporaryLocationStorage.delete(key);
    try { window.localStorage.removeItem(key); }
    catch { durableLocationStorage = false; }
  }
  let layerSources = [];
  let requestNumber = 0;
  const example = {
    site_name: 'North Field · Demo', area_ha: 2.5, solar_kwh_m2_day: 6.2, annual_rainfall_mm: 750,
    soil_ph: 6.4, soil_salinity_ds_m: 1.2, wind_kmh: 18, dust_risk: 20, water_availability: 75,
    mean_temp_c: 24, slope_pct: 2, market_distance_km: 35, budget: 30000, currency: 'USD',
    price_per_ton: 320, open_field_capex: 1800, open_field_opex: 2200,
    greenhouse_capex: 18000, greenhouse_opex: 6800, hydroponic_capex: 24000, hydroponic_opex: 8500,
  };

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }
  function money(value, currency) {
    if (value == null || !Number.isFinite(Number(value))) return 'Not reached';
    try { return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 0 }).format(value); }
    catch { return `${currency} ${Number(value).toLocaleString()}`; }
  }
  function setValues(values) {
    for (const [name, value] of Object.entries(values)) {
      const control = formFields.includes(name) ? formNode(name) : null;
      if (control) control.value = String(value);
    }
  }
  function makeLocationId() {
    return globalThis.crypto?.randomUUID?.() || `site_${Date.now()}_${Math.random().toString(36).slice(2, 14)}`;
  }
  function setLocationStatus(message, mode = '') {
    locationStatus.textContent = message;
    document.querySelector('#location-signal').classList.toggle('location-signal-online', mode === 'online');
    document.querySelector('#location-signal').classList.toggle('location-signal-pending', mode === 'pending');
    window.dispatchEvent(new CustomEvent('agrisense-location-state', { detail: { location: savedLocation, message, mode, online: navigator.onLine } }));
  }
  function mapEmbedUrl(location) {
    const zoom = mapZoom;
    const latSpan = Math.min(80, 180 / (2 ** zoom));
    const lonSpan = Math.min(170, latSpan / Math.max(Math.cos(location.latitude * Math.PI / 180), 0.15));
    const west = Math.max(-180, location.longitude - lonSpan);
    const east = Math.min(180, location.longitude + lonSpan);
    const south = Math.max(-85, location.latitude - latSpan);
    const north = Math.min(85, location.latitude + latSpan);
    const bbox = [west, south, east, north].map((value) => value.toFixed(6)).join('%2C');
    return `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${location.latitude.toFixed(6)}%2C${location.longitude.toFixed(6)}`;
  }
  function renderGpsMap(state = {}) {
    const frame = document.querySelector('#site-map-frame');
    const offlineCanvas = document.querySelector('#map-offline-canvas');
    if (!frame || !offlineCanvas) return;
    const location = state.location === undefined ? savedLocation : state.location;
    const isOnline = state.online === undefined ? navigator.onLine : state.online;
    const message = state.message || '';
    const mode = state.mode || '';
    const loading = document.querySelector('#map-loading');
    const loadButton = document.querySelector('#map-load-online');
    const showFallback = (caption) => {
      clearTimeout(mapLoadTimer);
      mapFrameLoaded = false;
      frame.hidden = true;
      frame.removeAttribute('src');
      loading.hidden = true;
      offlineCanvas.hidden = false;
      loadButton.hidden = !location || !isOnline || mapOnlineEnabled;
      if (caption) document.querySelector('#offline-map-caption').textContent = caption;
    };
    const connection = document.querySelector('#map-connection-status');
    connection.classList.toggle('online', isOnline);
    connection.classList.toggle('offline', !isOnline);
    connection.querySelector('b').textContent = isOnline ? 'Internet available' : 'Offline mode';
    const gpsStatus = document.querySelector('#map-gps-status');
    gpsStatus.classList.toggle('has-fix', Boolean(location));
    gpsStatus.classList.toggle('no-fix', !location);
    gpsStatus.querySelector('b').textContent = location ? (mode === 'pending' || !location.synced ? 'GPS fix saved locally' : 'GPS location synced') : 'No GPS fix';
    document.querySelector('#map-site-name').textContent = location?.site_name || 'No site location saved';
    document.querySelector('#map-site-status').textContent = location
      ? (message || (location.synced ? 'Location is saved locally and synchronized with the backend.' : 'Location is saved locally and waiting to sync.'))
      : (message || 'Use the button below to request a one-time location fix.');
    document.querySelector('#map-latitude').textContent = location ? location.latitude.toFixed(6) : '—';
    document.querySelector('#map-longitude').textContent = location ? location.longitude.toFixed(6) : '—';
    document.querySelector('#map-accuracy').textContent = location ? `±${Math.round(location.accuracy_m)} m` : '—';
    document.querySelector('#map-captured-at').textContent = location?.captured_at ? new Date(location.captured_at).toLocaleString() : '—';
    document.querySelector('#map-sync-indicator').classList.toggle('synced', Boolean(location?.synced));
    document.querySelector('#map-clear-location').hidden = !location;
    const captureButton = document.querySelector('#map-capture-location');
    captureButton.disabled = document.querySelector('#capture-location').disabled;
    const openMap = document.querySelector('#map-open-external');
    if (!location) {
      mapOnlineEnabled = false;
      mapRequestNumber += 1;
      showFallback('Capture a GPS fix to see your site coordinates here.');
      document.querySelector('#offline-map-title').textContent = 'Waiting for GPS location';
      document.querySelector('#offline-map-pin').hidden = true;
      document.querySelector('#offline-map-grid').classList.remove('has-location');
      document.querySelector('#map-scale').textContent = 'Coordinate preview · awaiting GPS';
      openMap.hidden = true;
      return;
    }
    document.querySelector('#offline-map-title').textContent = location.site_name || 'Saved farm location';
    document.querySelector('#offline-map-pin').hidden = false;
    const offlineGrid = document.querySelector('#offline-map-grid');
    offlineGrid.classList.add('has-location');
    offlineGrid.style.transform = `perspective(450px) rotateX(24deg) scale(${(1.28 * (1.12 ** (mapZoom - 15))).toFixed(3)})`;
    document.querySelector('#offline-map-caption').textContent = `${location.latitude.toFixed(6)}, ${location.longitude.toFixed(6)} · accuracy ±${Math.round(location.accuracy_m)} m`;
    document.querySelector('#map-scale').textContent = `Zoom ${mapZoom} · coordinates saved on this device`;
    openMap.href = `https://www.openstreetmap.org/?mlat=${encodeURIComponent(location.latitude)}&mlon=${encodeURIComponent(location.longitude)}#map=${mapZoom}/${encodeURIComponent(location.latitude)}/${encodeURIComponent(location.longitude)}`;
    openMap.hidden = false;
    if (!isOnline) {
      mapRequestNumber += 1;
      showFallback('You are offline. GPS coordinates are available; connect to load street and satellite map tiles.');
      document.querySelector('#map-scale').textContent = `Zoom ${mapZoom} · offline coordinate view, not cached map tiles`;
      return;
    }
    if (!mapOnlineEnabled) {
      mapRequestNumber += 1;
      showFallback('Preview shows your saved coordinates. Choose “Load online map tiles” to send this location to OpenStreetMap and display streets.');
      return;
    }
    const currentMapUrl = mapEmbedUrl(location);
    if (mapFrameLoaded && frame.src === currentMapUrl) {
      frame.hidden = false;
      offlineCanvas.hidden = true;
      loading.hidden = true;
      loadButton.hidden = true;
      return;
    }
    if (!frame.hidden && frame.src !== currentMapUrl) {
      mapFrameLoaded = false;
      offlineCanvas.hidden = true;
      loading.hidden = false;
      loadButton.hidden = true;
      frame.src = currentMapUrl;
      return;
    }
    const requestId = ++mapRequestNumber;
    clearTimeout(mapLoadTimer);
    mapFrameLoaded = false;
    frame.hidden = true;
    offlineCanvas.hidden = true;
    loading.hidden = false;
    loadButton.hidden = true;
    frame.onload = () => {
      if (requestId !== mapRequestNumber) return;
      clearTimeout(mapLoadTimer);
      mapFrameLoaded = true;
      frame.hidden = false;
      offlineCanvas.hidden = true;
      loading.hidden = true;
    };
    frame.onerror = () => {
      if (requestId !== mapRequestNumber) return;
      mapRequestNumber += 1;
      mapOnlineEnabled = false;
      showFallback('The online map could not load. Check your connection and try again.');
    };
    frame.src = currentMapUrl;
    mapLoadTimer = setTimeout(() => {
      if (requestId !== mapRequestNumber || mapFrameLoaded) return;
      mapRequestNumber += 1;
      mapOnlineEnabled = false;
      showFallback('The online map did not load. Check your connection and try loading the map again.');
    }, 12000);
  }
  function renderSavedLocation() {
    const hasLocation = Boolean(savedLocation);
    document.querySelector('#site-latitude').textContent = hasLocation ? savedLocation.latitude.toFixed(6) : 'Not set';
    document.querySelector('#site-longitude').textContent = hasLocation ? savedLocation.longitude.toFixed(6) : 'Not set';
    document.querySelector('#site-accuracy').textContent = hasLocation ? `±${Math.round(savedLocation.accuracy_m)} m` : '—';
    document.querySelector('#clear-location').hidden = !hasLocation;
    document.querySelector('#capture-location').disabled = false;
    if (hasLocation) {
      const mapLink = `https://www.openstreetmap.org/?mlat=${encodeURIComponent(savedLocation.latitude)}&mlon=${encodeURIComponent(savedLocation.longitude)}#map=16/${encodeURIComponent(savedLocation.latitude)}/${encodeURIComponent(savedLocation.longitude)}`;
      let link = document.querySelector('#site-map-link');
      if (!link) {
        link = document.createElement('a');
        link.id = 'site-map-link';
        link.className = 'subtle-button map-link';
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = 'Open map ↗';
        document.querySelector('.location-actions').append(link);
      }
      link.href = mapLink;
      link.hidden = false;
    } else {
      document.querySelector('#site-map-link')?.remove();
    }
    renderGpsMap();
  }
  function saveLocalLocation(location) {
    locationRevision += 1;
    savedLocation = location;
    writeLocationStorage(locationStorageKey, JSON.stringify(location));
    writeLocationStorage(locationIdKey, location.site_id);
    renderSavedLocation();
  }
  async function syncSiteLocation() {
    if (locationSyncing) return;
    if (!navigator.onLine) {
      if (savedLocation) setLocationStatus('Saved on this device. Waiting for an internet connection and backend to sync.', 'pending');
      else if (readLocationStorage(locationDeleteKey)) setLocationStatus('Location deletion is queued on this device until the backend is reachable.', 'pending');
      return;
    }
    locationSyncing = true;
    const startingRevision = locationRevision;
    try {
      const pendingDelete = readLocationStorage(locationDeleteKey);
      if (pendingDelete) {
        const response = await fetch(`${API_ORIGIN}/api/site-location/${encodeURIComponent(pendingDelete)}`, { method: 'DELETE' });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.message || body.error || `Sync failed (${response.status})`);
        deleteLocationStorage(locationDeleteKey);
        if (!savedLocation && readLocationStorage(locationIdKey) === pendingDelete) {
          deleteLocationStorage(locationIdKey);
          setLocationStatus('Location removed from this device and the AgriSense backend.');
        }
      }
      if (savedLocation && !savedLocation.synced) {
        const submittedLocation = { ...savedLocation };
        setLocationStatus('Saved on this device. Syncing site location…', 'pending');
        const response = await fetch(`${API_ORIGIN}/api/site-location`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(submittedLocation),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.message || body.error || `Sync failed (${response.status})`);
        if (savedLocation?.site_id === submittedLocation.site_id && savedLocation?.captured_at === submittedLocation.captured_at) {
          savedLocation = { ...savedLocation, synced: true, received_at: body.location?.received_at };
          writeLocationStorage(locationStorageKey, JSON.stringify(savedLocation));
          setLocationStatus(`Location saved on this device and synced with the AgriSense backend · captured ${new Date(savedLocation.captured_at).toLocaleString()}.`, 'online');
        }
      } else if (!savedLocation && readLocationStorage(locationIdKey)) {
        setLocationStatus('Checking for this browser’s synchronized site location…', 'pending');
        const siteId = readLocationStorage(locationIdKey);
        const response = await fetch(`${API_ORIGIN}/api/site-location/${encodeURIComponent(siteId)}`);
        if (response.status === 404) {
          deleteLocationStorage(locationIdKey);
          setLocationStatus('No saved location on this device or backend. Capture a fix when ready.');
        } else {
          const body = await response.json();
          if (!response.ok) throw new Error(body.message || body.error || `Sync failed (${response.status})`);
          if (body.location && startingRevision === locationRevision && !savedLocation) {
            savedLocation = { ...body.location, synced: true };
            writeLocationStorage(locationStorageKey, JSON.stringify(savedLocation));
            renderSavedLocation();
            setLocationStatus(`Restored the synced location for ${body.location.site_name} · captured ${new Date(body.location.captured_at).toLocaleString()}.`, 'online');
          }
        }
      } else if (savedLocation?.synced) {
        setLocationStatus(`Location saved on this device and synced with the AgriSense backend · captured ${new Date(savedLocation.captured_at).toLocaleString()}.`, 'online');
      }
    } catch (error) {
      if (savedLocation) setLocationStatus(`Location is safe on this device; backend sync will retry when available. ${error.message}`, 'pending');
      else setLocationStatus(`Could not check the backend location: ${error.message}.`, 'pending');
      clearTimeout(locationRetryTimer);
      locationRetryTimer = setTimeout(syncSiteLocation, 20000);
    } finally {
      locationSyncing = false;
      if (startingRevision !== locationRevision) queueMicrotask(syncSiteLocation);
    }
  }
  function captureSiteLocation() {
    if (!navigator.geolocation) {
      setLocationStatus('This browser does not provide GPS/location services. Try a supported browser on a GPS-capable device.');
      return;
    }
    const buttons = [document.querySelector('#capture-location'), document.querySelector('#map-capture-location')];
    buttons.forEach((button) => { button.disabled = true; });
    setLocationStatus('Requesting permission and a GPS/network location fix…');
    navigator.geolocation.getCurrentPosition((position) => {
      const location = {
        site_id: readLocationStorage(locationIdKey) || makeLocationId(),
        site_name: formNode('site_name').value.trim() || 'Farm site',
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy_m: position.coords.accuracy,
        captured_at: new Date(position.timestamp || Date.now()).toISOString(),
        synced: false,
      };
      try {
        deleteLocationStorage(locationDeleteKey);
        saveLocalLocation(location);
        const persistenceNote = durableLocationStorage ? '' : ' Persistent browser storage is unavailable, so offline access lasts only for this tab session.';
        setLocationStatus(`Location saved on this device.${persistenceNote} Syncing to the backend when available…`, 'pending');
        syncSiteLocation();
      } catch (error) {
        setLocationStatus(`Could not save this location locally: ${error.message}`);
      } finally {
        buttons.forEach((button) => { button.disabled = false; });
      }
    }, (error) => {
      const reasons = {
        1: 'Location permission was denied. Allow location access in your browser to continue.',
        2: 'The device could not determine its location. Check GPS/location services and try again.',
        3: 'Location lookup timed out. Move to an area with a clearer GPS/network signal and retry.',
      };
      setLocationStatus(reasons[error.code] || 'Location could not be read. Check device permissions and try again.');
      buttons.forEach((button) => { button.disabled = false; });
    }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 60000 });
  }
  function clearSiteLocation() {
    locationRevision += 1;
    const siteId = savedLocation?.site_id || readLocationStorage(locationIdKey);
    if (siteId) writeLocationStorage(locationDeleteKey, siteId);
    deleteLocationStorage(locationStorageKey);
    savedLocation = null;
    renderSavedLocation();
    if (navigator.onLine) setLocationStatus('Removed from this device. Sending the deletion to the backend…', 'pending');
    else setLocationStatus('Removed from this device. The backend deletion is queued until a connection is available.', 'pending');
    syncSiteLocation();
  }
  function restoreLocalLocation() {
    const raw = readLocationStorage(locationStorageKey);
    if (raw) {
      try {
        savedLocation = JSON.parse(raw);
        if (!savedLocation || !Number.isFinite(savedLocation.latitude) || !Number.isFinite(savedLocation.longitude)) savedLocation = null;
      } catch { savedLocation = null; }
    }
    renderSavedLocation();
    if (savedLocation) {
      const synced = Boolean(savedLocation.synced);
      const durabilityNote = durableLocationStorage ? '' : ' This browser blocks persistent local storage; the fix lasts only for this tab session.';
      setLocationStatus((synced ? `Location available on this device; checking backend sync · captured ${new Date(savedLocation.captured_at).toLocaleString()}.` : 'Offline location is saved on this device and waiting to sync.') + durabilityNote, synced ? 'online' : 'pending');
    } else if (!readLocationStorage(locationIdKey) && readLocationStorage(locationDeleteKey)) {
      setLocationStatus('Location deletion is queued on this device until the backend is reachable.', 'pending');
    }
    syncSiteLocation();
  }
  document.querySelector('#capture-location').addEventListener('click', captureSiteLocation);
  document.querySelector('#clear-location').addEventListener('click', clearSiteLocation);
  document.querySelector('#map-capture-location').addEventListener('click', captureSiteLocation);
  document.querySelector('#map-clear-location').addEventListener('click', clearSiteLocation);
  document.querySelector('#map-load-online').addEventListener('click', () => { mapOnlineEnabled = true; renderGpsMap(); });
  document.querySelector('#map-center-site').addEventListener('click', () => {
    if (!savedLocation) { document.querySelector('#map-site-status').textContent = 'Capture a GPS fix before centering the map.'; return; }
    mapFrameLoaded = false;
    if (mapOnlineEnabled) renderGpsMap();
    else document.querySelector('#map-site-status').textContent = 'Centered the coordinate preview on your saved site.';
  });
  document.querySelector('#map-zoom-in').addEventListener('click', () => { mapZoom = Math.min(19, mapZoom + 1); mapFrameLoaded = false; renderGpsMap(); });
  document.querySelector('#map-zoom-out').addEventListener('click', () => { mapZoom = Math.max(3, mapZoom - 1); mapFrameLoaded = false; renderGpsMap(); });
  window.addEventListener('online', () => { renderGpsMap({ online: true }); syncSiteLocation(); });
  window.addEventListener('offline', () => renderGpsMap({ online: false, message: savedLocation ? 'Offline. Saved GPS coordinates are still available on this device.' : '' }));
  window.addEventListener('agrisense-location-state', (event) => renderGpsMap(event.detail));
  window.addEventListener('offline', () => {
    if (savedLocation) setLocationStatus(savedLocation.synced
      ? 'Offline. The saved site location remains available on this device; backend sync resumes when online.'
      : 'Offline. The GPS fix is saved on this device and queued for backend sync.', 'pending');
  });
  document.querySelector('#suitability-example').addEventListener('click', exampleValues);
  restoreLocalLocation();
  function exampleValues() {
    setValues(example);
    layerSources = ['Illustrative demo values — not imported from GIS'];
    fileStatus.textContent = 'Example values added for practice. Replace them with your own land measurements and local prices.';
    errorNode.textContent = '';
    resultsNode.hidden = true;
  }
  function parseCsv(text) {
    const lines = text.split(/\r?\n/).filter((line) => line.trim());
    if (lines.length < 2) throw new Error('CSV needs a header row and at least one data row.');
    const parseLine = (line) => {
      const values = [];
      let value = '', quoted = false;
      for (let i = 0; i < line.length; i += 1) {
        const char = line[i];
        if (char === '"' && line[i + 1] === '"' && quoted) { value += '"'; i += 1; }
        else if (char === '"') quoted = !quoted;
        else if (char === ',' && !quoted) { values.push(value.trim()); value = ''; }
        else value += char;
      }
      values.push(value.trim());
      return values;
    };
    const headers = parseLine(lines[0]).map(normalizeKey);
    const rows = lines.slice(1).map(parseLine);
    const aliases = { solar: 'solar_kwh_m2_day', solar_irradiance: 'solar_kwh_m2_day', irradiance: 'solar_kwh_m2_day', ghi: 'solar_kwh_m2_day', solar_exposure: 'solar_kwh_m2_day', rainfall: 'annual_rainfall_mm', precipitation: 'annual_rainfall_mm', annual_precipitation: 'annual_rainfall_mm', annual_rainfall: 'annual_rainfall_mm', ph: 'soil_ph', soilph: 'soil_ph', salinity: 'soil_salinity_ds_m', soil_salinity: 'soil_salinity_ds_m', ec: 'soil_salinity_ds_m', electrical_conductivity: 'soil_salinity_ds_m', wind: 'wind_kmh', wind_speed: 'wind_kmh', max_wind: 'wind_kmh', dust: 'dust_risk', wind_dust_risk: 'dust_risk', water: 'water_availability', irrigation_access: 'water_availability', temperature: 'mean_temp_c', mean_temp: 'mean_temp_c', growing_season_temp: 'mean_temp_c', slope: 'slope_pct', terrain_slope: 'slope_pct', market_distance: 'market_distance_km', distance_to_market: 'market_distance_km' };
    const mappedHeaders = headers.map((header) => formFields.includes(header) ? header : aliases[header]);
    const headerIndex = mappedHeaders.findIndex(Boolean);
    if (headerIndex >= 0) {
      const result = {};
      rows.forEach((row) => mappedHeaders.forEach((header, index) => {
        if (header && header !== 'site_name' && header !== 'currency' && row[index] !== '') {
          const value = asNumber(row[index]);
          if (value !== null) result[header] = value;
        }
      }));
      return result;
    }
    const result = {};
    rows.forEach((row) => {
      const [key, value] = row;
      if (key && value !== undefined) result[key.trim().toLowerCase().replaceAll(' ', '_')] = value.trim();
    });
    return result;
  }
  function asNumber(value) { return value !== '' && value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value) : null; }

  function normalizeKey(value) {
    return String(value).toLowerCase().trim()
      .replace(/\s*\([^)]*\)\s*/g, '')
      .replace(/[\s\-/.]+/g, '_')
      .replace(/[^a-z0-9_]/g, '')
      .replace(/_+/g, '_').replace(/^_|_$/g, '');
  }
  function normalizeLayerData(data) {
    if (Array.isArray(data)) {
      data = data.map((item) => {
        if (!item || typeof item !== 'object') return {};
        const key = item.key ?? item.layer ?? item.name ?? item.variable;
        const value = item.mean ?? item.average ?? item.value ?? item.summary;
        return key === undefined ? item : { [key]: value };
      }).reduce((merged, item) => ({ ...merged, ...item }), {});
    }
    if (!data || typeof data !== 'object') throw new Error('GIS summary must contain an object of layer names and values.');
    const flat = data.layers && typeof data.layers === 'object' ? { ...data, ...data.layers } : data;
    const aliases = {
      sunshine_hours: 'solar_kwh_m2_day', sunlight_hours: 'solar_kwh_m2_day', solar: 'solar_kwh_m2_day', solar_irradiance: 'solar_kwh_m2_day', irradiance: 'solar_kwh_m2_day', ghi: 'solar_kwh_m2_day', solar_exposure: 'solar_kwh_m2_day',
      rainfall: 'annual_rainfall_mm', precipitation: 'annual_rainfall_mm', annual_precipitation: 'annual_rainfall_mm', annual_rainfall: 'annual_rainfall_mm',
      ph: 'soil_ph', soilph: 'soil_ph', salinity: 'soil_salinity_ds_m', soil_salinity: 'soil_salinity_ds_m', ec: 'soil_salinity_ds_m', electrical_conductivity: 'soil_salinity_ds_m',
      wind: 'wind_kmh', wind_speed: 'wind_kmh', max_wind: 'wind_kmh', dust: 'dust_risk', wind_dust_risk: 'dust_risk', water: 'water_availability', irrigation_access: 'water_availability',
      temperature: 'mean_temp_c', mean_temp: 'mean_temp_c', growing_season_temp: 'mean_temp_c', slope: 'slope_pct', terrain_slope: 'slope_pct', market_distance: 'market_distance_km', distance_to_market: 'market_distance_km', market_access: 'market_distance_km', distance_to_market_km: 'market_distance_km',
    };
    const values = {};
    for (const [rawKey, rawValue] of Object.entries(flat)) {
      const key = normalizeKey(rawKey);
      const normalized = fields.includes(key) ? key : aliases[key];
      if (normalized && rawValue !== '' && rawValue !== null && rawValue !== undefined) {
        const numeric = asNumber(rawValue && typeof rawValue === 'object' ? rawValue.mean ?? rawValue.average ?? rawValue.value ?? rawValue.summary : rawValue);
        if (numeric !== null) values[normalized] = numeric;
      }
    }
    if (!Object.keys(values).length) throw new Error('No recognized layer values were found. Use the layer names shown in the form or API schema.');
    return values;
  }

  document.querySelector('#suitability-example').addEventListener('click', exampleValues);
  document.querySelector('.upload-zone').addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    document.querySelector('#gis-file').click();
  });
  document.querySelector('#gis-file').addEventListener('change', async (event) => {
    const files = [...(event.target.files || [])];
    if (!files.length) return;
    const imported = {};
    const names = [];
    try {
      for (const file of files) {
        const text = await file.text();
        const data = file.name.toLowerCase().endsWith('.json') ? JSON.parse(text) : parseCsv(text);
        Object.assign(imported, normalizeLayerData(data));
        names.push(file.name);
      }
      setValues(imported);
      layerSources = [...new Set([...layerSources, ...names])];
      fileStatus.textContent = `Imported ${Object.keys(imported).length} layer value${Object.keys(imported).length === 1 ? '' : 's'} from ${names.join(', ')}. Add remaining GIS summaries and economic estimates before screening.`;
      errorNode.textContent = '';
      resultsNode.hidden = true;
    } catch (error) {
      fileStatus.textContent = '';
      errorNode.textContent = `Could not import layer summary: ${error.message}`;
    } finally {
      event.target.value = '';
    }
  });

  function collectPayload() {
    const site = { name: formNode('site_name').value.trim(), area_ha: Number(formNode('area_ha').value) };
    const layers = {};
    fields.forEach((key) => {
      const value = formNode(key).value.trim();
      if (value !== '') layers[key] = Number(value);
    });
    const economics = {
      currency: formNode('currency').value,
      budget: Number(formNode('budget').value),
      price_per_ton: Number(formNode('price_per_ton').value),
      systems: {
        open_field: { capex_per_ha: Number(formNode('open_field_capex').value), opex_per_ha: Number(formNode('open_field_opex').value) },
        greenhouse: { capex_per_ha: Number(formNode('greenhouse_capex').value), opex_per_ha: Number(formNode('greenhouse_opex').value) },
        hydroponic: { capex_per_ha: Number(formNode('hydroponic_capex').value), opex_per_ha: Number(formNode('hydroponic_opex').value) },
      },
    };
    if (savedLocation) {
      site.location = { latitude: savedLocation.latitude, longitude: savedLocation.longitude, accuracy_m: savedLocation.accuracy_m };
      site.location_captured_at = savedLocation.captured_at;
    }
    return { site, layers, economics, layer_sources: layerSources };
  }

  function renderResults(result) {
    const currency = result.recommendations[0]?.cost_estimates.currency || 'USD';
    const allMatches = result.recommendations;
    const top = allMatches.slice(0, 3);
    const noFit = !allMatches.some((item) => item.cost_estimates.budget_fit);
    const locationSummary = result.site.location
      ? `<p class="screened-location">Site location · ${Number(result.site.location.latitude).toFixed(5)}, ${Number(result.site.location.longitude).toFixed(5)} · GPS accuracy ±${Math.round(result.site.location.accuracy_m)} m · <a href="https://www.openstreetmap.org/?mlat=${encodeURIComponent(result.site.location.latitude)}&amp;mlon=${encodeURIComponent(result.site.location.longitude)}#map=16/${encodeURIComponent(result.site.location.latitude)}/${encodeURIComponent(result.site.location.longitude)}" target="_blank" rel="noopener noreferrer">Open map ↗</a></p>`
      : '';
    const factorHtml = (factors) => factors.slice(0, 4).map((factor) => `<span class="factor-chip ${factor.status}"><i></i>${escapeHtml(factor.label)} <b>${factor.score}</b></span>`).join('');
    const cards = top.map((item, index) => {
      const base = item.scenarios.base;
      const payback = base.payback_years == null ? 'Not reached at this estimate' : `${base.payback_years.toFixed(1)} years`;
      return `<article class="recommendation-card ${index === 0 ? 'recommended' : ''}">
        <div class="recommendation-card-top"><span class="recommendation-rank">${index === 0 ? 'BEST SITE MATCH' : `OPTION 0${index + 1}`}</span><span class="fit-score"><b>${item.suitability_score}</b><small>/100</small></span></div>
        <h3>${escapeHtml(item.crop.name)}</h3><div class="system-label"><i class="system-dot ${escapeHtml(item.production_system.id)}-dot"></i>${escapeHtml(item.production_system.name)} <span>· ${escapeHtml(item.band)} fit</span></div>
        <p class="recommendation-description">${escapeHtml(item.production_system.description)}</p>
        <div class="factor-chips">${factorHtml(item.factors)}</div>
        <div class="budget-state ${item.cost_estimates.budget_fit ? 'budget-ok' : 'budget-over'}"><span>SETUP COST · ${item.cost_estimates.budget_fit ? 'WITHIN BUDGET' : `OVER BY ${money(item.cost_estimates.funding_gap, currency)}`}</span><b>${money(item.cost_estimates.startup_capex, currency)}</b><small>Available budget ${money(item.cost_estimates.budget, currency)} · ${money(item.cost_estimates.startup_capex / Math.max(Number(result.site.area_ha), .01), currency)}/ha</small></div>
        <div class="scenario-title"><span>POSSIBLE YEARLY RESULTS</span><span>harvest · after yearly costs · setup payback</span></div>
        <div class="scenario-grid">${Object.entries(item.scenarios).map(([name, scenario]) => `<div class="scenario-cell"><span>${escapeHtml(name)} · ${Math.round(scenario.yield_factor * 100)}%</span><b>${scenario.yield_tonnes.toLocaleString()} tonnes</b><small>Sales before costs ${money(scenario.gross_revenue, currency)}</small><small>After yearly costs ${money(scenario.net_operating_estimate, currency)}</small><small>Setup paid back ${scenario.payback_years == null ? '—' : `${scenario.payback_years} yr`}</small></div>`).join('')}</div>
        <div class="recommendation-footer"><span>Yearly cost <b>${money(item.cost_estimates.annual_opex, currency)}/yr</b></span><span>Base payback <b>${escapeHtml(payback)}</b></span></div>
        <details class="factor-details"><summary>See matching factors &amp; assumptions</summary><div class="all-factors">${factorHtml(item.factors)}${factorHtml(item.system_factors)}</div></details>
      </article>`;
    }).join('');
    resultsNode.hidden = false;
    resultsNode.classList.remove('results-enter');
    resultsNode.innerHTML = `<div class="results-header"><div><div class="eyebrow"><span class="eyebrow-dot"></span> YOUR FARM DETAILS · ${result.layer_completeness_percent}% OF MEASUREMENTS INCLUDED</div><h2>Options for ${escapeHtml(result.site.name)}</h2><p>${result.site.area_ha.toLocaleString()} ha · ranked by site fit, then whether setup cost fits your budget</p>${locationSummary}</div><button type="button" id="clear-results" class="subtle-button">Clear results</button></div>
      ${noFit ? `<div class="no-budget-fit"><b>No growing method fits the budget entered.</b><span>Best site matches are still shown below with their amounts over budget, so you can assess starting with a smaller area.</span><small>All ${result.recommendations.length} options exceed the current budget.</small></div>` : ''}
      <div class="recommendation-grid">${cards || '<div class="no-budget-fit">No crop/system recommendations could be produced from these inputs.</div>'}</div>
      <details class="assumptions-panel"><summary>How these estimates work</summary><ul>${result.assumptions.map((line) => `<li>${escapeHtml(line)}</li>`).join('')}</ul>${result.layer_sources.length ? `<p class="source-list"><b>Measurements used:</b> ${result.layer_sources.map(escapeHtml).join(', ')}</p>` : ''}</details>`;
    document.querySelector('#clear-results').addEventListener('click', () => { resultsNode.hidden = true; resultsNode.innerHTML = ''; });
    requestAnimationFrame(() => resultsNode.classList.add('results-enter'));
    resultsNode.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    errorNode.textContent = '';
    if (!form.reportValidity()) return;
    const button = document.querySelector('#run-suitability');
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="loading-spinner"></span> Checking your farm details…';
    const thisRequest = ++requestNumber;
    try {
      const response = await fetch(`${API_ORIGIN}/api/suitability/recommendations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(collectPayload()),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || result.error || 'Screening request failed.');
      if (thisRequest === requestNumber) renderResults(result);
    } catch (error) {
      errorNode.textContent = `Could not screen this site: ${error.message}. Start the AgriSense backend and try again.`;
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  });
})();
