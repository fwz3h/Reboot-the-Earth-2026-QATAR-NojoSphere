import base64
import json
import os
import sqlite3
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime as datetime_cls
from http.server import ThreadingHTTPServer
from unittest import mock

from backend import ai_advisor, agri_ai
from backend.server import (AgriSenseApp, AgriSenseHTTPServer, InsightEngine, ReadingStore, Thresholds,
                            build_handler, validate_reading, validate_site_location)
from backend.suitability import SiteSuitabilityEngine


# A stand-in AI core: lets the API and job-queue behaviour be tested
# deterministically on a machine without PyTorch, without pretending the real
# pipeline ran.
class FakeAICore:
    def __init__(self, capabilities, run_pipeline=None):
        self._capabilities = capabilities
        self._run_pipeline = run_pipeline

    def capabilities(self):
        return dict(self._capabilities)

    def run_pipeline(self, **kwargs):
        if self._run_pipeline is None:
            raise AssertionError("run_pipeline should not be called")
        return self._run_pipeline(**kwargs)


RUNNABLE_CAPS = {
    "torch_available": True, "torch_error": None,
    "vision_available": True, "vision_error": None,
    "openai_client_available": False, "missing_packages": [],
    "pipeline_runnable": True, "notes": ["fake core for tests"],
}
BLOCKED_CAPS = dict(RUNNABLE_CAPS, torch_available=False, pipeline_runnable=False,
                    missing_packages=["torch"])


class BackendIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.tempdir.name, "test.sqlite3")
        self.app = AgriSenseApp(database_url=db_path, device_timeout=30, allowed_origins=["http://localhost:3000", "null"])
        self.server = AgriSenseHTTPServer(("127.0.0.1", 0), build_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.app.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, path, method="GET", data=None):
        raw = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(self.base + path, data=raw, method=method,
                                         headers={"Content-Type": "application/json"} if raw is not None else {})
        with urllib.request.urlopen(request, timeout=3) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None

    def test_suitability_engine_scores_site_and_returns_economic_scenarios(self):
        engine = SiteSuitabilityEngine()
        payload = {
            "site": {"name": "Test North", "area_ha": 2},
            "layers": {"solar_kwh_m2_day": 6.2, "annual_rainfall_mm": 750, "soil_ph": 6.4,
                       "soil_salinity_ds_m": 1.2, "wind_kmh": 18, "dust_risk": 20,
                       "water_availability": 75, "mean_temp_c": 24, "slope_pct": 2,
                       "market_distance_km": 35},
            "economics": {"currency": "USD", "budget": 20000, "price_per_ton": 320,
                          "systems": {"open_field": {"capex_per_ha": 1000, "opex_per_ha": 1200},
                                      "greenhouse": {"capex_per_ha": 10000, "opex_per_ha": 3500},
                                      "hydroponic": {"capex_per_ha": 15000, "opex_per_ha": 4000}}},
            "layer_sources": ["north-field-summary.csv"],
        }
        payload["site"]["location"] = {"latitude": 51.5, "longitude": -0.12, "accuracy_m": 5}
        payload["site"]["location_captured_at"] = "2026-09-26T12:00:00Z"
        result = engine.recommend(payload)
        self.assertEqual(result["site"]["name"], "Test North")
        self.assertEqual(result["site"]["location"]["latitude"], 51.5)
        self.assertEqual(result["site"]["location"]["longitude"], -0.12)
        self.assertEqual(result["layer_completeness_percent"], 100)
        self.assertTrue(result["recommendations"])
        for recommendation in result["recommendations"]:
            self.assertIn(recommendation["suitability_score"], range(101))
            self.assertEqual(set(recommendation["scenarios"]), {"conservative", "base", "optimistic"})
            self.assertEqual(recommendation["cost_estimates"]["budget"], 20000)
            self.assertGreaterEqual(recommendation["confidence"], 0)
        self.assertEqual(result["recommendations"][0]["production_system"]["id"], "open_field")
        base = result["recommendations"][0]["scenarios"]["base"]
        self.assertIn("startup_capex", base)
        self.assertIn("annual_opex", base)
        self.assertIn("payback_years", base)
        self.assertEqual(result["recommendations"][0]["cost_estimates"]["funding_gap"], 0)
        payload["site"]["location"] = {"latitude": 91, "longitude": -0.12, "accuracy_m": 5}
        with self.assertRaisesRegex(ValueError, "site.location.latitude"):
            engine.recommend(payload)
        payload["site"]["location"] = {"latitude": 51.5, "longitude": -0.12, "accuracy_m": 5}
        payload["site"]["location_captured_at"] = "not-a-date"
        with self.assertRaisesRegex(ValueError, "site.location_captured_at"):
            engine.recommend(payload)

    def test_suitability_budget_fit_is_reported_and_not_hidden(self):
        engine = SiteSuitabilityEngine()
        payload = {"site": {"name": "Small plot", "area_ha": 1},
                   "layers": {"solar_kwh_m2_day": 6, "soil_ph": 6.5, "soil_salinity_ds_m": 1,
                              "dust_risk": 10, "water_availability": 70, "market_distance_km": 20},
                   "economics": {"currency": "USD", "budget": 10, "price_per_ton": 100,
                                 "systems": {"open_field": {"capex_per_ha": 100, "opex_per_ha": 50},
                                             "greenhouse": {"capex_per_ha": 1000, "opex_per_ha": 200},
                                             "hydroponic": {"capex_per_ha": 2000, "opex_per_ha": 300}}}}
        result = engine.recommend(payload)
        self.assertTrue(all(not item["cost_estimates"]["budget_fit"] for item in result["recommendations"]))
        self.assertGreater(result["recommendations"][0]["suitability_score"], 0)
        self.assertEqual(result["recommendations"][0]["cost_estimates"]["funding_gap"], 90)

    def test_suitability_rejects_bad_or_missing_inputs(self):
        with self.assertRaises(ValueError):
            SiteSuitabilityEngine().recommend({"site": {}, "layers": {}, "economics": {}})
        payload = {"site": {"name": "Test", "area_ha": 1},
                   "layers": {"solar_kwh_m2_day": 6, "soil_ph": 6.5, "soil_salinity_ds_m": 1,
                              "dust_risk": 10, "water_availability": 70, "market_distance_km": 20},
                   "economics": {"currency": "USD", "budget": 1000, "price_per_ton": 100,
                                 "systems": {"open_field": {"capex_per_ha": -1, "opex_per_ha": 50},
                                             "greenhouse": {"capex_per_ha": 100, "opex_per_ha": 50},
                                             "hydroponic": {"capex_per_ha": 100, "opex_per_ha": 50}}}}
        with self.assertRaises(ValueError):
            SiteSuitabilityEngine().recommend(payload)

    def test_suitability_api_schema_and_recommendations(self):
        _, schema = self.request("/api/suitability/schema")
        self.assertIn("market_distance_km", schema["required_layers"])
        _, result = self.request("/api/suitability/recommendations", "POST", {
            "site": {"name": "API plot", "area_ha": 1,
                     "location": {"latitude": 51.5, "longitude": -0.12, "accuracy_m": 5},
                     "location_captured_at": "2026-09-26T12:00:00Z"},
            "layers": {"solar_kwh_m2_day": 6, "soil_ph": 6.5, "soil_salinity_ds_m": 1,
                       "wind_kmh": 12, "water_availability": 70, "market_distance_km": 20},
            "economics": {"currency": "USD", "budget": 2000, "price_per_ton": 100,
                          "systems": {"open_field": {"capex_per_ha": 500, "opex_per_ha": 300},
                                      "greenhouse": {"capex_per_ha": 3000, "opex_per_ha": 700},
                                      "hydroponic": {"capex_per_ha": 4000, "opex_per_ha": 800}}},
        })
        self.assertGreater(len(result["recommendations"]), 0)
        self.assertEqual(result["site"]["name"], "API plot")
        self.assertEqual(result["site"]["location"]["latitude"], 51.5)
        self.assertEqual(result["site"]["location"]["captured_at"], "2026-09-26T12:00:00Z")
        bad_time_payload = {
            "site": {"name": "API plot", "area_ha": 1, "location": {"latitude": 51.5, "longitude": -0.12, "accuracy_m": 5}, "location_captured_at": "not-a-date"},
            "layers": {"solar_kwh_m2_day": 6, "soil_ph": 6.5, "soil_salinity_ds_m": 1, "wind_kmh": 12, "water_availability": 70, "market_distance_km": 20},
            "economics": {"currency": "USD", "budget": 2000, "price_per_ton": 100,
                          "systems": {"open_field": {"capex_per_ha": 500, "opex_per_ha": 300},
                                      "greenhouse": {"capex_per_ha": 3000, "opex_per_ha": 700},
                                      "hydroponic": {"capex_per_ha": 4000, "opex_per_ha": 800}}},
        }
        request = urllib.request.Request(self.base + "/api/suitability/recommendations", data=json.dumps(bad_time_payload).encode(),
                                         method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(rejected.exception.code, 400)
        rejected.exception.close()

    def test_suitability_requires_a_wind_or_dust_risk_layer(self):
        payload = {"site": {"name": "No wind data", "area_ha": 1},
                   "layers": {"solar_kwh_m2_day": 6, "soil_ph": 6.5, "soil_salinity_ds_m": 1,
                              "water_availability": 70, "market_distance_km": 20},
                   "economics": {"currency": "USD", "budget": 2000, "price_per_ton": 100,
                                 "systems": {"open_field": {"capex_per_ha": 500, "opex_per_ha": 300},
                                             "greenhouse": {"capex_per_ha": 800, "opex_per_ha": 500},
                                             "hydroponic": {"capex_per_ha": 1000, "opex_per_ha": 600}}}}
        with self.assertRaisesRegex(ValueError, "wind/dust"):
            SiteSuitabilityEngine().recommend(payload)

    def test_site_location_validation_and_offline_store_api(self):
        location = {"site_id": "site_12345678", "site_name": "North Field", "latitude": 51.5072,
                    "longitude": -0.1276, "accuracy_m": 8.5, "captured_at": "2026-09-26T12:30:00Z"}
        validated = validate_site_location(location)
        self.assertEqual(validated["latitude"], 51.5072)
        self.assertEqual(validated["captured_at"], "2026-09-26T12:30:00.000Z")
        for invalid in (
            {**location, "latitude": 91},
            {**location, "longitude": -181},
            {**location, "accuracy_m": -1},
            {**location, "site_id": "bad/id"},
            {**location, "latitude": True},
        ):
            with self.assertRaises(ValueError):
                validate_site_location(invalid)
        _, saved = self.request("/api/site-location", "POST", location)
        self.assertEqual(saved["location"]["site_id"], location["site_id"])
        _, restored = self.request(f"/api/site-location/{location['site_id']}")
        self.assertEqual(restored["location"]["longitude"], location["longitude"])
        invalid = dict(location, latitude=91)
        request = urllib.request.Request(self.base + "/api/site-location", data=json.dumps(invalid).encode(),
                                         method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(rejected.exception.code, 400)
        rejected.exception.close()
        _, removed = self.request(f"/api/site-location/{location['site_id']}", "DELETE")
        self.assertTrue(removed["deleted"])
        with self.assertRaises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(self.base + f"/api/site-location/{location['site_id']}", timeout=3)
        self.assertEqual(missing.exception.code, 404)
        missing.exception.close()

    def test_validation_and_optional_sensor_fields(self):
        payload = {"device": "ESP32-01", "soil_moisture": 50, "temperature": 28,
                   "humidity": 62.5, "soil_ph": 6.4, "water_level": 74, "battery": 94,
                   "light": 820, "pressure": 1012, "custom_sensor": 12}
        result = validate_reading(payload)
        self.assertEqual(result["device"], "ESP32-01")
        self.assertEqual(result["extras"], {"custom_sensor": 12.0})
        for invalid in (
            {"device": "ESP32-01", "soil_moisture": "50", "temperature": 28},
            {"device": "ESP32-01", "soil_moisture": 101, "temperature": 28},
            {"device": "bad id", "soil_moisture": 50, "temperature": 28},
            {"device": "ESP32-01", "soil_moisture": 10**400, "temperature": 28},
        ):
            with self.assertRaises(ValueError):
                validate_reading(invalid)

    def test_in_memory_database_persists_across_short_lived_connections(self):
        store = ReadingStore(":memory:")
        self.addCleanup(store.close)
        payload = validate_reading({"device": "memory-device", "soil_moisture": 50, "temperature": 28})
        store.insert(payload, "esp32")
        self.assertEqual(store.count_readings("memory-device"), 1)
        store.close()
        with self.assertRaises(sqlite3.OperationalError):
            store.count_readings("memory-device")

    def test_database_file_survives_store_restart(self):
        path = os.path.join(self.tempdir.name, "restart.sqlite3")
        store = ReadingStore(path)
        payload = validate_reading({"device": "restart-device", "soil_moisture": 52, "temperature": 27})
        store.insert(payload, "esp32")
        store.close()
        restarted = ReadingStore(path)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.count_readings("restart-device"), 1)

    def test_concurrent_devices_keep_readings_separate(self):
        barrier = threading.Barrier(10)
        failures = []
        def ingest(index):
            try:
                barrier.wait(timeout=3)
                self.app.ingest({"device": f"parallel-{index}", "soil_moisture": 20 + index,
                                 "temperature": 20 + index, "timestamp": f"2026-09-25T12:00:{index:02d}Z"})
            except Exception as exc:
                failures.append(exc)
        workers = [threading.Thread(target=ingest, args=(index,)) for index in range(10)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertFalse(failures)
        for index in range(10):
            rows = self.app.store.list_readings(device=f"parallel-{index}")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["soil_moisture"], 20 + index)

    def test_offline_transition_recovered_from_sqlite_after_restart(self):
        reading = {"device": "persisted-status", "soil_moisture": 50, "temperature": 28,
                   "timestamp": "2026-09-25T12:00:00Z"}
        self.app.ingest(reading)
        self.app._statuses.clear()
        messages = []
        original = self.app._broadcast
        self.app._broadcast = messages.append
        try:
            self.app.device_timeout = 1
            time.sleep(1.02)
            self.app.list_devices()
        finally:
            self.app._broadcast = original
        self.assertIn({"type": "device_status", "device": "persisted-status", "status": "OFFLINE"}, messages)

    def test_metric_latest_timestamp_uses_latest_non_null_value(self):
        self.app.ingest({"device": "metric-device", "soil_moisture": 50, "temperature": 28,
                         "humidity": 60, "timestamp": "2026-09-25T10:00:00Z"})
        self.app.ingest({"device": "metric-device", "soil_moisture": 51, "temperature": 29,
                         "timestamp": "2026-09-25T11:00:00Z"})
        result = self.app.stats("metric-device", "30d")
        self.assertEqual(result["metrics"]["humidity"]["current"], 60)
        self.assertEqual(result["metrics"]["humidity"]["latest_timestamp"], "2026-09-25T10:00:00.000Z")

    def test_source_is_restricted_to_known_ingestion_paths(self):
        with self.assertRaises(ValueError):
            self.app.ingest({"device": "source-device", "soil_moisture": 50, "temperature": 28}, "unknown")
        with self.assertRaises(ValueError):
            self.app.ingest({"device": "source-device", "soil_moisture": 50, "temperature": 28,
                             "source": "simulation"}, "esp32")
        with self.assertRaises(ValueError):
            self.app.ingest({"device": "ESP32-01", "soil_moisture": 50, "temperature": 28}, "simulation")
        with self.assertRaises(ValueError):
            self.app.ingest({"device": "sim-fake", "soil_moisture": 50, "temperature": 28}, "esp32")

    def test_duplicate_reading_does_not_change_stored_data_or_duplicate_count(self):
        reading = {"device": "dedupe-device", "soil_moisture": 40, "temperature": 28,
                   "timestamp": "2026-09-25T12:00:00Z"}
        first = self.app.ingest(reading)
        later = self.app.ingest({**reading, "soil_moisture": 90})
        self.assertFalse(first["duplicate"])
        self.assertTrue(later["duplicate"])
        self.assertEqual(later["reading"]["soil_moisture"], 40)
        self.assertEqual(self.app.store.count_readings("dedupe-device"), 1)

    def test_database_failure_returns_503_and_does_not_fake_reading_success(self):
        original_insert = self.app.store.insert
        self.app.store.insert = lambda *args, **kwargs: (_ for _ in ()).throw(__import__("sqlite3").OperationalError("disk unavailable"))
        try:
            request = urllib.request.Request(self.base + "/api/readings",
                data=json.dumps({"device": "db-failure", "soil_moisture": 50, "temperature": 28}).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as failure:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(failure.exception.code, 503)
            payload = json.loads(failure.exception.read())
            self.assertEqual(payload["error"]["code"], "SERVICE_UNAVAILABLE")
            self.assertNotIn("traceback", payload)
            failure.exception.close()
        finally:
            self.app.store.insert = original_insert
        self.assertEqual(self.app.store.count_readings("db-failure"), 0)

    def test_invalid_sensor_bounds_and_json_constants_are_rejected(self):
        for invalid in (
            {"device": "range-device", "soil_moisture": 50, "temperature": 28, "humidity": float("nan")},
            {"device": "range-device", "soil_moisture": 50, "temperature": 28, "humidity": 101},
            {"device": "range-device", "soil_moisture": 50, "temperature": 28, "soil_ph": 14.1},
            {"device": "range-device", "soil_moisture": None, "temperature": 28},
            {"device": "range-device", "soil_moisture": 50, "temperature": "undefined"},
        ):
            with self.assertRaises(ValueError):
                validate_reading(invalid)
        request = urllib.request.Request(self.base + "/api/readings", data=b'{"device":"nan-device","soil_moisture":NaN,"temperature":28}',
                                         headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(rejected.exception.code, 400)
        payload = json.loads(rejected.exception.read())
        self.assertEqual(payload["error"]["code"], "INVALID_REQUEST")
        rejected.exception.close()

    def test_environment_sensor_ranges_can_be_configured(self):
        prior = os.environ.get("SENSOR_TEMPERATURE_MIN")
        os.environ["SENSOR_TEMPERATURE_MIN"] = "-30"
        try:
            self.assertEqual(validate_reading({"device": "range-device", "soil_moisture": 50,
                                               "temperature": -25})["temperature"], -25)
        finally:
            if prior is None:
                os.environ.pop("SENSOR_TEMPERATURE_MIN", None)
            else:
                os.environ["SENSOR_TEMPERATURE_MIN"] = prior

    def test_repeated_duplicate_packet_still_refreshes_device_last_seen(self):
        timestamp = "2026-09-25T12:00:00Z"
        reading = {"device": "reconnect-device", "soil_moisture": 50, "temperature": 28, "timestamp": timestamp}
        self.app.ingest(reading)
        before = self.app.store.device("reconnect-device")["last_seen"]
        time.sleep(0.01)
        self.app.ingest(reading)
        after = self.app.store.device("reconnect-device")["last_seen"]
        self.assertGreaterEqual(after, before)

    def test_wildcard_cors_does_not_claim_credential_support(self):
        original = self.app.allowed_origins
        self.app.allowed_origins = {"*"}
        self.addCleanup(setattr, self.app, "allowed_origins", original)
        request = urllib.request.Request(self.base + "/api/health", headers={"Origin": "http://example.test"})
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
            self.assertIsNone(response.headers.get("Access-Control-Allow-Credentials"))

    def test_simulation_requires_simulation_device_namespace(self):
        with self.assertRaises(ValueError):
            self.app.simulation.start("ESP32-01", 0.5)
        self.assertFalse(self.app.simulation.running)

    def test_device_timestamp_does_not_accept_extreme_future_reading(self):
        with self.assertRaises(ValueError):
            validate_reading({"device": "clock-device", "soil_moisture": 50, "temperature": 28,
                              "timestamp": "2099-01-01T00:00:00Z"})

    def test_insights_use_newest_reading_and_are_order_independent(self):
        thresholds = Thresholds()
        newest_first = [
            {"device": "dry-field", "timestamp": "2026-09-25T12:02:00Z", "soil_moisture": 20, "temperature": 31},
            {"device": "dry-field", "timestamp": "2026-09-25T12:01:00Z", "soil_moisture": 21, "temperature": 29},
            {"device": "dry-field", "timestamp": "2026-09-25T12:00:00Z", "soil_moisture": 22, "temperature": 28},
        ]
        newest_results = InsightEngine.evaluate(newest_first, thresholds)
        oldest_results = InsightEngine.evaluate(list(reversed(newest_first)), thresholds)
        self.assertEqual([item["type"] for item in newest_results],
                         [item["type"] for item in oldest_results])
        self.assertIn("IRRIGATION_RECOMMENDED", {item["type"] for item in newest_results})
        self.assertIn("TEMPERATURE_RISING", {item["type"] for item in newest_results})
        self.assertTrue(all(item["device"] == "dry-field" and item["timestamp"] == newest_first[0]["timestamp"]
                            for item in newest_results))

    def test_insights_do_not_mix_multiple_devices(self):
        groups = {
            "dry-field": [{"device": "dry-field", "timestamp": "2026-09-25T12:00:00Z",
                           "soil_moisture": 20, "temperature": 25}],
            "wet-field": [{"device": "wet-field", "timestamp": "2026-09-25T12:00:00Z",
                           "soil_moisture": 60, "temperature": 25}],
        }
        insights = InsightEngine.evaluate_devices(groups, Thresholds())
        irrigation = [item for item in insights if item["type"] == "IRRIGATION_RECOMMENDED"]
        self.assertEqual([item["device"] for item in irrigation], ["dry-field"])

    def test_insights_api_scopes_results_by_device(self):
        for reading in (
            {"device": "dry-field", "soil_moisture": 20, "temperature": 25,
             "timestamp": "2026-09-25T12:00:00Z"},
            {"device": "wet-field", "soil_moisture": 60, "temperature": 25,
             "timestamp": "2026-09-25T12:00:00Z"},
        ):
            self.request("/api/readings", "POST", reading)
        _, all_fields = self.request("/api/insights")
        irrigation = [item for item in all_fields["insights"] if item["type"] == "IRRIGATION_RECOMMENDED"]
        self.assertEqual([item["device"] for item in irrigation], ["dry-field"])
        _, one_field = self.request("/api/insights?device=wet-field")
        self.assertTrue(all(item["device"] == "wet-field" for item in one_field["insights"]))
        self.assertNotIn("IRRIGATION_RECOMMENDED", {item["type"] for item in one_field["insights"]})

    def test_offline_then_reconnect_broadcasts_status_transition(self):
        sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        sock.settimeout(2)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        sock.sendall(request.encode())
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(sock.recv(1024))
        header_end = response.index(b"\r\n\r\n") + 4
        buffered = bytearray(response[header_end:])
        def read_message():
            while len(buffered) < 2:
                buffered.extend(sock.recv(1024))
            frame = bytes(buffered[:2])
            del buffered[:2]
            length = frame[1] & 0x7F
            if length == 126:
                while len(buffered) < 2:
                    buffered.extend(sock.recv(1024))
                length = int.from_bytes(buffered[:2], "big")
                del buffered[:2]
            while len(buffered) < length:
                buffered.extend(sock.recv(1024))
            data = bytes(buffered[:length])
            del buffered[:length]
            return json.loads(data)
        try:
            self.assertEqual(read_message()["type"], "connection_status")
            self.request("/api/readings", "POST", {"device": "transition-device", "soil_moisture": 50,
                                                       "temperature": 28})
            online = None
            while online is None:
                event = read_message()
                if event["type"] == "device_status" and event["status"] == "ONLINE":
                    online = event
            # Poll instead of assuming a fixed sleep outruns the timeout: under a loaded
            # machine the reading can land later than the sleep allows for.
            self.app.device_timeout = 1
            offline = None
            deadline = time.monotonic() + 6
            sock.settimeout(0.25)
            while offline is None and time.monotonic() < deadline:
                self.app.list_devices()
                try:
                    event = read_message()
                except (TimeoutError, socket.timeout):
                    continue
                if event["type"] == "device_status" and event["status"] == "OFFLINE":
                    offline = event
            sock.settimeout(2)
            self.assertIsNotNone(offline, "device never reported OFFLINE after its timeout elapsed")
            self.request("/api/readings", "POST", {"device": "transition-device", "soil_moisture": 51,
                                                       "temperature": 28})
            reconnected = [read_message() for _ in range(3)]
            self.assertIn("ONLINE", [item.get("status") for item in reconnected if item["type"] == "device_status"])
        finally:
            sock.close()

    def test_reading_api_persists_and_reports_stats_alerts(self):
        reading = {"device": "ESP32-01", "soil_moisture": 20, "temperature": 36,
                   "water_level": 10, "timestamp": "2026-09-25T17:45:45Z"}
        status, result = self.request("/api/readings", "POST", reading)
        self.assertEqual(status, 201)
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["reading"]["source"], "esp32")

        status, latest = self.request("/api/readings/latest?device=ESP32-01")
        self.assertEqual(status, 200)
        self.assertEqual(latest["reading"]["soil_moisture"], 20)
        _, history = self.request("/api/readings/ESP32-01")
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["readings"][0]["device"], "ESP32-01")

        _, devices = self.request("/api/devices")
        self.assertEqual(devices["devices"][0]["status"], "ONLINE")
        _, device = self.request("/api/devices/ESP32-01")
        self.assertEqual(device["latest_reading"]["temperature"], 36)
        _, alerts = self.request("/api/alerts")
        self.assertEqual({item["type"] for item in alerts["alerts"]},
                         {"LOW_SOIL_MOISTURE", "HIGH_TEMPERATURE", "LOW_WATER_LEVEL"})
        _, stats = self.request("/api/stats?range=24h&device=ESP32-01")
        self.assertEqual(stats["metrics"]["soil_moisture"]["current"], 20)
        self.assertEqual(stats["metrics"]["soil_moisture"]["reading_count"], 1)
        _, health = self.request("/api/health")
        self.assertEqual(health["status"], "ok")

    def test_invalid_http_payload_is_rejected_without_stopping_server(self):
        request = urllib.request.Request(self.base + "/api/readings", data=b"{bad json",
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 400)
            error.close()
        else:
            self.fail("invalid JSON should return 400")
        _, health = self.request("/api/health")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["database"], "connected")
        self.assertIn("uptime_seconds", health)
        self.assertEqual(health["connectedFrontendClients"], 0)

    def test_device_timeout_marks_offline_and_creates_alert(self):
        self.request("/api/readings", "POST", {"device": "quiet-device", "soil_moisture": 50,
                                                  "temperature": 28})
        self.app.device_timeout = 1
        time.sleep(1.05)
        _, device = self.request("/api/devices/quiet-device")
        self.assertEqual(device["status"], "OFFLINE")
        _, alerts = self.request("/api/alerts")
        self.assertIn("DEVICE_OFFLINE", {item["type"] for item in alerts["alerts"]})

    def test_simulation_uses_same_store_and_pipeline(self):
        self.assertTrue(self.app.simulation.start("sim-test", 0.5))
        deadline = time.monotonic() + 3
        readings = []
        while time.monotonic() < deadline:
            readings = self.app.store.list_readings(device="sim-test")
            if readings:
                break
            time.sleep(0.03)
        self.assertTrue(readings, "simulator should store a reading")
        self.assertEqual(readings[0]["source"], "simulation")
        self.assertGreaterEqual(readings[0]["soil_moisture"], 45)
        self.assertLessEqual(readings[0]["soil_moisture"], 65)
        self.assertTrue(self.app.simulation.stop())

    def test_websocket_broadcasts_ingested_reading(self):
        sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        sock.settimeout(2)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        sock.sendall(request.encode())
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(sock.recv(1024))
        header, buffered = bytes(response).split(b"\r\n\r\n", 1)
        self.assertIn(b"101 Switching Protocols", header)

        def read_frame():
            nonlocal buffered
            def read_exact(size):
                nonlocal buffered
                while len(buffered) < size:
                    buffered += sock.recv(1024)
                result, buffered = buffered[:size], buffered[size:]
                return result
            header = read_exact(2)
            length = header[1] & 0x7F
            if length == 126:
                length = int.from_bytes(read_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(read_exact(8), "big")
            return json.loads(read_exact(length).decode())

        try:
            welcome = read_frame()
            self.assertEqual(welcome["type"], "connection_status")
            self.request("/api/readings", "POST", {"device": "ws-device", "soil_moisture": 50,
                                                       "temperature": 28})
            messages = [read_frame() for _ in range(3)]
            reading_message = next(message for message in messages if message["type"] == "sensor_reading")
            self.assertEqual(reading_message["data"]["device"], "ws-device")
        finally:
            sock.close()
            deadline = time.monotonic() + 2
            while self.app.peer_count and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(self.app.peer_count, 0)

    def test_esp32_can_push_readings_over_the_device_websocket(self):
        sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        sock.settimeout(2)
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET /ws?role=device HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        sock.sendall(request.encode())
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(sock.recv(1024))
        header, buffered = bytes(response).split(b"\r\n\r\n", 1)
        self.assertIn(b"101 Switching Protocols", header)

        def read_exact(size):
            nonlocal buffered
            while len(buffered) < size:
                buffered += sock.recv(1024)
            result, buffered = buffered[:size], buffered[size:]
            return result

        def read_frame():
            head = read_exact(2)
            length = head[1] & 0x7F
            if length == 126:
                length = int.from_bytes(read_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(read_exact(8), "big")
            return json.loads(read_exact(length).decode())

        def send_text(text):
            data = text.encode()
            mask = os.urandom(4)
            if len(data) < 126:
                head = bytes((0x81, 0x80 | len(data)))
            else:
                head = bytes((0x81, 0x80 | 126)) + len(data).to_bytes(2, "big")
            sock.sendall(head + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(data)))

        try:
            welcome = read_frame()
            self.assertEqual(welcome["type"], "welcome")
            self.assertEqual(welcome["role"], "device")
            self.assertEqual(self.app.peer_count, 0, "a device socket must not join the browser broadcast peers")

            send_text(json.dumps({"device": "esp32-ws-01", "soil_moisture": 42, "temperature": 26.5,
                                  "humidity": 61, "timestamp": "2026-09-26T09:00:00Z"}))
            ack = read_frame()
            self.assertEqual(ack["type"], "ack")
            self.assertEqual(ack["device"], "esp32-ws-01")
            self.assertFalse(ack["duplicate"])

            _status, body = self.request("/api/readings/esp32-ws-01?limit=1")
            self.assertEqual(body["readings"][0]["soil_moisture"], 42)
            self.assertEqual(body["readings"][0]["source"], "esp32")

            send_text(json.dumps({"device": "esp32-ws-01"}))
            rejected = read_frame()
            self.assertEqual(rejected["type"], "error")
            self.assertEqual(rejected["code"], "INVALID_READING")

            send_text("not json at all")
            malformed = read_frame()
            self.assertEqual(malformed["type"], "error")
            self.assertEqual(malformed["code"], "INVALID_JSON")
        finally:
            sock.close()

    def test_multiple_websocket_clients_ping_pong_disconnect_and_reconnect(self):
        def connect():
            sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
            sock.settimeout(2)
            key = base64.b64encode(os.urandom(16)).decode()
            request = ("GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                       f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
            sock.sendall(request.encode())
            response = bytearray()
            while b"\r\n\r\n" not in response:
                response.extend(sock.recv(1024))
            header, buffered = bytes(response).split(b"\r\n\r\n", 1)
            self.assertIn(b"101 Switching Protocols", header)
            return sock, buffered

        clients = [connect(), connect()]
        try:
            deadline = time.monotonic() + 2
            while self.app.peer_count != 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(self.app.peer_count, 2)
            for client, buffered in clients:
                while len(buffered) < 2:
                    buffered += client.recv(1024)
                header, buffered = buffered[:2], buffered[2:]
                self.assertEqual(header[0] & 0x0F, 0x1)
                length = header[1] & 0x7F
                if length == 126:
                    while len(buffered) < 2:
                        buffered += client.recv(1024)
                    length, buffered = int.from_bytes(buffered[:2], "big"), buffered[2:]
                while len(buffered) < length:
                    buffered += client.recv(1024)
                body, buffered = buffered[:length], buffered[length:]
                self.assertEqual(json.loads(body)["type"], "connection_status")
            mask = b"abcd"
            client_frame = bytes((0x89, 0x80)) + mask
            clients[0][0].sendall(client_frame)
            pong_header = clients[0][0].recv(2)
            self.assertEqual(pong_header[0] & 0x0F, 0xA)
            clients[0][0].close()
            clients = clients[1:]
            deadline = time.monotonic() + 2
            while self.app.peer_count != 1 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(self.app.peer_count, 1)
        finally:
            for client, _ in clients:
                client.close()
        reconnect, _ = connect()
        try:
            deadline = time.monotonic() + 2
            while self.app.peer_count != 1 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(self.app.peer_count, 1)
        finally:
            reconnect.close()

    def test_dashboard_assets_are_served_by_same_backend(self):
        for path, expected in (("/", b"AgriSense AI"),("/styles.css", b"--green: #6e964d"),
                               ("/app.js", b"/api/simulation/"),("/suitability.js", b"suitability-form"),
                               ("/ParticleText.js", b"window.ParticleText"),("/ParticleText.css", b".particle-text"),
                               ("/ScrollExpand.js", b"window.ScrollExpand"),("/ScrollExpand.css", b".scroll-expand__frame"),
                               ("/views.js", b"data-view"),("/field-showcase.svg", b"<svg"),("/crops/leaf.svg", b"<svg"),
                               ("/crops/maize.svg", b"<svg")):
            with urllib.request.urlopen(self.base + path, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(expected, response.read())
        with urllib.request.urlopen(self.base + "/", timeout=3) as response:
            page = response.read()
        self.assertIn(b'href="styles.css"', page)
        self.assertIn(b'src="app.js"', page)
        self.assertIn(b'src="suitability.js"', page)
        self.assertIn(b'href="ParticleText.css"', page)
        self.assertIn(b'src="ParticleText.js"', page)
        self.assertIn(b'id="hero-particle-text"', page)
        self.assertIn(b'data-particle-text', page)
        self.assertIn(b'href="ScrollExpand.css"', page)
        self.assertIn(b'src="ScrollExpand.js"', page)
        self.assertIn(b'data-scroll-expand', page)
        self.assertIn(b'id="field-showcase"', page)
        self.assertIn(b'id="crop-library"', page)
        self.assertIn(b'src="crops/leaf.svg"', page)
        self.assertIn(b'class="topnav"', page)
        self.assertIn(b'src="views.js"', page)
        self.assertIn(b'data-view="overview"', page)
        self.assertIn(b'data-view-group', page)
        self.assertIn(b'id="simulate-button"', page)
        self.assertIn(b'id="refresh-devices"', page)
        self.assertIn(b'id="action-dialog"', page)
        self.assertIn(b'id="capture-location"', page)
        self.assertIn(b'id="map-view"', page)
        self.assertIn(b'id="map-load-online"', page)
        self.assertIn(b'id="map-capture-location"', page)
        self.assertIn(b'id="map-zoom-in"', page)
        self.assertIn(b'id="map-zoom-out"', page)
        self.assertIn(b'class="mobile-nav"', page)
        self.assertIn(b'class="mobile-nav-link"', page)
        self.assertIn(b'aria-label="Open GPS map in a new tab"', page)
        self.assertIn(b'href="?view=map#map-view"', page)
        self.assertIn(b'id="location-status"', page)
        self.assertIn(b"Location privacy", page)
        self.assertIn(b'id="ai-advisor"', page)
        self.assertIn(b'data-view="ai-advisor"', page)
        self.assertIn(b'id="ai-form"', page)
        self.assertIn(b'id="ai-run"', page)
        self.assertIn(b'id="ai-unavailable"', page)
        self.assertIn(b'id="ai-results"', page)
        self.assertIn(b'src="ai-advisor.js"', page)
        with urllib.request.urlopen(self.base + "/app.js", timeout=3) as response:
            app_js = response.read()
        self.assertIn(b"devices-refresh", app_js)
        self.assertIn(b"exportReadingsCsv", app_js)
        self.assertIn(b"showMetricDetails", app_js)
        self.assertIn(b"event.key !== 'Tab'", app_js)
        self.assertIn(b"Stop demo readings", app_js)
        self.assertIn(b".mobile-nav-link.active", app_js)
        self.assertIn(b"data-range=", page)
        self.assertIn(b"data-metric=", page)
        with urllib.request.urlopen(self.base + "/suitability.js", timeout=3) as response:
            suitability_js = response.read()
        self.assertIn(b"clear-results", suitability_js)
        self.assertIn(b"getCurrentPosition", suitability_js)
        self.assertIn(b"renderGpsMap", suitability_js)
        self.assertIn(b"mapEmbedUrl", suitability_js)
        self.assertIn(b"mapOnlineEnabled", suitability_js)
        self.assertIn(b"addEventListener('offline'", suitability_js)
        self.assertIn(b"addEventListener('online'", suitability_js)
        self.assertIn(b"locationStorageKey", suitability_js)
        self.assertIn(b"event.key !== 'Enter'", suitability_js)
        with urllib.request.urlopen(self.base + "/ai-advisor.js", timeout=3) as response:
            advisor_js = response.read()
        self.assertIn(b"/api/ai/analyze", advisor_js)
        self.assertIn(b"/api/ai/status", advisor_js)
        self.assertIn(b"/api/ai/warmup", advisor_js)
        self.assertIn(b"ai/jobs/", advisor_js)
        self.assertIn(b"ai-banner-warn", advisor_js)
        with urllib.request.urlopen(self.base + "/styles.css", timeout=3) as response:
            styles = response.read()
        self.assertIn(b".action-dialog", styles)
        self.assertIn(b".ai-result-grid", styles)
        self.assertIn(b"@keyframes ai-spin", styles)
        self.assertIn(b".map-stage", styles)
        self.assertIn(b".map-offline-canvas", styles)
        self.assertIn(b"@media(max-width:760px)", styles)
        self.assertIn(b".mobile-nav { position: fixed", styles)
        self.assertIn(b"min-height: 50px", styles)
        with urllib.request.urlopen(self.base + "/app.js", timeout=3) as response:
            app_js = response.read()
        self.assertIn(b"soil-ring-progress", app_js)
        self.assertIn(b"series-live-point", app_js)
        self.assertIn(b"view-devices", app_js)

    def test_direct_file_origin_is_allowed_for_dashboard_api(self):
        request = urllib.request.Request(self.base + "/api/health", headers={"Origin": "null"})
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "null")

    def test_frontend_origin_and_missing_device_responses(self):
        request = urllib.request.Request(self.base + "/api/devices", headers={"Origin": "http://localhost:3000"})
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://localhost:3000")
        try:
            urllib.request.urlopen(self.base + "/api/devices/missing", timeout=3)
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 404)
            error.close()
        else:
            self.fail("missing device should return 404")

    def _websocket_handshake_head(self, origin):
        host = f"127.0.0.1:{self.server.server_port}"
        sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2)
        sock.settimeout(2)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (f"GET /ws HTTP/1.1\r\nHost: {host}\r\nOrigin: {origin}\r\nUpgrade: websocket\r\n"
                     f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        try:
            sock.sendall(handshake.encode())
            head = bytearray()
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                head.extend(chunk)
            return bytes(head)
        finally:
            sock.close()
            deadline = time.monotonic() + 2
            while self.app.peer_count and time.monotonic() < deadline:
                time.sleep(0.02)

    def test_dashboard_origin_can_open_its_own_websocket(self):
        """The dashboard is served by this very server, so the origin the browser sends
        on its own /ws upgrade must be accepted even though the ephemeral test port is
        absent from ALLOWED_ORIGINS. Regression: the default allow-list only names dev
        ports, so a refusal here left the live feed permanently offline."""
        origin = f"http://127.0.0.1:{self.server.server_port}"
        request = urllib.request.Request(self.base + "/api/health", headers={"Origin": origin})
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
        self.assertIn(b"101 Switching Protocols", self._websocket_handshake_head(origin))

    def test_unrelated_origin_is_still_refused_for_websockets_and_api(self):
        self.assertIn(b"403", self._websocket_handshake_head("http://evil.example"))
        request = urllib.request.Request(self.base + "/api/health", headers={"Origin": "http://evil.example"})
        try:
            urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 403)
            self.assertEqual(json.loads(error.read())["error"]["code"], "ORIGIN_NOT_ALLOWED")
            error.close()
        else:
            self.fail("an unrelated origin should not be able to call the API")

    # -- AI advisor API ---------------------------------------------------

    def _api(self, path, method="GET", data=None):
        """Like request(), but returns the error status and body instead of raising."""
        raw = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(self.base + path, data=raw, method=method,
                                         headers={"Content-Type": "application/json"} if raw is not None else {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
                return response.status, json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            body = error.read()
            error.close()
            return error.code, json.loads(body) if body else None

    def _wait_for_job(self, job_id, timeout=15):
        deadline = time.monotonic() + timeout
        job = None
        while time.monotonic() < deadline:
            status, job = self._api(f"/api/ai/jobs/{job_id}")
            self.assertEqual(status, 200)
            if job["status"] in ("succeeded", "failed"):
                return job
            time.sleep(0.05)
        self.fail(f"job {job_id} did not settle within {timeout}s (last seen {job})")

    def test_ai_status_reports_availability_consistently(self):
        status, body = self._api("/api/ai/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["available"], bool(body["torch_available"] and body["vision_available"]))
        self.assertEqual(body["available"], body["available"])
        for key in ("reason", "missing_packages", "model_state", "modes", "proxy_data_disclosure"):
            self.assertIn(key, body)
        self.assertEqual(body["modes"], ["plan", "health", "both"])
        # The proxy-training disclosure must never be quietly dropped.
        self.assertIn("synthetic proxy", body["proxy_data_disclosure"])
        if not body["available"]:
            self.assertTrue(body["missing_packages"])
            self.assertIn("pip install", body["reason"])

    def test_ai_status_survives_a_core_that_has_the_reasoning_client(self):
        """Regression: status() probed Ollama through an attribute that shadowed the
        method of the same name, so the endpoint 500'd on any machine that had the
        openai client installed -- i.e. the intended deployment."""
        with_client = dict(RUNNABLE_CAPS, openai_client_available=True, missing_packages=[])
        with mock.patch.object(ai_advisor, "_load_core", lambda: FakeAICore(with_client)):
            status, body = self._api("/api/ai/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["available"])
        self.assertTrue(body["reasoning_client_available"])
        self.assertIn("reasoning_server_reachable", body)
        self.assertIsInstance(body["reasoning_server_reachable"], bool)

    def test_ai_analyze_refuses_to_fake_a_result_without_its_dependencies(self):
        """When the core cannot run, the API must say so -- never queue a job that
        would come back with invented numbers."""
        blocked = FakeAICore(BLOCKED_CAPS)
        with mock.patch.object(ai_advisor, "_load_core", lambda: blocked):
            status, body = self._api("/api/ai/analyze", "POST", {
                "latitude": 24.86, "longitude": 46.72, "soil_moisture_pct": 38,
                "air_temp_c": 33.2, "air_humidity_pct": 45, "ldr_pct": 25,
            })
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "AI_UNAVAILABLE")
        self.assertIn("torch", body["error"]["message"])
        self.assertIn("pip install torch", body["error"]["message"])

    def test_ai_analyze_validates_inputs_before_queueing_any_work(self):
        def run(**kwargs):
            raise AssertionError("an invalid request must never reach the pipeline")

        runnable = FakeAICore(RUNNABLE_CAPS, run)
        base = {"latitude": 24.86, "longitude": 46.72, "soil_moisture_pct": 38,
                "air_temp_c": 33.2, "air_humidity_pct": 45, "ldr_pct": 25}
        cases = [
            ("latitude out of range", {**base, "latitude": 120}),
            ("longitude out of range", {**base, "longitude": -400}),
            ("non-numeric reading", {**base, "soil_moisture_pct": "wet"}),
            ("missing required reading", {k: v for k, v in base.items() if k != "ldr_pct"}),
            ("boolean where a number belongs", {**base, "camera_index": True}),
            ("non-integer camera index", {**base, "camera_index": 2.5}),
            ("unknown field", {**base, "soil_moisture": 38}),
            ("unknown mode", {**base, "mode": "everything"}),
            ("oversized budget", {**base, "budget": 1e12}),
            ("region with control characters", {**base, "region": "sahel\nrm -rf"}),
        ]
        with mock.patch.object(ai_advisor, "_load_core", lambda: runnable):
            for label, payload in cases:
                with self.subTest(case=label):
                    status, body = self._api("/api/ai/analyze", "POST", payload)
                    self.assertEqual(status, 400, label)
                    self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.app.ai.status()["jobs_queued_or_running"], 0)

    def test_ai_job_runs_to_completion_and_hides_internal_handles(self):
        def run(**kwargs):
            return {
                "message": "✅ No irrigation needed.",
                "ai_status": "fallback",
                "irrigation_decision": {"action": "skip", "reason": "sufficient"},
                "structured_intelligence": {"site_state": {"overall_suitability": 61.5}},
                "_forecast_log_id": 4242,
                "params_seen": kwargs["mode"],
            }

        runnable = FakeAICore(RUNNABLE_CAPS, run)
        with mock.patch.object(ai_advisor, "_load_core", lambda: runnable):
            status, submitted = self._api("/api/ai/analyze", "POST", {
                "latitude": 24.86, "longitude": 46.72, "soil_moisture_pct": 38,
                "air_temp_c": 33.2, "air_humidity_pct": 45, "ldr_pct": 25, "mode": "both",
            })
            self.assertEqual(status, 202)
            self.assertIn(submitted["status"], ("queued", "running"))
            self.assertTrue(submitted["job_id"].startswith("ai-"))
            job = self._wait_for_job(submitted["job_id"])
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result"]["params_seen"], "both")
        self.assertEqual(job["result"]["structured_intelligence"]["site_state"]["overall_suitability"], 61.5)
        # _forecast_log_id is an internal handle for record_actual_weather().
        self.assertNotIn("_forecast_log_id", job["result"])
        self.assertIsInstance(job["duration_seconds"], float)

    def test_ai_job_failure_is_reported_with_the_missing_package(self):
        def run(**kwargs):
            raise ai_advisor.AIUnavailable("PyTorch is required for the state model.", ["torch"])

        runnable = FakeAICore(RUNNABLE_CAPS, run)
        with mock.patch.object(ai_advisor, "_load_core", lambda: runnable):
            status, submitted = self._api("/api/ai/analyze", "POST", {
                "latitude": 1.0, "longitude": 2.0, "soil_moisture_pct": 20,
                "air_temp_c": 30, "air_humidity_pct": 50, "ldr_pct": 50,
            })
            self.assertEqual(status, 202)
            job = self._wait_for_job(submitted["job_id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "AI_DEPENDENCY_MISSING")
        self.assertEqual(job["error"]["missing_packages"], ["torch"])
        self.assertNotIn("result", job)

    def test_ai_job_failure_is_reported_as_a_run_error(self):
        def run(**kwargs):
            raise RuntimeError("camera exploded")

        runnable = FakeAICore(RUNNABLE_CAPS, run)
        with mock.patch.object(ai_advisor, "_load_core", lambda: runnable):
            _, submitted = self._api("/api/ai/analyze", "POST", {
                "latitude": 1.0, "longitude": 2.0, "soil_moisture_pct": 20,
                "air_temp_c": 30, "air_humidity_pct": 50, "ldr_pct": 50,
            })
            job = self._wait_for_job(submitted["job_id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "AI_RUN_FAILED")
        self.assertIn("camera exploded", job["error"]["message"])

    def test_ai_queue_refuses_work_beyond_capacity(self):
        gate = threading.Event()

        def run(**kwargs):
            gate.wait(timeout=15)
            return {"message": "done", "ai_status": "fallback"}

        runnable = FakeAICore(RUNNABLE_CAPS, run)
        payload = {"latitude": 1.0, "longitude": 2.0, "soil_moisture_pct": 20,
                   "air_temp_c": 30, "air_humidity_pct": 50, "ldr_pct": 50}
        with mock.patch.object(ai_advisor, "_load_core", lambda: runnable):
            self.app.ai._max_queued = 1
            try:
                status, first = self._api("/api/ai/analyze", "POST", payload)
                self.assertEqual(status, 202)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if self.app.ai.get(first["job_id"]).status == "running":
                        break
                    time.sleep(0.02)
                status, body = self._api("/api/ai/analyze", "POST", payload)
                self.assertEqual(status, 429)
                self.assertEqual(body["error"]["code"], "AI_BUSY")
            finally:
                gate.set()
                self._wait_for_job(first["job_id"])
                self.app.ai._max_queued = ai_advisor.MAX_QUEUED_JOBS

    def test_ai_job_lookup_rejects_unknown_and_malformed_ids(self):
        status, body = self._api("/api/ai/jobs/ai-does-not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "AI_JOB_NOT_FOUND")
        status, body = self._api("/api/ai/jobs/..%2F..%2Fetc%2Fpasswd")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_REQUEST")

    def test_ai_warmup_refuses_when_the_core_cannot_run(self):
        blocked = FakeAICore(BLOCKED_CAPS)
        with mock.patch.object(ai_advisor, "_load_core", lambda: blocked):
            status, body = self._api("/api/ai/warmup", "POST")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "AI_UNAVAILABLE")
        self.assertFalse(self.app.ai.status()["available"] is True and not BLOCKED_CAPS["pipeline_runnable"])


class AIPipelineLogicTests(unittest.TestCase):
    """The deterministic parts of the AI core that need no PyTorch: the irrigation
    safety controller, candidate retrieval, sensor checks and the colour analyzer."""

    def test_irrigation_controller_refuses_to_act_on_invalid_moisture(self):
        decision = agri_ai.irrigation_decision(None, 40.0, 0.1)
        self.assertEqual(decision["action"], "skip")
        self.assertIn("invalid", decision["reason"])
        decision = agri_ai.irrigation_decision(150.0, 40.0, 0.1)
        self.assertEqual(decision["action"], "skip")

    def test_irrigation_controller_overrides_a_suspicious_sensor_jump(self):
        flagged = {"suspicious": True, "flags": {"soil_moisture_pct": {"flagged": True}}}
        decision = agri_ai.irrigation_decision(10.0, 40.0, 0.0, rate_of_change_check=flagged)
        self.assertEqual(decision["action"], "skip")
        self.assertIn("physically plausible", decision["reason"])
        self.assertIn("not irrigated on", decision["reason"])
        # Without the rate-of-change flag the same reading irrigates normally.
        self.assertEqual(agri_ai.irrigation_decision(10.0, 40.0, 0.0)["action"], "irrigate")

    def test_irrigation_controller_holds_off_for_rain_and_irrigates_when_dry(self):
        self.assertEqual(agri_ai.irrigation_decision(20.0, 40.0, 0.9)["action"], "skip")
        self.assertEqual(agri_ai.irrigation_decision(55.0, 40.0, 0.0)["action"], "skip")
        decision = agri_ai.irrigation_decision(20.0, 40.0, 0.05)
        self.assertEqual(decision["action"], "irrigate")
        # Qwen's advice is recorded as advisory input, never executed.
        decision = agri_ai.irrigation_decision(20.0, 40.0, 0.05, qwen_irrigation_strategy={"approach": "flood"})
        self.assertEqual(decision["qwen_strategy_considered"], {"approach": "flood"})

    def test_rate_of_change_flags_physically_implausible_jumps(self):
        previous = {"soil_moisture_pct": 42.0, "air_temp_c": 30.0, "air_humidity_pct": 50.0}
        calm = agri_ai.check_sensor_rate_of_change({"soil_moisture_pct": 43.0, "air_temp_c": 30.1,
                                                    "air_humidity_pct": 51.0}, previous, 60)
        self.assertFalse(calm["suspicious"])
        jumped = agri_ai.check_sensor_rate_of_change({"soil_moisture_pct": 97.0, "air_temp_c": 30.1,
                                                      "air_humidity_pct": 51.0}, previous, 30)
        self.assertTrue(jumped["suspicious"])
        self.assertTrue(jumped["flags"]["soil_moisture_pct"]["flagged"])
        self.assertFalse(agri_ai.check_sensor_rate_of_change({"soil_moisture_pct": 97.0}, previous, None)["suspicious"])

    def test_candidate_retrieval_filters_by_region_and_scores_salinity_when_known(self):
        expected_sahel = [name for name, info in agri_ai.CROP_KNOWLEDGE_BASE.items() if "sahel" in info.get("regions", [])]
        sahel = agri_ai.retrieve_relevant_crops(32.0, "low", soil_ph=7.0, region="sahel", top_n=10)
        self.assertEqual(len(sahel), len(expected_sahel))
        for entry in sahel:
            self.assertIn("sahel", entry["regions"])
            self.assertGreaterEqual(entry["candidate_fit_score"], 0.0)
            self.assertLessEqual(entry["candidate_fit_score"], 1.0)
        # An unknown region must fall back to the whole knowledge base, not return nothing.
        self.assertEqual(len(agri_ai.retrieve_relevant_crops(32.0, "low", region="atlantis", top_n=3)), 3)
        salty = agri_ai.retrieve_relevant_crops(30.0, "low", soil_ph=7.5, top_n=5, soil_salinity="high")
        self.assertTrue(any(entry["salinity_tolerance"] in ("high", "moderate-high") for entry in salty))

    def test_time_context_flips_season_for_the_southern_hemisphere(self):
        january = datetime_cls(2026, 1, 15, 12, 0)
        north = agri_ai.get_time_context(24.86, january)
        south = agri_ai.get_time_context(-33.9, january)
        self.assertEqual(north["season"], "winter")
        self.assertEqual(south["season"], "summer")
        self.assertEqual(north["day_of_year"], 15)
        self.assertEqual(north["hemisphere"], "northern")

    def test_capabilities_never_claim_the_pipeline_runs_without_its_dependencies(self):
        caps = agri_ai.capabilities()
        self.assertEqual(caps["pipeline_runnable"], bool(caps["torch_available"] and caps["vision_available"]))
        for key in ("torch_available", "vision_available", "openai_client_available", "missing_packages", "notes"):
            self.assertIn(key, caps)
        if caps["pipeline_runnable"]:
            self.assertEqual(caps["missing_packages"], [])
        else:
            self.assertTrue(caps["missing_packages"])

    def test_neural_paths_fail_loudly_rather_than_silently_when_torch_is_absent(self):
        if agri_ai.TORCH_AVAILABLE:
            self.skipTest("torch is installed here; the missing-dependency path cannot be exercised")
        with self.assertRaises(agri_ai.AIDependencyError) as raised:
            agri_ai.predict_state(40.0, 30.0, 50.0, 25.0)
        self.assertIn("torch", raised.exception.missing)
        # The module itself must still import and expose its pure logic.
        self.assertIsNone(agri_ai.STATE_NN_MODEL)

    def test_plant_stress_rules_are_rule_based_and_uncertainty_scored(self):
        dry = agri_ai.plant_stress_rules({"green_pct": 40.0, "yellow_pct": 30.0, "brown_pct": 4.0,
                                          "necrotic_pct": 2.0, "other_pct": 24.0}, 20.0, 28.0)
        self.assertGreater(dry["water_stress_suspicion"], 0)
        self.assertEqual(dry["source"], "rule_based_estimate_not_neural_network")
        hot = agri_ai.plant_stress_rules({"green_pct": 40.0, "yellow_pct": 5.0, "brown_pct": 25.0,
                                          "necrotic_pct": 5.0, "other_pct": 25.0}, 50.0, 40.0)
        self.assertGreater(hot["heat_stress_suspicion"], 0)
        self.assertEqual(agri_ai.plant_stress_rules({"green_pct": 90.0, "yellow_pct": 2.0, "brown_pct": 2.0,
                                                     "necrotic_pct": 1.0, "other_pct": 5.0}, 55.0, 24.0)["disease_suspicion"], 0.0)

    def test_pixel_analyzer_separates_colour_classes(self):
        try:
            import cv2
            import numpy
        except ImportError:                                  # pragma: no cover - env dependent
            self.skipTest("opencv + numpy are not installed")
        image = numpy.zeros((80, 80, 3), dtype=numpy.uint8)
        image[:, :] = (60, 190, 70)                          # BGR: mostly green foliage
        analysis = agri_ai.PlantPixelAnalyzer().analyze(image)
        self.assertGreater(analysis["green_pct"], 50.0)
        total = analysis["green_pct"] + analysis["yellow_pct"] + analysis["brown_pct"] \
            + analysis["necrotic_pct"] + analysis["other_pct"]
        self.assertAlmostEqual(total, 100.0, delta=0.6)


if __name__ == "__main__":
    unittest.main()
