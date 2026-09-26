"""Transparent site/crop screening from summarized GIS layer values.

This is a planning aid, not a geographic information system or agronomic guarantee:
users supply/export the layer summaries and replace the illustrative yield library and
all economic assumptions with local extension advice and buyer/vendor quotes.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

LAYER_SCHEMA: dict[str, dict[str, Any]] = {
    "solar_kwh_m2_day": {"label": "Solar exposure", "unit": "kWh/m²/day", "minimum": 0, "maximum": 15,
                         "aliases": ["solar", "solar_irradiance", "irradiance", "ghi", "solar_exposure"]},
    "annual_rainfall_mm": {"label": "Annual rainfall", "unit": "mm/year", "minimum": 0, "maximum": 15000,
                            "aliases": ["rainfall", "precipitation", "annual_precipitation", "annual_rainfall"]},
    "soil_ph": {"label": "Soil pH", "unit": "pH", "minimum": 0, "maximum": 14,
                "aliases": ["ph", "soil_ph", "soilph"]},
    "soil_salinity_ds_m": {"label": "Soil salinity", "unit": "dS/m", "minimum": 0, "maximum": 100,
                            "aliases": ["salinity", "soil_salinity", "ec", "electrical_conductivity"]},
    "wind_kmh": {"label": "Peak wind", "unit": "km/h", "minimum": 0, "maximum": 400,
                 "aliases": ["wind", "wind_speed", "max_wind", "wind_kmh"]},
    "dust_risk": {"label": "Wind / dust risk", "unit": "/100", "minimum": 0, "maximum": 100,
                  "aliases": ["dust", "dust_risk", "wind_dust_risk"]},
    "water_availability": {"label": "Water availability", "unit": "/100", "minimum": 0, "maximum": 100,
                           "aliases": ["water", "water_availability", "irrigation_access"]},
    "mean_temp_c": {"label": "Mean growing-season temperature", "unit": "°C", "minimum": -20, "maximum": 60,
                    "aliases": ["temperature", "mean_temp", "mean_temp_c", "growing_season_temp"]},
    "slope_pct": {"label": "Slope", "unit": "%", "minimum": 0, "maximum": 200,
                  "aliases": ["slope", "slope_pct", "terrain_slope"]},
    "market_distance_km": {"label": "Distance to market", "unit": "km", "minimum": 0, "maximum": 5000,
                            "aliases": ["market_distance", "distance_to_market", "market_distance_km"]},
}
REQUIRED_LAYERS = ("solar_kwh_m2_day", "soil_ph", "soil_salinity_ds_m", "water_availability", "market_distance_km")
AT_LEAST_ONE_LAYER_GROUP = (("wind_kmh", "dust_risk"),)

# Suitability ranges and annual reference yields are deliberately conservative planning
# defaults. They are tunable, generalized assumptions—not location-specific crop advice.
CROPS: list[dict[str, Any]] = [
    {"id": "maize", "name": "Maize", "systems": ["open_field"], "yield_t_ha": 6.0,
     "ranges": {"solar_kwh_m2_day": (4.5, 9.5), "annual_rainfall_mm": (500, 1200), "soil_ph": (5.5, 7.5), "soil_salinity_ds_m": (0, 2), "wind_kmh": (0, 35), "water_availability": (50, 100), "mean_temp_c": (18, 32), "slope_pct": (0, 8), "market_distance_km": (0, 150)}},
    {"id": "wheat", "name": "Wheat", "systems": ["open_field"], "yield_t_ha": 4.5,
     "ranges": {"solar_kwh_m2_day": (4, 9), "annual_rainfall_mm": (300, 900), "soil_ph": (6, 7.5), "soil_salinity_ds_m": (0, 6), "wind_kmh": (0, 35), "water_availability": (35, 100), "mean_temp_c": (10, 25), "slope_pct": (0, 10), "market_distance_km": (0, 150)}},
    {"id": "rice", "name": "Rice", "systems": ["open_field"], "yield_t_ha": 5.5,
     "ranges": {"solar_kwh_m2_day": (4, 9), "annual_rainfall_mm": (1000, 2500), "soil_ph": (5.5, 7), "soil_salinity_ds_m": (0, 3), "wind_kmh": (0, 30), "water_availability": (75, 100), "mean_temp_c": (20, 32), "slope_pct": (0, 3), "market_distance_km": (0, 150)}},
    {"id": "soybean", "name": "Soybean", "systems": ["open_field"], "yield_t_ha": 2.8,
     "ranges": {"solar_kwh_m2_day": (4.5, 9), "annual_rainfall_mm": (450, 900), "soil_ph": (6, 7.5), "soil_salinity_ds_m": (0, 4), "wind_kmh": (0, 35), "water_availability": (45, 100), "mean_temp_c": (20, 30), "slope_pct": (0, 8), "market_distance_km": (0, 150)}},
    {"id": "tomato", "name": "Tomato", "systems": ["open_field", "greenhouse", "hydroponic"], "yield_t_ha": 45,
     "ranges": {"solar_kwh_m2_day": (4, 9.5), "annual_rainfall_mm": (400, 900), "soil_ph": (6, 7.5), "soil_salinity_ds_m": (0, 2.5), "wind_kmh": (0, 25), "water_availability": (55, 100), "mean_temp_c": (18, 30), "slope_pct": (0, 6), "market_distance_km": (0, 100)}},
    {"id": "lettuce", "name": "Lettuce", "systems": ["open_field", "greenhouse", "hydroponic"], "yield_t_ha": 30,
     "ranges": {"solar_kwh_m2_day": (3.5, 8), "annual_rainfall_mm": (350, 800), "soil_ph": (6, 7), "soil_salinity_ds_m": (0, 2), "wind_kmh": (0, 30), "water_availability": (50, 100), "mean_temp_c": (10, 24), "slope_pct": (0, 8), "market_distance_km": (0, 80)}},
    {"id": "cucumber", "name": "Cucumber", "systems": ["open_field", "greenhouse", "hydroponic"], "yield_t_ha": 35,
     "ranges": {"solar_kwh_m2_day": (4, 9), "annual_rainfall_mm": (400, 1000), "soil_ph": (5.8, 7), "soil_salinity_ds_m": (0, 2.5), "wind_kmh": (0, 25), "water_availability": (55, 100), "mean_temp_c": (18, 30), "slope_pct": (0, 6), "market_distance_km": (0, 100)}},
    {"id": "pepper", "name": "Sweet pepper", "systems": ["open_field", "greenhouse", "hydroponic"], "yield_t_ha": 25,
     "ranges": {"solar_kwh_m2_day": (4.5, 9), "annual_rainfall_mm": (500, 1200), "soil_ph": (6, 7.5), "soil_salinity_ds_m": (0, 3), "wind_kmh": (0, 30), "water_availability": (50, 100), "mean_temp_c": (20, 32), "slope_pct": (0, 8), "market_distance_km": (0, 100)}},
]
SYSTEMS: dict[str, dict[str, Any]] = {
    "open_field": {"name": "Open field", "description": "Lower infrastructure; more exposed to weather and site conditions.",
                   "yield_multiplier": 1.0, "ranges": {"solar_kwh_m2_day": (3.5, 10), "annual_rainfall_mm": (350, 1600), "soil_ph": (5.5, 7.5), "soil_salinity_ds_m": (0, 4), "wind_kmh": (0, 30), "water_availability": (45, 100), "mean_temp_c": (12, 31), "slope_pct": (0, 7), "market_distance_km": (0, 120)}},
    "greenhouse": {"name": "Greenhouse", "description": "Protected cultivation; higher infrastructure needs, useful for selected high-value crops.",
                   "yield_multiplier": 2.0, "ranges": {"solar_kwh_m2_day": (4, 10), "soil_ph": (5, 8), "soil_salinity_ds_m": (0, 12), "wind_kmh": (0, 55), "water_availability": (35, 100), "mean_temp_c": (8, 35), "market_distance_km": (0, 80)}},
    "hydroponic": {"name": "Hydroponic", "description": "Soilless controlled production; needs reliable water, power, skills, and nearby buyers.",
                   "yield_multiplier": 2.0, "ranges": {"solar_kwh_m2_day": (3, 10), "water_availability": (65, 100), "mean_temp_c": (12, 30), "market_distance_km": (0, 45)}},
}
SYSTEM_MULTIPLIERS = {
    ("tomato", "greenhouse"): 2.4, ("tomato", "hydroponic"): 1.8,
    ("lettuce", "greenhouse"): 1.7, ("lettuce", "hydroponic"): 2.3,
    ("cucumber", "greenhouse"): 2.0, ("cucumber", "hydroponic"): 1.8,
    ("pepper", "greenhouse"): 2.0, ("pepper", "hydroponic"): 1.7,
}
SCENARIO_FACTORS = {"conservative": 0.8, "base": 1.0, "optimistic": 1.2}
CURRENCY_CODES = ("USD", "EUR", "INR", "KES", "NGN", "GHS", "TZS", "UGX", "BRL", "TRY")


def score_range(value: float, lower: float, upper: float) -> float:
    """100 in the ideal interval; fades smoothly to zero outside it."""
    if lower <= value <= upper:
        return 100.0
    margin = max((upper - lower) * 0.5, 0.25)
    distance = lower - value if value < lower else value - upper
    return max(0.0, 100.0 * (1 - distance / margin))


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


class SiteSuitabilityEngine:
    """Scores summarized site layers, then returns user-budgeted crop/system scenarios."""

    def schema(self) -> dict[str, Any]:
        return {
            "engine": "transparent-rule-based-screening",
            "layer_inputs": [{"key": key, **{name: value for name, value in metadata.items() if name != "aliases"}}
                             for key, metadata in LAYER_SCHEMA.items()],
            "required_layers": list(REQUIRED_LAYERS),
            "at_least_one_of": [list(group) for group in AT_LEAST_ONE_LAYER_GROUP],
            "production_systems": [{"id": key, "name": value["name"], "description": value["description"]}
                                   for key, value in SYSTEMS.items()],
            "crops": [{"id": crop["id"], "name": crop["name"], "systems": crop["systems"],
                       "reference_yield_t_ha_year": crop["yield_t_ha"]} for crop in CROPS],
            "scenario_factors": SCENARIO_FACTORS,
            "disclaimer": "Preliminary screening only. Crop ranges and yields are generalized reference assumptions, not a site-specific agronomic or financial forecast.",
        }

    def recommend(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("request must be a JSON object")
        site = payload.get("site")
        layers = payload.get("layers")
        economics = payload.get("economics")
        if not isinstance(site, dict) or not isinstance(layers, dict) or not isinstance(economics, dict):
            raise ValueError("site, layers, and economics objects are required")
        area_ha = _number(site.get("area_ha"), "site.area_ha", 0.01, 100_000)
        site_name = site.get("name", "My field")
        if not isinstance(site_name, str) or not site_name.strip() or len(site_name) > 100:
            raise ValueError("site.name must be 1-100 characters")
        parsed_layers: dict[str, float] = {}
        for key, value in layers.items():
            if key not in LAYER_SCHEMA:
                raise ValueError(f"unknown GIS layer value: {key}")
            meta = LAYER_SCHEMA[key]
            parsed_layers[key] = _number(value, f"layers.{key}", meta["minimum"], meta["maximum"])
        missing = [key for key in REQUIRED_LAYERS if key not in parsed_layers]
        if missing:
            raise ValueError("required GIS layer summaries missing: " + ", ".join(missing))
        missing_groups = [group for group in AT_LEAST_ONE_LAYER_GROUP if not any(key in parsed_layers for key in group)]
        if missing_groups:
            raise ValueError("provide at least one wind/dust layer summary: " + " or ".join(missing_groups[0]))

        currency = economics.get("currency", "USD")
        if currency not in CURRENCY_CODES:
            raise ValueError("economics.currency must be a supported ISO currency code")
        budget = _number(economics.get("budget"), "economics.budget", 0, 1e15)
        price = _number(economics.get("price_per_ton"), "economics.price_per_ton", 0.01, 1e12)
        system_costs = economics.get("systems")
        if not isinstance(system_costs, dict):
            raise ValueError("economics.systems must include cost assumptions for each production system")
        costs: dict[str, dict[str, float]] = {}
        for system_id in SYSTEMS:
            values = system_costs.get(system_id)
            if not isinstance(values, dict):
                raise ValueError(f"economics.systems.{system_id} capex_per_ha and opex_per_ha are required")
            costs[system_id] = {
                "capex_per_ha": _number(values.get("capex_per_ha"), f"economics.systems.{system_id}.capex_per_ha", 0, 1e12),
                "opex_per_ha": _number(values.get("opex_per_ha"), f"economics.systems.{system_id}.opex_per_ha", 0, 1e12),
            }
        site_location = site.get("location")
        if site_location is not None:
            if not isinstance(site_location, dict):
                raise ValueError("site.location must be an object when provided")
            site_location = {
                "latitude": _number(site_location.get("latitude"), "site.location.latitude", -90, 90),
                "longitude": _number(site_location.get("longitude"), "site.location.longitude", -180, 180),
                "accuracy_m": _number(site_location.get("accuracy_m"), "site.location.accuracy_m", 0, 100_000),
            }
            captured_at = site.get("location_captured_at")
            if captured_at is not None:
                if not isinstance(captured_at, str) or len(captured_at) > 64:
                    raise ValueError("site.location_captured_at must be a short ISO-8601 string")
                try:
                    parsed_at = dt.datetime.fromisoformat(captured_at.strip().replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError("site.location_captured_at must be a valid ISO-8601 date-time") from exc
                site_location["captured_at"] = parsed_at.isoformat().replace("+00:00", "Z")
        layer_sources = payload.get("layer_sources", [])
        if not isinstance(layer_sources, list) or any(not isinstance(name, str) or len(name) > 160 for name in layer_sources):
            raise ValueError("layer_sources must be a list of short file/source names")

        completeness = sum(key in parsed_layers for key in LAYER_SCHEMA) / len(LAYER_SCHEMA)
        recommendations = []
        for crop in CROPS:
            crop_score, crop_factors, crop_coverage = self._score(parsed_layers, crop["ranges"])
            for system_id in crop["systems"]:
                system = SYSTEMS[system_id]
                system_score, system_factors, system_coverage = self._score(parsed_layers, system["ranges"])
                score = round(crop_score * 0.72 + system_score * 0.28)
                factor = 0.55 + score / 200
                yield_per_ha = crop["yield_t_ha"] * SYSTEM_MULTIPLIERS.get((crop["id"], system_id), system["yield_multiplier"]) * factor
                cost = costs[system_id]
                capex = cost["capex_per_ha"] * area_ha
                opex = cost["opex_per_ha"] * area_ha
                scenarios = {}
                for scenario, scenario_factor in SCENARIO_FACTORS.items():
                    tonnes = yield_per_ha * area_ha * scenario_factor
                    revenue = tonnes * price
                    net = revenue - opex
                    scenarios[scenario] = {
                        "yield_factor": scenario_factor,
                        "yield_tonnes": round(tonnes, 2),
                        "gross_revenue": round(revenue, 2),
                        "startup_capex": round(capex, 2),
                        "annual_opex": round(opex, 2),
                        "net_operating_estimate": round(net, 2),
                        "payback_years": round(capex / net, 2) if net > 0 else None,
                    }
                recommendations.append({
                    "crop": {"id": crop["id"], "name": crop["name"]},
                    "production_system": {"id": system_id, "name": system["name"]},
                    "suitability_score": score,
                    "confidence": round(100 * (crop_coverage * 0.72 + system_coverage * 0.28)),
                    "band": "strong" if score >= 80 else "promising" if score >= 65 else "cautious" if score >= 45 else "low-fit",
                    "factors": crop_factors,
                    "system_factors": system_factors,
                    "reference_yield_t_ha_year": round(yield_per_ha, 2),
                    "cost_estimates": {"currency": currency, "startup_capex": round(capex, 2),
                                       "annual_opex": round(opex, 2), "budget": round(budget, 2),
                                       "budget_fit": capex <= budget,
                                       "funding_gap": round(max(0, capex - budget), 2)},
                    "scenarios": scenarios,
                })
        # First prefer options the grower can actually fund; within each budget
        # group, prioritize biophysical fit and then lower capital requirements.
        recommendations.sort(key=lambda item: (
            not item["cost_estimates"]["budget_fit"], -item["suitability_score"],
            item["cost_estimates"]["startup_capex"], item["crop"]["name"]
        ))
        site_result = {"name": site_name.strip(), "area_ha": area_ha}
        if site_location is not None:
            site_result["location"] = site_location
        return {
            "site": site_result,
            "layer_sources": layer_sources,
            "layer_completeness_percent": round(completeness * 100),
            "recommendations": recommendations[:12],
            "assumptions": [
                "This is a rule-based first-pass screen of user-supplied GIS layer summaries, not an AI/ML model or a substitute for local agronomic review.",
                "Crop suitability ranges and annual reference yields are generalized planning assumptions; validate cultivar, season, soil tests, climate, and water rights locally.",
                "All economic results use the prices, available capital, capex/ha, and opex/ha entered for this site. No land, finance, tax, transport, labor, utilities, or price volatility is included unless reflected in your cost inputs.",
                "Yield scenarios are 80%, 100%, and 120% of the screened reference estimate; payback is startup capex divided by positive annual operating estimate and excludes financing and replacement costs.",
            ],
        }

    @staticmethod
    def _score(layers: dict[str, float], ranges: dict[str, tuple[float, float]]) -> tuple[float, list[dict[str, Any]], float]:
        scores = []
        factors = []
        weights = {"solar_kwh_m2_day": 1.0, "annual_rainfall_mm": 1.0, "soil_ph": 1.2,
                   "soil_salinity_ds_m": 1.2, "wind_kmh": 0.8, "dust_risk": 0.8,
                   "water_availability": 1.2, "mean_temp_c": 1.0, "slope_pct": 0.7,
                   "market_distance_km": 0.8}
        for key, (lower, upper) in ranges.items():
            if key not in layers:
                continue
            value = layers[key]
            score = 100 - value if key == "dust_risk" else score_range(value, lower, upper)
            weight = weights.get(key, 1.0)
            scores.append((score, weight))
            factors.append({"key": key, "label": LAYER_SCHEMA[key]["label"], "value": value,
                            "unit": LAYER_SCHEMA[key]["unit"], "score": round(score),
                            "status": "good" if score >= 75 else "watch" if score >= 45 else "risk"})
        total_weight = sum(weight for _, weight in scores)
        overall = sum(score * weight for score, weight in scores) / total_weight if total_weight else 0
        possible_weight = sum(weights.get(key, 1.0) for key in ranges)
        coverage = total_weight / possible_weight if possible_weight else 0
        factors.sort(key=lambda factor: factor["score"])
        return round(overall), factors, coverage
