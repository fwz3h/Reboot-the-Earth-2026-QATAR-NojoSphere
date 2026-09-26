"""Bridge between the AgriSense HTTP API and the vendored AI core (agri_ai.py).

Design notes
------------
* The AI core is imported *lazily*. Importing it pulls in OpenCV and NumPy, and
  it also trains two networks on first use, so the dashboard backend must not
  pay that cost at start-up -- and must keep working on a machine where the
  heavy dependencies are absent entirely.
* One run can take minutes (three external HTTP calls, two small network
  trainings, camera capture). Running that inline in an HTTP handler would trip
  the handler's socket timeout and block other requests, so runs are submitted
  as jobs and executed by a single background worker. A single worker is
  deliberate: the camera and the models are process-wide resources, and
  serialising runs keeps their SQLite writes simple.
* Nothing here fabricates output. If a dependency is missing, or the job fails,
  the caller gets a specific reason and the package names to install.
"""
from __future__ import annotations

import queue
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

import dataclasses

MAX_QUEUED_JOBS = 4
MAX_RETAINED_JOBS = 25
JOB_RESULT_TTL_SECONDS = 30 * 60
OLLAMA_PROBE_TTL_SECONDS = 15.0
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

MODES = ("plan", "health", "both")


class AIUnavailable(RuntimeError):
    """The AI core cannot run here, with the package list needed to fix it."""

    def __init__(self, message: str, missing: list[str] | None = None):
        super().__init__(message)
        self.missing = list(missing or [])


class AIQueueFull(RuntimeError):
    """Too many runs are already queued or in flight."""


class AIRequestError(ValueError):
    """The submitted run parameters are not usable."""


def iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_core():
    """Import the vendored AI core on first use, once, thread-safely."""
    global _CORE, _CORE_ERROR
    if _CORE is not None or _CORE_ERROR is not None:
        return _CORE
    with _CORE_LOCK:
        if _CORE is None and _CORE_ERROR is None:
            try:
                from backend import agri_ai
                _CORE = agri_ai
            except Exception as exc:                      # pragma: no cover - defensive
                _CORE_ERROR = f"{type(exc).__name__}: {exc}"
    if _CORE is None:
        raise AIUnavailable(f"The AI core could not be imported: {_CORE_ERROR}")
    return _CORE


_CORE = None
_CORE_ERROR: str | None = None
_CORE_LOCK = threading.Lock()


