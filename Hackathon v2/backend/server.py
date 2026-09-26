#!/usr/bin/env python3
"""AgriSense AI local-LAN backend. Uses only the Python standard library."""

from __future__ import annotations

import base64
import datetime as dt
from contextlib import contextmanager
from collections.abc import Iterator
import hashlib
import json
import logging
import math
import os
import random
import re
import secrets
import socket
import sqlite3
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

try:
    from .ai_advisor import AIAdvisor, AIQueueFull, AIUnavailable, validate_run_request
    from .suitability import SiteSuitabilityEngine
except ImportError:  # Also allow running this file directly from the backend directory.
    from ai_advisor import AIAdvisor, AIQueueFull, AIUnavailable, validate_run_request
    from suitability import SiteSuitabilityEngine

logger = logging.getLogger("agrisense")
UTC = dt.timezone.utc
METRIC_BOUNDS: dict[str, tuple[float, float]] = {
    "soil_moisture": (0, 100),
    "temperature": (-80, 100),
    "humidity": (0, 100),
    "soil_ph": (0, 14),
    "water_level": (0, 100),
    "battery": (0, 100),
    "light": (0, 2_000_000),
    "pressure": (100, 2_000),
}
STAT_METRICS = tuple(METRIC_BOUNDS)
DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
EXTRA_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
RANGE_SECONDS = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC)


