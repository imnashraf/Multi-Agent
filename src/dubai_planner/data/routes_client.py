"""Routing and Dubai RTA Nol / taxi fare client (OSRM + cache + Haversine fallback)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from dubai_planner.data.cache import SQLiteCache
from dubai_planner.data.curated import CuratedDataset, haversine_km
from dubai_planner.models.schemas import Provenance, TransitMode, TransitSegment

OSRM_BASE_URL = "https://router.project-osrm.org"

_MODE_SPEED_KMH: dict[TransitMode, float] = {
    "walk": 4.5,
    "metro": 30.0,
    "tram": 22.0,
    "bus": 22.0,
    "taxi": 38.0,
    "abra": 6.0,
}

_MODE_WAIT_MINS: dict[TransitMode, int] = {
    "walk": 0,
    "metro": 10,
    "tram": 8,
    "bus": 10,
    "taxi": 3,
    "abra": 3,
}


class RoutesClient:
    """Computes point-to-point transit durations, distances, and official RTA fares."""

    def __init__(
        self,
        dataset: CuratedDataset,
        cache: SQLiteCache | None = None,
        *,
        offline_mode: bool = False,
        http_client: httpx.Client | None = None,
        osrm_base_url: str = OSRM_BASE_URL,
        cache_ttl_seconds: int = 604800,
    ) -> None:
        self.dataset = dataset
        self.cache = cache
        self.offline_mode = offline_mode
        self._http_client = http_client
        self.osrm_base_url = osrm_base_url.rstrip("/")
        self.cache_ttl_seconds = cache_ttl_seconds

    def estimate_zones_crossed(
        self,
        *,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
        distance_km: float,
        from_zone: int | None = None,
        to_zone: int | None = None,
    ) -> int:
        """Determine RTA fare zones crossed using neighborhood zones or distance fallback."""
        if from_zone is None:
            nearest_from = self.dataset.nearest_neighborhood(from_lat, from_lon)
            if nearest_from:
                from_zone = nearest_from.get("rta_zone")
        if to_zone is None:
            nearest_to = self.dataset.nearest_neighborhood(to_lat, to_lon)
            if nearest_to:
                to_zone = nearest_to.get("rta_zone")

        corridor: dict[str, int] = self.dataset.rta_fares.get("zone_corridor_order", {})
        if (
            from_zone is not None
            and to_zone is not None
            and str(from_zone) in corridor
            and str(to_zone) in corridor
        ):
            return abs(corridor[str(from_zone)] - corridor[str(to_zone)]) + 1

        if distance_km < 6.0:
            return 1
        if distance_km < 15.0:
            return 2
        return 3

    def calculate_rta_fare(self, zones_crossed: int, card_type: str = "silver_nol") -> float:
        """Return official RTA Nol fare in AED for the given number of zones."""
        table = self.dataset.rta_fares.get(card_type) or self.dataset.rta_fares.get(
            "silver_nol", {}
        )
        if zones_crossed <= 1:
            return float(table.get("within_1_zone_aed", 3.0))
        if zones_crossed == 2:
            return float(table.get("two_adjacent_zones_aed", 5.0))
        return float(table.get("three_plus_zones_aed", 7.5))

    def calculate_taxi_fare(self, distance_km: float) -> float:
        """Estimate Dubai taxi fare in AED using RTA meter heuristics."""
        taxi_cfg = self.dataset.rta_fares.get("taxi", {})
        base = float(taxi_cfg.get("base_fare_aed", 5.0))
        per_km = float(taxi_cfg.get("per_km_aed", 2.19))
        minimum = float(taxi_cfg.get("minimum_fare_aed", 12.0))
        return round(max(minimum, base + per_km * max(0.0, distance_km)), 2)

    def calculate_daily_transit_cost(self, segments: list[TransitSegment]) -> float:
        """Compute daily transit spend, applying the AED 20 1-day Nol pass cap when cheaper."""
        rta_public_modes = {"metro", "bus", "tram"}
        public_total = sum(s.estimated_fare_aed for s in segments if s.mode in rta_public_modes)
        day_pass = float(self.dataset.rta_fares.get("one_day_pass_regular_aed", 20.0))
        capped_public = min(public_total, day_pass) if public_total > 0 else 0.0
        other_total = sum(s.estimated_fare_aed for s in segments if s.mode not in rta_public_modes)
        return round(capped_public + other_total, 2)

    def _query_osrm(
        self,
        *,
        profile: str,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
    ) -> tuple[dict[str, float] | None, datetime]:
        cache_key = (
            f"osrm:{profile}:{from_lat:.4f},{from_lon:.4f}->{to_lat:.4f},{to_lon:.4f}"
        )
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return dict(cached.payload), cached.retrieved_at

        if self.offline_mode:
            return None, datetime.now(timezone.utc)

        url = (
            f"{self.osrm_base_url}/route/v1/{profile}/"
            f"{from_lon:.6f},{from_lat:.6f};{to_lon:.6f},{to_lat:.6f}"
        )
        try:
            if self._http_client is not None:
                resp = self._http_client.get(url, params={"overview": "false"}, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(url, params={"overview": "false"})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            routes = data.get("routes") or []
            if not routes:
                return None, datetime.now(timezone.utc)
            payload = {
                "distance_m": float(routes[0]["distance"]),
                "duration_s": float(routes[0]["duration"]),
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return None, datetime.now(timezone.utc)

        retrieved_at = datetime.now(timezone.utc)
        if self.cache is not None:
            retrieved_at = self.cache.set(
                cache_key,
                namespace="osrm",
                payload=payload,
                source_name="osrm",
                ttl_seconds=self.cache_ttl_seconds,
            )
        return payload, retrieved_at

    def choose_mode(
        self,
        *,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
        preferred_mode: TransitMode | None = None,
    ) -> TransitMode:
        """Select a practical default Dubai transit mode when none is specified."""
        if preferred_mode is not None:
            return preferred_mode
        straight_km = haversine_km(from_lat, from_lon, to_lat, to_lon)
        if straight_km <= 0.9:
            return "walk"
        return "metro"

    def get_route(
        self,
        *,
        from_place: str,
        to_place: str,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
        mode: TransitMode | None = None,
        from_zone: int | None = None,
        to_zone: int | None = None,
    ) -> TransitSegment:
        """Compute a TransitSegment using OSRM/cache with Haversine + RTA fare fallback."""
        selected_mode = self.choose_mode(
            from_lat=from_lat,
            from_lon=from_lon,
            to_lat=to_lat,
            to_lon=to_lon,
            preferred_mode=mode,
        )

        if selected_mode == "abra":
            abra_fare = float(self.dataset.rta_fares.get("abra_creek_fare_aed", 1.0))
            return TransitSegment(
                from_place=from_place,
                to_place=to_place,
                mode="abra",
                duration_mins=7,
                distance_km=0.4,
                estimated_fare_aed=abra_fare,
                provenance=Provenance(
                    source_type="verified_dataset",
                    source_name="rta_nol_fares",
                    last_verified=self.dataset.last_verified,
                ),
            )

        osrm_profile = "foot" if selected_mode == "walk" else "driving"
        osrm_data, retrieved_at = self._query_osrm(
            profile=osrm_profile,
            from_lat=from_lat,
            from_lon=from_lon,
            to_lat=to_lat,
            to_lon=to_lon,
        )

        if osrm_data is not None:
            distance_km = round(osrm_data["distance_m"] / 1000.0, 2)
            base_mins = osrm_data["duration_s"] / 60.0
            if selected_mode in {"metro", "tram", "bus"}:
                duration_mins = max(5, round(base_mins + _MODE_WAIT_MINS[selected_mode]))
            else:
                duration_mins = max(1, round(base_mins))
            provenance = Provenance(
                source_type="api_derived",
                source_name="osrm",
                retrieved_at=retrieved_at,
            )
        else:
            straight_km = haversine_km(from_lat, from_lon, to_lat, to_lon)
            distance_km = round(straight_km * 1.35, 2)
            speed = _MODE_SPEED_KMH[selected_mode]
            wait = _MODE_WAIT_MINS[selected_mode]
            travel_mins = (distance_km / speed) * 60.0 if speed > 0 else 0.0
            duration_mins = max(2, round(travel_mins + wait))
            provenance = Provenance(
                source_type="estimated",
                source_name="haversine_rta_fallback",
                last_verified=self.dataset.last_verified,
            )

        if selected_mode == "walk":
            fare_aed = 0.0
        elif selected_mode == "taxi":
            fare_aed = self.calculate_taxi_fare(distance_km)
        else:
            zones = self.estimate_zones_crossed(
                from_lat=from_lat,
                from_lon=from_lon,
                to_lat=to_lat,
                to_lon=to_lon,
                distance_km=distance_km,
                from_zone=from_zone,
                to_zone=to_zone,
            )
            fare_aed = self.calculate_rta_fare(zones)

        return TransitSegment(
            from_place=from_place,
            to_place=to_place,
            mode=selected_mode,
            duration_mins=duration_mins,
            distance_km=distance_km,
            estimated_fare_aed=fare_aed,
            provenance=provenance,
        )