def _number(field: str, value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise AIRequestError(f"{field} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AIRequestError(f"{field} must be a number") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise AIRequestError(f"{field} must be a finite number")
    if not minimum <= number <= maximum:
        raise AIRequestError(f"{field} must be between {minimum} and {maximum}")
    return number


def validate_run_request(payload: Any) -> dict[str, Any]:
    """Validates and normalises a run request.

    Ranges mirror the AI core's own safety ranges (irrigation control limits)
    so the API rejects out-of-range values at the boundary rather than letting
    them reach the controller.
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise AIRequestError("body must be a JSON object")

    allowed = {"latitude", "longitude", "budget", "soil_moisture_pct", "air_temp_c",
               "air_humidity_pct", "ldr_pct", "crop_moisture_threshold_pct",
               "camera_index", "mode", "region", "only_validated_fields"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise AIRequestError(f"unexpected field(s): {', '.join(unknown)}")

    mode = payload.get("mode", "plan")
    if not isinstance(mode, str) or mode.strip().lower() not in MODES:
        raise AIRequestError(f"mode must be one of {', '.join(MODES)}")

    camera_index = payload.get("camera_index", 0)
    if isinstance(camera_index, bool) or not isinstance(camera_index, int):
        raise AIRequestError("camera_index must be an integer")
    if not 0 <= camera_index <= 10:
        raise AIRequestError("camera_index must be between 0 and 10")

    region = payload.get("region")
    if region in (None, ""):
        region = None
    else:
        if not isinstance(region, str):
            raise AIRequestError("region must be a string")
        region = region.strip().lower()
        if len(region) > 40 or not all(char.isalnum() or char in "_- " for char in region):
            raise AIRequestError("region must be up to 40 characters of letters, digits, _ - or space")

    return {
        "latitude": _number("latitude", payload.get("latitude"), -90.0, 90.0),
        "longitude": _number("longitude", payload.get("longitude"), -180.0, 180.0),
        "budget": _number("budget", payload.get("budget", 0), 0.0, 1e9),
        "soil_moisture_pct": _number("soil_moisture_pct", payload.get("soil_moisture_pct"), 0.0, 100.0),
        "air_temp_c": _number("air_temp_c", payload.get("air_temp_c"), -10.0, 60.0),
        "air_humidity_pct": _number("air_humidity_pct", payload.get("air_humidity_pct"), 0.0, 100.0),
        "ldr_pct": _number("ldr_pct", payload.get("ldr_pct"), 0.0, 100.0),
        "crop_moisture_threshold_pct": _number(
            "crop_moisture_threshold_pct", payload.get("crop_moisture_threshold_pct", 40.0), 0.0, 100.0),
        "camera_index": camera_index,
        "mode": mode.strip().lower(),
        "region": region,
    }


@dataclasses.dataclass
class Job:
    id: str
    status: str                      # queued | running | succeeded | failed
    params: dict[str, Any]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    duration_seconds: float | None = None
    _queued_at: float = dataclasses.field(default_factory=time.monotonic)

    def as_dict(self, include_result: bool = True) -> dict[str, Any]:
        payload = {
            "job_id": self.id,
            "status": self.status,
            "params": self.params,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
        }
        if self.error is not None:
            payload["error"] = self.error
        if include_result and self.result is not None:
            payload["result"] = self.result
        return payload


class AIAdvisor:
    """Owns AI availability reporting and the job queue."""

    def __init__(self, max_queued: int = MAX_QUEUED_JOBS):
        self._max_queued = max_queued
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._counter = 0
        self._warm_state = "cold"        # cold | training | ready | failed
        self._warm_error: str | None = None
        self._warm_thread: threading.Thread | None = None
        self._ollama_checked_at = 0.0
        self._ollama_reachable: bool | None = None

    # -- availability ----------------------------------------------------

    def core_capabilities(self) -> dict[str, Any]:
        try:
            return _load_core().capabilities()
        except AIUnavailable as exc:
            return {
                "torch_available": False, "torch_error": str(exc),
                "vision_available": False, "vision_error": str(exc),
                "openai_client_available": False,
                "missing_packages": exc.missing or ["torch", "opencv-python", "numpy"],
                "pipeline_runnable": False,
                "notes": ["The AI core module could not be imported at all."],
            }

    def _probe_ollama(self, force: bool = False) -> bool:
        """Probe the local Ollama server, cached briefly so the status endpoint
        stays fast and does not hammer it.

        Named `_probe_*` deliberately: an earlier version called this method and
        the cached-result attribute the same thing, so the attribute shadowed the
        method and status() raised TypeError on any machine that had the openai
        client installed.
        """
        now = time.monotonic()
        if not force and self._ollama_reachable is not None and now - self._ollama_checked_at < OLLAMA_PROBE_TTL_SECONDS:
            return self._ollama_reachable
        reachable = False
        try:
            with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=1.5) as response:
                reachable = response.status == 200
        except Exception:
            reachable = False
        self._ollama_reachable = reachable
        self._ollama_checked_at = now
        return reachable

    def status(self) -> dict[str, Any]:
        core = self.core_capabilities()
        runnable = bool(core.get("pipeline_runnable"))
        with self._lock:
            warm_state = self._warm_state
            warm_error = self._warm_error
            pending = sum(1 for job in self._jobs.values() if job.status in ("queued", "running"))
        try:
            from backend.agri_ai import PROXY_DATA_DISCLOSURE
        except Exception:
            PROXY_DATA_DISCLOSURE = None

        if not runnable:
            reason = ("Missing dependency: " + ", ".join(core.get("missing_packages") or [])
                      + ". Install with: pip install " + " ".join(core.get("missing_packages") or []))
        else:
            reason = None
        return {
            "available": runnable,
            "reason": reason,
            "missing_packages": core.get("missing_packages") or [],
            "torch_available": core.get("torch_available"),
            "vision_available": core.get("vision_available"),
            "reasoning_client_available": core.get("openai_client_available"),
            "reasoning_server_reachable": self._probe_ollama() if core.get("openai_client_available") else False,
            "model_state": warm_state,
            "model_error": warm_error,
            "jobs_queued_or_running": pending,
            "jobs_capacity": self._max_queued,
            "modes": list(MODES),
            "proxy_data_disclosure": PROXY_DATA_DISCLOSURE,
            "notes": core.get("notes") or [],
        }

    # -- warm-up ---------------------------------------------------------

    def warmup(self) -> dict[str, Any]:
        """Train both networks ahead of the first request.

        Training is the slow part of a first run and it is cached process-wide,
        so paying for it in the background turns the first user request from
        'several minutes' into 'one pipeline pass'.
        """
        core = self.core_capabilities()
        if not core.get("pipeline_runnable"):
            raise AIUnavailable("The AI core cannot run without: "
                                + ", ".join(core.get("missing_packages") or []),
                                core.get("missing_packages") or [])
        with self._lock:
            if self._warm_state in ("training", "ready"):
                return {"state": self._warm_state}
            self._warm_state = "training"
            self._warm_error = None
            if self._warm_thread is None or not self._warm_thread.is_alive():
                self._warm_thread = threading.Thread(target=self._warm_worker, name="ai-warmup", daemon=True)
                self._warm_thread.start()
            return {"state": self._warm_state}

    def _warm_worker(self) -> None:
        try:
            core = _load_core()
            core._ensure_state_model()
            core._ensure_weather_model()
            with self._lock:
                self._warm_state = "ready"
        except Exception as exc:
            with self._lock:
                self._warm_state = "failed"
                self._warm_error = f"{type(exc).__name__}: {exc}"

    # -- job lifecycle ---------------------------------------------------

    def submit(self, params: dict[str, Any]) -> Job:
        core = self.core_capabilities()
        if not core.get("pipeline_runnable"):
            missing = core.get("missing_packages") or []
            raise AIUnavailable(
                "The AI core cannot run on this machine. Missing: "
                + ", ".join(missing)
                + (f". Install with: pip install {' '.join(missing)}" if missing else ""),
                missing,
            )
        with self._lock:
            active = sum(1 for job in self._jobs.values() if job.status in ("queued", "running"))
            if active >= self._max_queued:
                raise AIQueueFull(f"{active} AI run(s) already queued or running; try again shortly.")
            self._counter += 1
            job_id = f"ai-{int(time.time())}-{self._counter:04d}"
            job = Job(id=job_id, status="queued", params=params, created_at=iso_utc())
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._prune_locked()
            self._ensure_worker_locked()
            # Queued inside the lock, so the capacity check above is exact. The queue
            # itself is unbounded, so this never blocks while holding the lock.
            self._queue.put(job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _ensure_worker_locked(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run_worker, name="ai-advisor", daemon=True)
            self._worker.start()

    def _prune_locked(self) -> None:
        """Keep the registry bounded: retire stale results, then the oldest ones."""
        now = time.monotonic()
        keep: list[str] = []
        for job_id in self._order:
            job = self._jobs.get(job_id)
            if job is not None and job_is_stale(job, now):
                self._jobs.pop(job_id, None)
                continue
            keep.append(job_id)
        self._order = keep
        if len(self._order) <= MAX_RETAINED_JOBS:
            return
        drop = len(self._order) - MAX_RETAINED_JOBS
        keep = []
        for job_id in self._order:
            job = self._jobs.get(job_id)
            if drop > 0 and job is not None and job.status in ("succeeded", "failed"):
                self._jobs.pop(job_id, None)
                drop -= 1
                continue
            keep.append(job_id)
        self._order = keep

    def _run_worker(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._execute(job_id)
            except Exception:                      # never let the worker die
                pass
            finally:
                self._queue.task_done()

    def _execute(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
            job.started_at = iso_utc()
            params = dict(job.params)
        started = time.monotonic()
        try:
            core = _load_core()
            result = core.run_pipeline(
                latitude=params["latitude"],
                longitude=params["longitude"],
                budget=params["budget"],
                soil_moisture_pct=params["soil_moisture_pct"],
                air_temp_c=params["air_temp_c"],
                air_humidity_pct=params["air_humidity_pct"],
                ldr_pct=params["ldr_pct"],
                crop_moisture_threshold_pct=params["crop_moisture_threshold_pct"],
                camera_index=params["camera_index"],
                mode=params["mode"],
                region=params["region"],
            )
            # _forecast_log_id is an internal handle for record_actual_weather();
            # it is not meaningful to the dashboard.
            result.pop("_forecast_log_id", None)
            with self._lock:
                job.result = result
                job.status = "succeeded"
        except Exception as exc:
            missing = getattr(exc, "missing", [])
            with self._lock:
                job.status = "failed"
                job.error = {
                    "code": "AI_DEPENDENCY_MISSING" if missing else "AI_RUN_FAILED",
                    "message": str(exc),
                    "missing_packages": missing,
                }
        finally:
            with self._lock:
                job.finished_at = iso_utc()
                job.duration_seconds = round(time.monotonic() - started, 2)

    # -- housekeeping ----------------------------------------------------

    def close(self) -> None:
        """Mark the queue closed so a shutdown does not hang on a run."""
        with self._lock:
            self._max_queued = 0


def job_is_stale(job: Job, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    return job.status in ("succeeded", "failed") and now - job._queued_at > JOB_RESULT_TTL_SECONDS