def iso_utc(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_timestamp(value: Any, *, max_future_seconds: float | None = None) -> str:
    if value is None:
        return iso_utc()
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValueError("timestamp must be an ISO-8601 string")
    timestamp_text = value.strip()
    if "T" not in timestamp_text and "t" not in timestamp_text and " " not in timestamp_text:
        raise ValueError("timestamp must include a date and time")
    try:
        parsed = dt.datetime.fromisoformat(timestamp_text.replace("Z", "+00:00").replace("z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        parsed = parsed.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise ValueError("timestamp must be a valid ISO-8601 date-time") from exc
    if max_future_seconds is not None:
        if not math.isfinite(max_future_seconds) or max_future_seconds < 0:
            max_future_seconds = 300.0
        if (parsed - utc_now()).total_seconds() > max_future_seconds:
            raise ValueError("timestamp is too far in the future")
    return iso_utc(parsed)


def _sensor_bounds(name: str) -> tuple[float, float]:
    lower, upper = METRIC_BOUNDS[name]
    try:
        configured_lower = float(os.getenv(f"SENSOR_{name.upper()}_MIN", str(lower)))
        configured_upper = float(os.getenv(f"SENSOR_{name.upper()}_MAX", str(upper)))
        if (math.isfinite(configured_lower) and math.isfinite(configured_upper)
                and configured_lower <= configured_upper):
            return configured_lower, configured_upper
    except (OverflowError, ValueError):
        pass
    return lower, upper


def _future_timestamp_tolerance() -> float:
    try:
        value = float(os.getenv("MAX_FUTURE_TIMESTAMP_SECONDS", "300"))
        return value if math.isfinite(value) and value >= 0 else 300.0
    except (OverflowError, ValueError):
        return 300.0


def validate_site_location(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("site location must be a JSON object")
    site_id = payload.get("site_id")
    if not isinstance(site_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", site_id):
        raise ValueError("site_id must be 8-80 letters, numbers, underscores, or hyphens")
    site_name = payload.get("site_name")
    if not isinstance(site_name, str) or not site_name.strip() or len(site_name.strip()) > 100:
        raise ValueError("site_name must be 1-100 characters")
    def coordinate(name: str, minimum: float, maximum: float) -> float:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a finite number")
        try:
            result = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{name} must be a finite number") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be a finite number")
        if not minimum <= result <= maximum:
            raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
        return result
    return {
        "site_id": site_id,
        "site_name": site_name.strip(),
        "latitude": coordinate("latitude", -90, 90),
        "longitude": coordinate("longitude", -180, 180),
        "accuracy_m": coordinate("accuracy_m", 0, 100_000),
        "captured_at": parse_timestamp(payload.get("captured_at")),
    }


def validate_reading(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("reading must be a JSON object")
    device = payload.get("device")
    if not isinstance(device, str) or not DEVICE_RE.fullmatch(device.strip()):
        raise ValueError("device must be 1-64 characters using letters, numbers, '.', '_', ':', or '-'")
    device = device.strip()
    declared_source = payload.get("source")
    if declared_source is not None and (not isinstance(declared_source, str)
                                        or declared_source not in {"esp32", "simulation"}):
        raise ValueError("source must be esp32 or simulation")
    normalized: dict[str, Any] = {"device": device}
    for name in ("soil_moisture", "temperature"):
        if name not in payload:
            raise ValueError(f"missing required field: {name}")
    extras: dict[str, float] = {}
    for key, value in payload.items():
        if key in {"device", "timestamp", "source"}:
            continue
        if not isinstance(key, str) or not EXTRA_RE.fullmatch(key):
            raise ValueError(f"invalid sensor field name: {key!r}")
        if value is None and key in METRIC_BOUNDS and key not in {"soil_moisture", "temperature"}:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a finite number")
        try:
            value = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{key} must be a finite number") from exc
        if not math.isfinite(value):
            raise ValueError(f"{key} must be a finite number")
        bounds = _sensor_bounds(key) if key in METRIC_BOUNDS else None
        if bounds and not bounds[0] <= value <= bounds[1]:
            raise ValueError(f"{key} must be between {bounds[0]:g} and {bounds[1]:g}")
        if key in METRIC_BOUNDS:
            normalized[key] = value
        else:
            extras[key] = value
    if len(extras) > 32:
        raise ValueError("at most 32 additional numeric sensor fields are allowed")
    normalized["timestamp"] = parse_timestamp(
        payload.get("timestamp"), max_future_seconds=_future_timestamp_tolerance())
    normalized["extras"] = extras
    return normalized


class Thresholds:
    def __init__(self) -> None:
        self.soil_moisture_min = self._env_float("ALERT_SOIL_MOISTURE_MIN", 30, 0, 100)
        self.temperature_max = self._env_float("ALERT_TEMPERATURE_MAX", 35, -80, 100)
        self.temperature_min = self._env_float("ALERT_TEMPERATURE_MIN", 10, -80, 100)
        self.water_level_min = self._env_float("ALERT_WATER_LEVEL_MIN", 20, 0, 100)
        if self.temperature_min > self.temperature_max:
            self.temperature_min, self.temperature_max = 10.0, 35.0

    @staticmethod
    def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
            return value if math.isfinite(value) and minimum <= value <= maximum else default
        except (OverflowError, ValueError):
            return default


class ReadingStore:
    """SQLite persistence; every operation uses its own short-lived connection."""

    def __init__(self, database_url: str | None = None) -> None:
        raw = database_url or os.getenv("DATABASE_URL") or os.getenv("DB_PATH", "agrisense.sqlite3")
        if raw.startswith("sqlite:///"):
            raw = raw[10:]
        elif "://" in raw:
            raise ValueError("Only SQLite DATABASE_URL values are supported by this standard-library backend")
        self.path = raw
        self._memory_uri: str | None = None
        self._memory_lock = threading.RLock()
        self._closed = False
        self._keeper: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_uri = f"file:agrisense-{secrets.token_hex(8)}?mode=memory&cache=shared"
            self._keeper = self._connect()
        else:
            parent = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(parent, exist_ok=True)
        try:
            self._initialize()
        except Exception:
            self.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._memory_uri or self.path, timeout=10,
                               uri=self._memory_uri is not None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._closed:
            raise sqlite3.OperationalError("database is closed")
        if self._memory_uri is not None:
            self._memory_lock.acquire()
            if self._closed or self._keeper is None:
                self._memory_lock.release()
                raise sqlite3.OperationalError("database is closed")
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            with conn:
                yield conn
        finally:
            if conn is not None:
                conn.close()
            if self._memory_uri is not None:
                self._memory_lock.release()

    def close(self) -> None:
        with self._memory_lock:
            self._closed = True
            if self._keeper is not None:
                self._keeper.close()
                self._keeper = None

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS devices (
                    device TEXT PRIMARY KEY,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    ip_address TEXT
                );
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device TEXT NOT NULL REFERENCES devices(device),
                    timestamp TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    soil_moisture REAL NOT NULL,
                    temperature REAL NOT NULL,
                    humidity REAL,
                    soil_ph REAL,
                    water_level REAL,
                    battery REAL,
                    light REAL,
                    pressure REAL,
                    extras TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(device, timestamp)
                );
                CREATE INDEX IF NOT EXISTS readings_timestamp_idx ON readings(timestamp);
                CREATE INDEX IF NOT EXISTS readings_device_timestamp_idx ON readings(device, timestamp DESC);
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    device TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS alerts_active_unique
                    ON alerts(type, device) WHERE resolved_at IS NULL;
                CREATE INDEX IF NOT EXISTS alerts_timestamp_idx ON alerts(timestamp DESC);
                CREATE TABLE IF NOT EXISTS site_locations (
                    site_id TEXT PRIMARY KEY,
                    site_name TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    accuracy_m REAL NOT NULL,
                    captured_at TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
            """)
            device_columns = {row["name"] for row in conn.execute("PRAGMA table_info(devices)")}
            if "ip_address" not in device_columns:
                conn.execute("ALTER TABLE devices ADD COLUMN ip_address TEXT")

    @staticmethod
    def _row_site_location(row: sqlite3.Row) -> dict[str, Any]:
        return {"site_id": row["site_id"], "site_name": row["site_name"],
                "latitude": row["latitude"], "longitude": row["longitude"],
                "accuracy_m": row["accuracy_m"], "captured_at": row["captured_at"],
                "received_at": row["received_at"]}

    def save_site_location(self, location: dict[str, Any]) -> dict[str, Any]:
        received = iso_utc()
        with self._connection() as conn:
            conn.execute("""INSERT INTO site_locations
                (site_id, site_name, latitude, longitude, accuracy_m, captured_at, received_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site_id) DO UPDATE SET site_name=excluded.site_name,
                latitude=excluded.latitude, longitude=excluded.longitude,
                accuracy_m=excluded.accuracy_m, captured_at=excluded.captured_at,
                received_at=excluded.received_at""",
                (location["site_id"], location["site_name"], location["latitude"],
                 location["longitude"], location["accuracy_m"], location["captured_at"], received))
            row = conn.execute("SELECT * FROM site_locations WHERE site_id=?", (location["site_id"],)).fetchone()
            return self._row_site_location(row)

    def get_site_location(self, site_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM site_locations WHERE site_id=?", (site_id,)).fetchone()
            return self._row_site_location(row) if row else None

    def delete_site_location(self, site_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM site_locations WHERE site_id=?", (site_id,))
            return cursor.rowcount > 0

    @staticmethod
    def _row_reading(row: sqlite3.Row) -> dict[str, Any]:
        result: dict[str, Any] = {
            "device": row["device"],
            "soil_moisture": row["soil_moisture"],
            "temperature": row["temperature"],
            "timestamp": row["timestamp"],
            "received_at": row["received_at"],
            "source": row["source"],
        }
        for field in ("humidity", "soil_ph", "water_level", "battery", "light", "pressure"):
            if row[field] is not None:
                result[field] = row[field]
        try:
            result.update(json.loads(row["extras"] or "{}"))
        except (ValueError, TypeError):
            pass
        return result

    def insert(self, reading: dict[str, Any], source: str,
               thresholds: Thresholds | None = None, ip_address: str | None = None
               ) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
        received = iso_utc()
        columns = ("soil_moisture", "temperature", "humidity", "soil_ph", "water_level", "battery", "light", "pressure")
        values = [reading.get(name) for name in columns]
        with self._connection() as conn:
            conn.execute("INSERT OR IGNORE INTO devices(device, first_seen, last_seen, ip_address) VALUES(?, ?, ?, ?)",
                         (reading["device"], received, received, ip_address))
            cursor = conn.execute("""INSERT OR IGNORE INTO readings
                (device, timestamp, received_at, source, soil_moisture, temperature,
                 humidity, soil_ph, water_level, battery, light, pressure, extras)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (reading["device"], reading["timestamp"], received, source, *values,
                 json.dumps(reading["extras"], separators=(",", ":"))))
            row = conn.execute("SELECT * FROM readings WHERE device=? AND timestamp=?",
                              (reading["device"], reading["timestamp"])).fetchone()
            created_alerts: list[dict[str, Any]] = []
            conn.execute("UPDATE devices SET last_seen=?, ip_address=COALESCE(?, ip_address) WHERE device=?",
                         (received, ip_address, reading["device"]))
            if cursor.rowcount:
                created_alerts = self._evaluate_reading_alerts(conn, reading, received, thresholds or Thresholds())
            return self._row_reading(row), cursor.rowcount == 0, created_alerts

    @staticmethod
    def _evaluate_reading_alerts(conn: sqlite3.Connection, reading: dict[str, Any], now: str,
                                 thresholds: Thresholds) -> list[dict[str, Any]]:
        rules: list[tuple[str, bool, str]] = [
            ("LOW_SOIL_MOISTURE", reading["soil_moisture"] < thresholds.soil_moisture_min,
             "Soil moisture is below the configured threshold."),
            ("HIGH_TEMPERATURE", reading["temperature"] > thresholds.temperature_max,
             "Temperature is above the configured threshold."),
            ("LOW_TEMPERATURE", reading["temperature"] < thresholds.temperature_min,
             "Temperature is below the configured threshold."),
        ]
        if reading.get("water_level") is not None:
            rules.append(("LOW_WATER_LEVEL", reading["water_level"] < thresholds.water_level_min,
                          "Water level is below the configured threshold."))
        new_alerts: list[dict[str, Any]] = []
        for alert_type, active, message in rules:
            if active:
                cursor = conn.execute("INSERT OR IGNORE INTO alerts(type,severity,device,message,timestamp) VALUES(?,?,?,?,?)",
                                      (alert_type, "warning", reading["device"], message, now))
                if cursor.rowcount:
                    new_alerts.append({"type": alert_type, "severity": "warning", "device": reading["device"],
                                       "message": message, "timestamp": now})
            else:
                conn.execute("UPDATE alerts SET resolved_at=? WHERE type=? AND device=? AND resolved_at IS NULL",
                             (now, alert_type, reading["device"]))
        return new_alerts

    def device_rows(self) -> list[sqlite3.Row]:
        with self._connection() as conn:
            return conn.execute("SELECT * FROM devices ORDER BY device").fetchall()

    def device(self, device_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM devices WHERE device=?", (device_id,)).fetchone()
            if row is None:
                return None
            latest = conn.execute("SELECT * FROM readings WHERE device=? ORDER BY timestamp DESC,id DESC LIMIT 1",
                                  (device_id,)).fetchone()
            reading_count = int(conn.execute("SELECT COUNT(*) FROM readings WHERE device=?", (device_id,)).fetchone()[0])
            latest_reading = self._row_reading(latest) if latest else None
            sensor_fields = (set(latest_reading) - {"device", "timestamp", "received_at", "source"}) if latest_reading else set()
            return {"device": row["device"], "first_seen": row["first_seen"], "last_seen": row["last_seen"],
                    "reading_count": reading_count, "ip_address": row["ip_address"],
                    "available_sensors": sorted(sensor_fields), "latest_reading": latest_reading}

    def list_readings(self, *, device: str | None = None, since: str | None = None,
                      limit: int = 100, offset: int = 0, ascending: bool = False) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if device:
            clauses.append("device=?")
            params.append(device)
        if since:
            clauses.append("timestamp>=?")
            params.append(since)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = "ASC" if ascending else "DESC"
        with self._connection() as conn:
            rows = conn.execute(f"SELECT * FROM readings{where} ORDER BY timestamp {order},id {order} LIMIT ? OFFSET ?",
                                (*params, limit, offset)).fetchall()
        return [self._row_reading(row) for row in rows]

    def count_readings(self, device: str | None = None, since: str | None = None) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if device:
            clauses.append("device=?")
            params.append(device)
        if since:
            clauses.append("timestamp>=?")
            params.append(since)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM readings{where}", params).fetchone()[0])

    def stats_summary(self, device: str | None, since: str) -> dict[str, Any]:
        clauses = ["timestamp>=?"]
        params: list[Any] = [since]
        if device:
            clauses.append("device=?")
            params.append(device)
        where = " AND ".join(clauses)
        with self._connection() as conn:
            count = int(conn.execute(f"SELECT COUNT(*) FROM readings WHERE {where}", params).fetchone()[0])
            latest_row = conn.execute(f"SELECT * FROM readings WHERE {where} ORDER BY timestamp DESC,id DESC LIMIT 1",
                                      params).fetchone()
            metrics: dict[str, Any] = {}
            if count:
                for name in STAT_METRICS:
                    aggregate = conn.execute(
                        f"SELECT AVG({name}) AS average, MIN({name}) AS minimum, MAX({name}) AS maximum, "
                        f"COUNT({name}) AS value_count FROM readings WHERE {where}", params).fetchone()
                    if not aggregate["value_count"]:
                        continue
                    current_row = conn.execute(
                        f"SELECT {name},timestamp,id FROM readings WHERE {where} AND {name} IS NOT NULL "
                        "ORDER BY timestamp DESC,id DESC LIMIT 1", params).fetchone()
                    previous = conn.execute(
                        f"SELECT {name} FROM readings WHERE {name} IS NOT NULL AND "
                        "(timestamp<? OR (timestamp=? AND id<?)) "
                        + ("AND device=? " if device else "") + "ORDER BY timestamp DESC,id DESC LIMIT 1",
                        (current_row["timestamp"], current_row["timestamp"], current_row["id"],
                         *((device,) if device else ())),
                    ).fetchone()
                    current = current_row[name]
                    old = previous[name] if previous else None
                    change = ((current - old) / abs(old) * 100) if old is not None and old != 0 else None
                    metrics[name] = {"current": current, "average": round(aggregate["average"], 3),
                                     "minimum": aggregate["minimum"], "maximum": aggregate["maximum"],
                                     "change_percent": round(change, 2) if change is not None else None,
                                     "trend": "stable" if change is None or abs(change) < 2 else
                                     ("up" if change > 0 else "down"),
                                     "reading_count": aggregate["value_count"],
                                     "latest_timestamp": current_row["timestamp"]}
        return {"reading_count": count, "latest_reading": self._row_reading(latest_row) if latest_row else None,
                "metrics": metrics}

    def recent_for_insights(self, device: str | None, limit: int = 8) -> list[dict[str, Any]]:
        return self.list_readings(device=device, limit=limit)

    def recent_by_device_for_insights(self, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
        with self._connection() as conn:
            rows = conn.execute("""SELECT * FROM (
                SELECT readings.*, ROW_NUMBER() OVER (
                    PARTITION BY device ORDER BY timestamp DESC, id DESC
                ) AS device_rank FROM readings
            ) WHERE device_rank<=? ORDER BY device, timestamp DESC, id DESC""", (limit,)).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            reading = self._row_reading(row)
            grouped.setdefault(reading["device"], []).append(reading)
        return grouped

    def alerts(self, active_only: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as conn:
            where = "WHERE resolved_at IS NULL" if active_only else ""
            rows = conn.execute(f"SELECT * FROM alerts {where} ORDER BY timestamp DESC,id DESC LIMIT ?", (limit,)).fetchall()
        return [{"type": row["type"], "severity": row["severity"], "device": row["device"],
                 "message": row["message"], "timestamp": row["timestamp"],
                 "resolved_at": row["resolved_at"]} for row in rows]

    def set_offline_alert(self, device: str, offline: bool) -> dict[str, Any] | None:
        now = iso_utc()
        alert_type = "DEVICE_OFFLINE"
        message = "No sensor reading has been received within the configured device timeout."
        with self._connection() as conn:
            if offline:
                cursor = conn.execute("INSERT OR IGNORE INTO alerts(type,severity,device,message,timestamp) VALUES(?,?,?,?,?)",
                                      (alert_type, "warning", device, message, now))
                return {"type": alert_type, "severity": "warning", "device": device,
                        "message": message, "timestamp": now} if cursor.rowcount else None
            conn.execute("UPDATE alerts SET resolved_at=? WHERE type=? AND device=? AND resolved_at IS NULL",
                         (now, alert_type, device))
        return None


class InsightEngine:
    """Transparent agricultural heuristics; this is not a machine-learning model."""

    @staticmethod
    def evaluate(readings: list[dict[str, Any]], thresholds: Thresholds) -> list[dict[str, Any]]:
        if not readings:
            return []
        # Store queries and API queries use different sort directions. Normalize here
        # so the latest sensor sample is always evaluated first.
        newest_first = sorted(readings, key=lambda row: row.get("timestamp", ""), reverse=True)
        latest = newest_first[0]
        device = latest.get("device")
        result: list[dict[str, Any]] = []
        if latest["soil_moisture"] < thresholds.soil_moisture_min:
            result.append({"type": "IRRIGATION_RECOMMENDED", "severity": "warning",
                           "message": "Soil moisture is below the configured optimal range. Irrigation may be required."})
        moisture = [row["soil_moisture"] for row in newest_first[:5]]
        if len(moisture) >= 3 and max(moisture) - min(moisture) < 5:
            result.append({"type": "MOISTURE_STABLE", "severity": "info",
                           "message": "Soil moisture has stayed stable across recent readings."})
        temperatures = [row["temperature"] for row in newest_first[:5]]
        if len(temperatures) >= 3 and temperatures[0] - temperatures[-1] >= 2:
            result.append({"type": "TEMPERATURE_RISING", "severity": "info",
                           "message": "Temperature has increased significantly over the recent monitoring period."})
        if not result:
            result.append({"type": "CONDITIONS_MONITORED", "severity": "info",
                           "message": "Recent conditions are being monitored. No configured rule currently requires attention."})
        generated_at = iso_utc()
        for insight in result:
            insight["device"] = device
            insight["timestamp"] = latest.get("timestamp", generated_at)
        return result

    @classmethod
    def evaluate_devices(cls, readings_by_device: dict[str, list[dict[str, Any]]],
                         thresholds: Thresholds) -> list[dict[str, Any]]:
        """Evaluate each device independently; never blend unrelated field sensors."""
        return [insight for readings in readings_by_device.values()
                for insight in cls.evaluate(readings, thresholds)]


class WebSocketPeer:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.lock = threading.Lock()
        self.alive = True

    def close(self) -> None:
        with self.lock:
            if not self.alive:
                try:
                    self.sock.close()
                except OSError:
                    pass
                return
            self.alive = False
            try:
                self.sock.sendall(b"\x88\x02\x03\xe9")
            except OSError:
                pass
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass

    def send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        size = len(payload)
        if size < 126:
            header = bytes((0x81, size))
        elif size <= 65535:
            header = bytes((0x81, 126)) + struct.pack("!H", size)
        else:
            header = bytes((0x81, 127)) + struct.pack("!Q", size)
        with self.lock:
            if not self.alive:
                return
            try:
                self.sock.sendall(header + payload)
            except OSError:
                self.alive = False
                try:
                    self.sock.close()
                except OSError:
                    pass

    def receive_loop(self, on_text: Callable[[str], None] | None = None) -> None:
        self.sock.settimeout(1.0)
        fragmented_message = False
        fragment_opcode = 0x1
        buffer = bytearray()
        try:
            last_ping = time.monotonic()
            last_pong = last_ping
            ping_payload = secrets.token_bytes(4)
            while self.alive:
                try:
                    head = self._read_exact(2)
                except socket.timeout:
                    now = time.monotonic()
                    if now - last_pong > 60:
                        break
                    if now - last_ping >= 25:
                        self._send_control(0x9, ping_payload)
                        last_ping = now
                        ping_payload = secrets.token_bytes(4)
                    continue
                first, second = head
                if first & 0x70 or not second & 0x80:
                    break
                final = bool(first & 0x80)
                opcode = first & 0x0F
                length = second & 0x7F
                if length == 126:
                    length = struct.unpack("!H", self._read_exact(2))[0]
                    if length < 126:
                        break
                elif length == 127:
                    length = struct.unpack("!Q", self._read_exact(8))[0]
                    if length < 65536 or length >> 63:
                        break
                if length > 1_048_576:
                    break
                if opcode >= 0x8 and (not final or length > 125):
                    break
                if opcode in (0x1, 0x2):
                    if fragmented_message:
                        break
                    fragment_opcode = opcode
                    fragment_size = length
                    fragmented_message = not final
                elif opcode == 0x0:
                    if not fragmented_message:
                        break
                    fragment_size += length
                    if fragment_size > 1_048_576:
                        break
                elif opcode not in (0x8, 0x9, 0xA):
                    break
                mask = self._read_exact(4)
                payload = self._read_exact(length) if length else b""
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
                if opcode == 0x8:
                    self._send_control(0x8, payload[:125])
                    break
                if opcode == 0x9:
                    self._send_control(0xA, payload)
                    continue
                if opcode == 0xA:
                    last_pong = time.monotonic()
                    continue
                if opcode == 0x0:
                    buffer.extend(payload)
                    if not final:
                        continue
                    fragmented_message = False
                    fragment_size = 0
                    message = bytes(buffer)
                    buffer = bytearray()
                    if fragment_opcode == 0x1 and on_text is not None:
                        on_text(message.decode("utf-8", "replace"))
                    continue
                if not final:
                    buffer = bytearray(payload)
                    continue
                if opcode == 0x1 and on_text is not None:
                    on_text(payload.decode("utf-8", "replace"))
        except (OSError, ValueError, struct.error):
            pass
        finally:
            self.alive = False

    def _read_exact(self, count: int) -> bytes:
        parts = bytearray()
        while len(parts) < count:
            try:
                data = self.sock.recv(count - len(parts))
            except socket.timeout as exc:
                if parts:
                    raise OSError("incomplete WebSocket frame timed out") from exc
                raise
            if not data:
                raise OSError("WebSocket peer disconnected")
            parts.extend(data)
        return bytes(parts)

    def _send_control(self, opcode: int, payload: bytes) -> None:
        if len(payload) > 125:
            self.close()
            return
        frame = bytes((0x80 | opcode, len(payload))) + payload
        with self.lock:
            if self.alive:
                try:
                    self.sock.sendall(frame)
                except OSError:
                    self.alive = False
                    try:
                        self.sock.close()
                    except OSError:
                        pass


class Simulation:
    def __init__(self, ingest: Callable[[dict[str, Any], str], dict[str, Any]]) -> None:
        self.ingest = ingest
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self.device = "sim-01"
        self.interval = 5.0
        self._moisture = 54.0
        self._temperature = 28.0
        self._phase = 0.0
        self._random = random.Random()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def configuration(self) -> tuple[str, float]:
        with self._lock:
            return self.device, self.interval

    def start(self, device: str = "sim-01", interval: float = 5.0) -> bool:
        if not isinstance(device, str) or not DEVICE_RE.fullmatch(device) or not device.lower().startswith("sim-"):
            raise ValueError("simulation device ID must use the sim-* namespace")
        if isinstance(interval, bool) or not isinstance(interval, (int, float)):
            raise ValueError("interval_seconds must be a finite number")
        try:
            interval = float(interval)
        except (OverflowError, ValueError) as exc:
            raise ValueError("interval_seconds must be a finite number") from exc
        if not math.isfinite(interval) or interval < 0.5 or interval > 3600:
            raise ValueError("interval_seconds must be between 0.5 and 3600")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if self._thread is not None:
                self._thread.join(timeout=0)
            self.device = device
            self.interval = interval
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(self._stop,), name="agrisense-simulation", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            stop, thread = self._stop, self._thread
            if stop is None or thread is None or not thread.is_alive():
                return False
            stop.set()
        if thread is not threading.current_thread():
            thread.join(timeout=10)
        return not thread.is_alive()

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self._phase += 0.14
            self._moisture = min(65, max(45, self._moisture - 0.025 + self._random.gauss(0, 0.65)))
            self._temperature = min(32, max(26, self._temperature + 0.07 * math.sin(self._phase) + self._random.gauss(0, 0.18)))
            reading = {"device": self.device, "soil_moisture": round(self._moisture, 1),
                       "temperature": round(self._temperature, 1),
                       "humidity": round(min(75, max(40, 60 - (self._temperature - 28) * 1.7 + self._random.gauss(0, 1))), 1),
                       "soil_ph": round(min(7.2, max(5.8, 6.4 + self._random.gauss(0, 0.04))), 2),
                       "water_level": round(min(100, max(0, 74 - self._phase * 0.1 + self._random.gauss(0, 0.2))), 1),
                       "light": round(min(1200, max(100, 700 + 180 * math.sin(self._phase / 3) + self._random.gauss(0, 15))), 0),
                       "battery": 94.0}
            try:
                self.ingest(reading, "simulation")
            except Exception:
                logger.exception("READING_REJECTED source=simulation device=%s", self.device)
            if stop.wait(self.interval):
                break


class AgriSenseApp:
    def __init__(self, database_url: str | None = None, device_timeout: float | None = None,
                 allowed_origins: list[str] | None = None) -> None:
        self.store = ReadingStore(database_url)
        try:
            configured_timeout = (device_timeout if device_timeout is not None else
                                  float(os.getenv("DEVICE_TIMEOUT_MS", "30000")) / 1000
                                  if os.getenv("DEVICE_TIMEOUT_MS") is not None else
                                  os.getenv("DEVICE_TIMEOUT_SECONDS", "30"))
            timeout = float(configured_timeout)
            self.device_timeout = timeout if math.isfinite(timeout) and timeout >= 0.1 else 30.0
        except (OverflowError, ValueError):
            self.device_timeout = 30.0
        raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,null")
        self.allowed_origins = set(allowed_origins if allowed_origins is not None else
                                   [origin.strip() for origin in raw_origins.split(",") if origin.strip()])
        self.thresholds = Thresholds()
        self.suitability = SiteSuitabilityEngine()
        # The AI advisor loads its heavy dependencies lazily and runs jobs on its
        # own worker thread, so constructing it is cheap and dependency-free.
        self.ai = AIAdvisor()
        self.peers: set[WebSocketPeer] = set()
        self.peer_lock = threading.Lock()
        self.status_lock = threading.Lock()
        self._statuses: dict[str, str] = {}
        self._closing = threading.Event()
        self.simulation = Simulation(self.ingest)
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self.started_at = time.monotonic()

    def ingest(self, payload: Any, source: str = "esp32", ip_address: str | None = None) -> dict[str, Any]:
        if source not in {"esp32", "simulation"}:
            raise ValueError("source must be esp32 or simulation")
        if isinstance(payload, dict) and payload.get("source") not in (None, source):
            raise ValueError("payload source does not match the trusted ingestion path")
        try:
            reading = validate_reading(payload)
        except ValueError as exc:
            logger.warning("READING_REJECTED source=%s reason=%s", source, exc)
            raise
        is_simulation_device = reading["device"].lower().startswith("sim-")
        if (source == "simulation") != is_simulation_device:
            raise ValueError("simulation readings must use a sim-* device ID; ESP32 readings must not")
        logger.info("READING_RECEIVED source=%s device=%s", source, reading["device"])
        stored, duplicate, alerts = self.store.insert(reading, source, self.thresholds, ip_address)
        try:
            prior = self._set_status(stored["device"], "ONLINE")
        except sqlite3.Error:
            logger.exception("DATABASE_ERROR while resolving device status alert device=%s", stored["device"])
            with self.status_lock:
                prior = self._statuses.get(stored["device"])
                self._statuses[stored["device"]] = "ONLINE"
        if prior != "ONLINE":
            self._broadcast({"type": "device_status", "device": stored["device"], "status": "ONLINE"})
        if duplicate:
            logger.info("READING_DUPLICATE source=%s device=%s timestamp=%s",
                        source, stored["device"], stored["timestamp"])
            return {"reading": stored, "duplicate": True}
        logger.info("READING_STORED source=%s device=%s timestamp=%s",
                    source, stored["device"], stored["timestamp"])
        self._broadcast({"type": "sensor_reading", "data": stored})
        for alert in alerts:
            logger.info("ALERT_CREATED type=%s device=%s", alert["type"], alert["device"])
            self._broadcast({"type": "alert", "data": alert})
        try:
            insights = InsightEngine.evaluate(self.store.recent_for_insights(stored["device"]), self.thresholds)
            self._broadcast({"type": "ai_insights", "device": stored["device"], "data": insights})
        except sqlite3.Error:
            logger.exception("DATABASE_ERROR while generating insights device=%s", stored["device"])
        return {"reading": stored, "duplicate": False}

    def _set_status(self, device: str, status: str) -> str | None:
        with self.status_lock:
            previous = self._statuses.get(device)
            self._statuses[device] = status
        if status == "ONLINE" and previous != "ONLINE":
            try:
                self.store.set_offline_alert(device, False)
            except sqlite3.Error:
                logger.exception("DATABASE_ERROR while resolving offline alert device=%s", device)
        return previous

    def list_devices(self) -> list[dict[str, Any]]:
        now = utc_now()
        result = []
        for row in self.store.device_rows():
            last_seen = dt.datetime.fromisoformat(row["last_seen"].replace("Z", "+00:00"))
            status = "ONLINE" if (now - last_seen).total_seconds() <= self.device_timeout else "OFFLINE"
            previous = self._set_status(row["device"], status)
            try:
                details = self.store.device(row["device"])
            except sqlite3.Error:
                logger.exception("DATABASE_ERROR while loading device details device=%s", row["device"])
                details = {"device": row["device"], "first_seen": row["first_seen"],
                           "last_seen": row["last_seen"], "reading_count": None,
                           "ip_address": row["ip_address"], "available_sensors": [], "latest_reading": None}
            uptime = max(0, (last_seen - dt.datetime.fromisoformat(row["first_seen"].replace("Z", "+00:00"))).total_seconds())
            result.append({**details, "status": status, "uptime_seconds": int(uptime),
                           "connection": {"connected": status == "ONLINE", "last_seen": row["last_seen"]}})
            if status == "OFFLINE":
                try:
                    alert = self.store.set_offline_alert(row["device"], True)
                    if alert:
                        logger.info("ALERT_CREATED type=DEVICE_OFFLINE device=%s", row["device"])
                        self._broadcast({"type": "alert", "data": alert})
                except sqlite3.Error:
                    logger.exception("DATABASE_ERROR while creating offline alert device=%s", row["device"])
            if previous != status:
                self._broadcast({"type": "device_status", "device": row["device"], "status": status})
        return result

    def device(self, device_id: str) -> dict[str, Any] | None:
        known = self.store.device(device_id)
        if known is None:
            return None
        return next((item for item in self.list_devices() if item["device"] == device_id), None)

    def stats(self, device: str | None, range_name: str) -> dict[str, Any]:
        seconds = RANGE_SECONDS[range_name]
        since = iso_utc(utc_now() - dt.timedelta(seconds=seconds))
        summary = self.store.stats_summary(device, since)
        devices = self.list_devices()
        if device:
            devices = [item for item in devices if item["device"] == device]
        return {"range": range_name, "since": since, "reading_count": summary["reading_count"],
                "latest_reading": summary["latest_reading"],
                "device_uptime_seconds": {item["device"]: item["uptime_seconds"] for item in devices},
                "metrics": summary["metrics"]}

    def health(self) -> dict[str, Any]:
        with self.store._connection() as conn:
            conn.execute("SELECT 1").fetchone()
        online = sum(device["status"] == "ONLINE" for device in self.list_devices())
        return {"status": "ok", "service": "AgriSense AI", "timestamp": iso_utc(),
                "uptime_seconds": int(time.monotonic() - self.started_at), "database": "connected",
                "websocket": "running", "devices_online": online,
                "devicesOnline": online, "connectedFrontendClients": self.peer_count,
                "websocket_clients": self.peer_count, "simulation": {
                    "running": self.simulation.running,
                    "device": self.simulation.configuration()[0]},
                "device_timeout_seconds": self.device_timeout}

    @property
    def peer_count(self) -> int:
        with self.peer_lock:
            return len(self.peers)

    def start_monitor(self) -> None:
        if self._closing.is_set():
            return
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(target=self._monitor, name="agrisense-device-monitor", daemon=True)
        self._monitor_thread.start()

    def _monitor(self) -> None:
        while not self._closing.is_set() and not self._monitor_stop.wait(min(2.0, max(0.5, self.device_timeout / 10))):
            try:
                self.list_devices()
            except Exception:
                logger.exception("device status monitor failed")

    def close(self) -> None:
        self._closing.set()
        self._monitor_stop.set()
        self.ai.close()
        self.simulation.stop()
        if self._monitor_thread and self._monitor_thread is not threading.current_thread():
            self._monitor_thread.join(timeout=12)
        with self.peer_lock:
            peers = list(self.peers)
        for peer in peers:
            try:
                peer.send({"type": "system_status", "data": {"status": "stopping", "timestamp": iso_utc()}})
            except OSError:
                logger.info("WEBSOCKET_DISCONNECT_FAILED during shutdown")
            finally:
                peer.close()
                self.remove_peer(peer)
        with self.peer_lock:
            self.peers.clear()
        self.store.close()

    def add_peer(self, peer: WebSocketPeer) -> bool:
        with self.peer_lock:
            if self._closing.is_set():
                return False
            self.peers.add(peer)
            return True

    def remove_peer(self, peer: WebSocketPeer) -> None:
        with self.peer_lock:
            was_connected = peer in self.peers
            self.peers.discard(peer)
        if was_connected:
            logger.info("WEBSOCKET_DISCONNECTED clients=%d", self.peer_count)

    def _broadcast(self, message: dict[str, Any]) -> None:
        with self.peer_lock:
            peers = list(self.peers)
        for peer in peers:
            try:
                peer.send(message)
            except Exception:
                logger.exception("WebSocket broadcast failed")
                peer.close()
            if not peer.alive:
                self.remove_peer(peer)

    def websocket_connected(self, peer: WebSocketPeer) -> bool:
        if not self.add_peer(peer):
            peer.close()
            return False
        logger.info("WEBSOCKET_CONNECTED clients=%d", self.peer_count)
        peer.send({"type": "connection_status", "status": "connected", "timestamp": iso_utc()})
        try:
            for item in self.list_devices():
                peer.send({"type": "device_status", "device": item["device"], "status": item["status"]})
        except sqlite3.Error:
            logger.exception("DATABASE_ERROR sending WebSocket device snapshot")
        return peer.alive


def build_handler(app: AgriSenseApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AgriSenseAI/2.0"
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10.0)

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.info("HTTP %s %s", self.address_string(), fmt % args)

        def _origin_trusted(self, origin: str | None) -> bool:
            """Whether a browser origin may talk to this server.

            The dashboard is served by this very process, and browsers send their own
            origin on same-origin requests (including the /ws upgrade), so the server's
            own origin is always trusted. Without that, the page would be refused its own
            live feed on any port missing from ALLOWED_ORIGINS -- including the default
            http://localhost:5000 the README tells people to open.
            """
            if not origin:
                return True                      # curl, ESP32 firmware, or a file:// page
            if "*" in app.allowed_origins or origin in app.allowed_origins:
                return True
            host = self.headers.get("Host")
            if not host:
                return False
            return origin in {f"http://{host}", f"https://{host}"}

        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            wildcard = "*" in app.allowed_origins
            if origin and self._origin_trusted(origin):
                self.send_header("Access-Control-Allow-Origin", "*" if wildcard else origin)
                self.send_header("Vary", "Origin")
                if not wildcard:
                    self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Max-Age", "600")

        def _json(self, status: int, body: Any) -> None:
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _error(self, status: int, code: str, message: str) -> None:
            self._json(status, {"error": {"code": code, "message": message, "timestamp": iso_utc()},
                                "message": message})

        def _origin_allowed(self) -> bool:
            return self._origin_trusted(self.headers.get("Origin"))

        def _static(self, filename: str, content_type: str) -> None:
            static_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
            try:
                with open(os.path.join(static_root, filename), "rb") as asset:
                    body = asset.read()
            except OSError:
                self._error(404, "NOT_FOUND", "The requested resource was not found.")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors()
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _body(self) -> Any:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("chunked request bodies are not supported")
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type and content_type != "application/json":
                raise ValueError("Content-Type must be application/json")
            length_header = self.headers.get("Content-Length", "0")
            try:
                length = int(length_header)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0 or length > 262_144:
                raise ValueError("request body must be 256 KiB or smaller")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("request body ended before Content-Length bytes were received")

            def reject_constant(value: str) -> None:
                raise ValueError(f"invalid JSON constant: {value}")
            try:
                return json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                raise ValueError("request body must contain valid JSON") from exc
            except ValueError as exc:
                raise ValueError("request body must contain valid JSON") from exc

        def do_DELETE(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if not self._origin_allowed():
                self._error(403, "ORIGIN_NOT_ALLOWED", "This request origin is not allowed.")
                return
            try:
                if not path.startswith("/api/site-location/"):
                    self._error(404, "NOT_FOUND", "The requested resource was not found.")
                    return
                site_id = path[len("/api/site-location/"):]
                if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", site_id):
                    raise ValueError("invalid site_id")
                self._json(200, {"ok": True, "deleted": app.store.delete_site_location(site_id)})
            except ValueError as exc:
                self._error(400, "INVALID_REQUEST", str(exc))
            except (sqlite3.Error, OSError, RuntimeError):
                logger.exception("API_ERROR method=DELETE path=%s", path)
                self._error(503, "SERVICE_UNAVAILABLE", "The request could not be completed.")
            except Exception:
                logger.exception("API_ERROR method=DELETE path=%s", path)
                self._error(500, "INTERNAL_ERROR", "The request could not be completed.")

        def do_OPTIONS(self) -> None:
            if not self._origin_allowed():
                self._error(403, "ORIGIN_NOT_ALLOWED", "This request origin is not allowed.")
                return
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:
            if not self._origin_allowed():
                self._error(403, "ORIGIN_NOT_ALLOWED", "This request origin is not allowed.")
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/ws":
                self._websocket()
                return
            static_assets = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/suitability.js": ("suitability.js", "text/javascript; charset=utf-8"),
                "/ParticleText.js": ("ParticleText.js", "text/javascript; charset=utf-8"),
                "/ParticleText.css": ("ParticleText.css", "text/css; charset=utf-8"),
                "/ScrollExpand.js": ("ScrollExpand.js", "text/javascript; charset=utf-8"),
                "/ScrollExpand.css": ("ScrollExpand.css", "text/css; charset=utf-8"),
                "/views.js": ("views.js", "text/javascript; charset=utf-8"),
                "/ai-advisor.js": ("ai-advisor.js", "text/javascript; charset=utf-8"),
                "/field-showcase.svg": ("field-showcase.svg", "image/svg+xml"),
            }
            if path in static_assets:
                filename, content_type = static_assets[path]
                self._static(filename, content_type)
                return
            if path.startswith("/crops/"):
                crop = path[len("/crops/"):]
                if re.fullmatch(r"[a-z0-9_-]{1,40}\.svg", crop):
                    self._static(os.path.join("crops", crop), "image/svg+xml")
                    return
            try:
                if path == "/api/health":
                    self._json(200, app.health())
                elif path == "/api/suitability/schema":
                    self._json(200, app.suitability.schema())
                elif path.startswith("/api/site-location/"):
                    site_id = path[len("/api/site-location/"):]
                    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", site_id):
                        raise ValueError("invalid site_id")
                    location = app.store.get_site_location(site_id)
                    if location:
                        self._json(200, {"location": location})
                    else:
                        self._error(404, "SITE_LOCATION_NOT_FOUND", "The requested site location was not found.")
                elif path == "/api/devices":
                    devices = app.list_devices()
                    self._json(200, {"devices": devices, "count": len(devices)})
                elif path.startswith("/api/devices/"):
                    device = app.device(path[len("/api/devices/"):])
                    self._json(200, device) if device else self._error(404, "DEVICE_NOT_FOUND", "The requested device was not found.")
                elif path == "/api/readings/latest":
                    rows = app.store.list_readings(device=self._query_one(query, "device"), limit=1)
                    self._json(200, {"reading": rows[0] if rows else None})
                elif path == "/api/readings":
                    device = self._query_one(query, "device")
                    limit = self._integer_query(query, "limit", 100, 1, 1000)
                    offset = self._integer_query(query, "offset", 0, 0, 10_000_000)
                    time_range = self._query_one(query, "range")
                    if time_range:
                        if time_range not in RANGE_SECONDS:
                            raise ValueError("range must be one of 1h, 6h, 24h, 7d, 30d")
                        since = iso_utc(utc_now() - dt.timedelta(seconds=RANGE_SECONDS[time_range]))
                    else:
                        since = None
                    rows = app.store.list_readings(device=device, since=since, limit=limit, offset=offset)
                    self._json(200, {"readings": rows, "count": app.store.count_readings(device, since),
                                     "limit": limit, "offset": offset})
                elif path.startswith("/api/readings/"):
                    device = path[len("/api/readings/"):]
                    limit = self._integer_query(query, "limit", 100, 1, 1000)
                    offset = self._integer_query(query, "offset", 0, 0, 10_000_000)
                    rows = app.store.list_readings(device=device, limit=limit, offset=offset)
                    self._json(200, {"device": device, "readings": rows,
                                     "count": app.store.count_readings(device), "limit": limit, "offset": offset})
                elif path == "/api/stats":
                    range_name = self._query_one(query, "range") or "24h"
                    if range_name not in RANGE_SECONDS:
                        raise ValueError("range must be one of 1h, 6h, 24h, 7d, 30d")
                    self._json(200, app.stats(self._query_one(query, "device"), range_name))
                elif path == "/api/alerts":
                    active = self._query_one(query, "active") != "false"
                    limit = self._integer_query(query, "limit", 100, 1, 1000)
                    alerts = app.store.alerts(active, limit)
                    self._json(200, {"alerts": alerts, "count": len(alerts)})
                elif path == "/api/insights":
                    device = self._query_one(query, "device")
                    if device:
                        readings = app.store.recent_for_insights(device)
                        insights = InsightEngine.evaluate(readings, app.thresholds)
                    else:
                        readings_by_device = app.store.recent_by_device_for_insights()
                        insights = InsightEngine.evaluate_devices(readings_by_device, app.thresholds)
                    self._json(200, {"engine": "rule-based", "device": device, "insights": insights})
                elif path == "/api/ai/status":
                    self._json(200, app.ai.status())
                elif path.startswith("/api/ai/jobs/"):
                    job_id = path[len("/api/ai/jobs/"):]
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", job_id):
                        raise ValueError("invalid job id")
                    job = app.ai.get(job_id)
                    if job is None:
                        self._error(404, "AI_JOB_NOT_FOUND", "No AI run with that id is retained.")
                    else:
                        self._json(200, job.as_dict())
                else:
                    self._error(404, "NOT_FOUND", "The requested resource was not found.")
            except ValueError as exc:
                self._error(400, "INVALID_REQUEST", str(exc))
            except AIUnavailable as exc:
                self._error(503, "AI_UNAVAILABLE", str(exc))
            except AIQueueFull as exc:
                self._error(429, "AI_BUSY", str(exc))
            except (sqlite3.Error, OSError, RuntimeError):
                logger.exception("API_ERROR path=%s", path)
                self._error(503, "SERVICE_UNAVAILABLE", "The request could not be completed.")
            except Exception:
                logger.exception("API_ERROR method=GET path=%s", path)
                self._error(500, "INTERNAL_ERROR", "The request could not be completed.")

        @staticmethod
        def _query_one(query: dict[str, list[str]], key: str) -> str | None:
            value = query.get(key, [None])[0]
            return value if value else None

        def _integer_query(self, query: dict[str, list[str]], name: str, default: int,
                           minimum: int, maximum: int) -> int:
            value = self._query_one(query, name)
            if value is None:
                return default
            try:
                result = int(value)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if not minimum <= result <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            return result

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if not self._origin_allowed():
                self._error(403, "ORIGIN_NOT_ALLOWED", "This request origin is not allowed.")
                return
            if path == "/api/simulation/stop" and self.headers.get("Content-Length", "0") == "0":
                stopped = app.simulation.stop()
                self._json(200, {"ok": True, "running": False, "stopped": stopped})
                return
            if path not in {"/api/readings", "/api/simulation/start", "/api/simulation/stop",
                            "/api/site-location", "/api/suitability/recommendations",
                            "/api/ai/analyze", "/api/ai/warmup"}:
                self._error(404, "NOT_FOUND", "The requested resource was not found.")
                return
            try:
                if path == "/api/site-location":
                    location = validate_site_location(self._body())
                    saved = app.store.save_site_location(location)
                    self._json(200, {"ok": True, "location": saved})
                elif path == "/api/suitability/recommendations":
                    self._json(200, app.suitability.recommend(self._body()))
                elif path == "/api/readings":
                    result = app.ingest(self._body(), "esp32", self.client_address[0])
                    status = 200 if result["duplicate"] else 201
                    self._json(status, {"ok": True, **result})
                elif path == "/api/simulation/start":
                    payload = self._body() if self.headers.get("Content-Length", "0") != "0" else {}
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be a JSON object")
                    if set(payload) - {"device", "interval_seconds"}:
                        raise ValueError("simulation request contains unsupported fields")
                    device = payload.get("device", "sim-01")
                    interval = payload.get("interval_seconds", 5)
                    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
                        raise ValueError("interval_seconds must be a number")
                    try:
                        interval = float(interval)
                    except (OverflowError, ValueError) as exc:
                        raise ValueError("interval_seconds must be finite") from exc
                    started = app.simulation.start(device, interval)
                    actual_device, actual_interval = app.simulation.configuration()
                    self._json(200, {"ok": True, "running": app.simulation.running, "started": started,
                                     "device": actual_device, "interval_seconds": actual_interval})
                elif path == "/api/simulation/stop":
                    stopped = app.simulation.stop()
                    self._json(200, {"ok": True, "running": False, "stopped": stopped})
                elif path == "/api/ai/analyze":
                    payload = self._body() if self.headers.get("Content-Length", "0") != "0" else {}
                    params = validate_run_request(payload)
                    job = app.ai.submit(params)
                    logger.info("AI_JOB_SUBMITTED job=%s mode=%s", job.id, params["mode"])
                    self._json(202, {"ok": True, **job.as_dict(include_result=False)})
                elif path == "/api/ai/warmup":
                    self._json(202, {"ok": True, **app.ai.warmup()})
                else:
                    self._error(404, "NOT_FOUND", "The requested resource was not found.")
            except ValueError as exc:
                self._error(400, "INVALID_REQUEST", str(exc))
            except AIUnavailable as exc:
                self._error(503, "AI_UNAVAILABLE", str(exc))
            except AIQueueFull as exc:
                self._error(429, "AI_BUSY", str(exc))
            except (sqlite3.Error, OSError, RuntimeError):
                logger.exception("API_ERROR method=POST path=%s", path)
                self._error(503, "SERVICE_UNAVAILABLE", "The request could not be completed.")
            except Exception:
                logger.exception("API_ERROR method=POST path=%s", path)
                self._error(500, "INTERNAL_ERROR", "The request could not be completed.")

        def _websocket(self) -> None:
            origin = self.headers.get("Origin")
            if not self._origin_allowed():
                self._error(403, "ORIGIN_NOT_ALLOWED", "This request origin is not allowed.")
                return
            key = self.headers.get("Sec-WebSocket-Key")
            connection_tokens = {token.strip().lower() for token in self.headers.get("Connection", "").split(",")}
            try:
                decoded_key = base64.b64decode(key or "", validate=True)
            except ValueError:
                decoded_key = b""
            if (self.headers.get("Upgrade", "").lower() != "websocket"
                    or "upgrade" not in connection_tokens
                    or self.headers.get("Sec-WebSocket-Version") != "13"
                    or len(decoded_key) != 16):
                self._error(400, "WEBSOCKET_UPGRADE_REQUIRED", "A valid WebSocket version 13 upgrade is required.")
                return
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            if origin and self._origin_trusted(origin):
                self.send_header("Access-Control-Allow-Origin", "*" if "*" in app.allowed_origins else origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            peer = WebSocketPeer(self.connection)
            self.close_connection = True
            role = (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("role", [""])[0] or "").strip().lower()
            if role == "device":
                self._websocket_device(peer)
                return
            if not app.websocket_connected(peer):
                return
            try:
                peer.receive_loop()
            finally:
                app.remove_peer(peer)
                peer.close()

        def _websocket_device(self, peer: WebSocketPeer) -> None:
            """ESP32 firmware can push readings over this socket instead of HTTP POST."""
            remote = self.client_address[0]
            logger.info("DEVICE_WS_CONNECTED ip=%s", remote)
            peer.send({"type": "welcome", "role": "device", "server_time": iso_utc(),
                       "hint": "send one JSON reading object per text frame"})

            def handle_text(text: str) -> None:
                try:
                    payload = json.loads(text)
                except (ValueError, RecursionError):
                    peer.send({"type": "error", "code": "INVALID_JSON",
                               "message": "device message must be a JSON object"})
                    return
                try:
                    result = app.ingest(payload, "esp32", remote)
                except ValueError as exc:
                    logger.warning("DEVICE_WS_REJECTED ip=%s reason=%s", remote, exc)
                    peer.send({"type": "error", "code": "INVALID_READING", "message": str(exc)})
                    return
                except (sqlite3.Error, OSError, RuntimeError):
                    logger.exception("DEVICE_WS_STORAGE_ERROR ip=%s", remote)
                    peer.send({"type": "error", "code": "STORAGE_UNAVAILABLE",
                               "message": "the reading could not be stored"})
                    return
                reading = result["reading"]
                logger.info("DEVICE_WS_READING ip=%s device=%s duplicate=%s",
                            remote, reading["device"], result["duplicate"])
                peer.send({"type": "ack", "device": reading["device"], "timestamp": reading["timestamp"],
                           "duplicate": result["duplicate"], "server_time": iso_utc()})

            try:
                peer.receive_loop(handle_text)
            finally:
                peer.close()
                logger.info("DEVICE_WS_DISCONNECTED ip=%s", remote)

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            # Keep the standard concise request log.
            super().log_request(code, size)

    return Handler


class AgriSenseHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    try:
        port = int(os.getenv("PORT", "5000"))
    except (OverflowError, ValueError) as exc:
        raise SystemExit("PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be between 1 and 65535")
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = AgriSenseApp()
    server = AgriSenseHTTPServer((host, port), build_handler(app))
    app.start_monitor()
    logger.info("SERVER_START host=%s port=%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("SERVER_STOP requested=keyboard_interrupt")
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        logger.info("SERVER_STOP complete=true")


if __name__ == "__main__":
    main()
