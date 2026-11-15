"""Unified DataService router: Free Open API -> SQLite Cache -> Curated Local DB."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx

from dubai_planner.config import Settings, load_settings
from dubai_planner.data.cache import SQLiteCache
from dubai_planner.data.curated import CuratedDataset
from dubai_planner.data.hotels_client import HotelsClient
from dubai_planner.data.places_client import PlacesClient
from dubai_planner.data.routes_client import RoutesClient
from dubai_planner.data.weather_client import WeatherClient, WeatherSummary
from dubai_planner.models.schemas import (
    AccommodationOption,
    CrowdPreference,
    HotelPreference,
    PlaceCandidate,
    Provenance,
    TransitMode,
    TransitSegment,
    TravelDates,
)


class DataService:
    """Facade routing Dubai data lookups across free APIs, SQLite cache, and local JSON."""

    def __init__(
        self,
        *,
        curated_data_path: str | Path,
        cache_db_path: str | Path | None = None,
        offline_mode: bool = False,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.dataset = CuratedDataset(curated_data_path)
        self.cache = SQLiteCache(cache_db_path) if cache_db_path is not None else None
        self.offline_mode = offline_mode

        self.places = PlacesClient(
            self.dataset,
            self.cache,
            offline_mode=offline_mode,
            http_client=http_client,
        )
        self.routes = RoutesClient(
            self.dataset,
            self.cache,
            offline_mode=offline_mode,
            http_client=http_client,
        )
        self.hotels = HotelsClient(self.dataset)
        self.weather = WeatherClient(
            self.dataset,
            self.cache,
            offline_mode=offline_mode,
            http_client=http_client,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> DataService:
        """Instantiate DataService using application Settings."""
        cfg = settings or load_settings()
        assert cfg.curated_data_path is not None
        assert cfg.cache_db_path is not None
        return cls(
            curated_data_path=cfg.curated_data_path,
            cache_db_path=cfg.cache_db_path,
            offline_mode=cfg.offline_mode,
            http_client=http_client,
        )

    def get_neighborhoods(
        self, *, crowd_preference: CrowdPreference = "any"
    ) -> list[PlaceCandidate]:
        """Return curated Dubai neighborhoods as PlaceCandidate records."""
        results: list[PlaceCandidate] = []
        for item in self.dataset.neighborhoods:
            crowd_level = item.get("crowd_level", "medium")
            if crowd_preference == "low" and crowd_level == "high":
                continue
            results.append(
                PlaceCandidate(
                    id=item["id"],
                    name=item["name"],
                    category="neighborhood",
                    area=item["name"],
                    lat=float(item["lat"]),
                    lon=float(item["lon"]),
                    crowd_level=crowd_level,
                    estimated_cost_aed=0.0,
                    duration_mins=60,
                    description=item.get("vibe"),
                    tags=list(item.get("best_for", [])),
                    provenance=Provenance(
                        source_type="verified_dataset",
                        source_name="dubai_curated.json",
                        last_verified=self.dataset.last_verified,
                    ),
                )
            )
        return results

    def search_attractions(
        self,
        *,
        interests: list[str] | None = None,
        crowd_preference: CrowdPreference = "any",
        max_cost_aed: float | None = None,
        area: str | None = None,
        include_live: bool = True,
        limit: int = 20,
    ) -> list[PlaceCandidate]:
        """Search attractions and beaches via Overpass/cache + curated fallback."""
        return self.places.search_attractions(
            interests=interests,
            crowd_preference=crowd_preference,
            max_cost_aed=max_cost_aed,
            area=area,
            include_live=include_live,
            limit=limit,
        )

    def search_restaurants(
        self,
        *,
        interests: list[str] | None = None,
        crowd_preference: CrowdPreference = "any",
        max_meal_aed: float | None = None,
        area: str | None = None,
        local_only: bool = False,
        include_live: bool = True,
        limit: int = 15,
    ) -> list[PlaceCandidate]:
        """Search local restaurants via curated meal ranges + Overpass/cache."""
        return self.places.search_restaurants(
            interests=interests,
            crowd_preference=crowd_preference,
            max_meal_aed=max_meal_aed,
            area=area,
            local_only=local_only,
            include_live=include_live,
            limit=limit,
        )

    def search_hotels(
        self,
        *,
        tier: HotelPreference | None = None,
        max_nightly_aed: float | None = None,
        crowd_preference: CrowdPreference = "any",
        preferred_areas: list[str] | None = None,
        limit: int = 10,
    ) -> list[AccommodationOption]:
        """Search curated hotel options filtered by tier, budget, and crowd preference."""
        return self.hotels.search_hotels(
            tier=tier,
            max_nightly_aed=max_nightly_aed,
            crowd_preference=crowd_preference,
            preferred_areas=preferred_areas,
            limit=limit,
        )

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
        """Compute transit duration, distance, and RTA/taxi fare."""
        return self.routes.get_route(
            from_place=from_place,
            to_place=to_place,
            from_lat=from_lat,
            from_lon=from_lon,
            to_lat=to_lat,
            to_lon=to_lon,
            mode=mode,
            from_zone=from_zone,
            to_zone=to_zone,
        )

    def calculate_daily_transit_cost(self, segments: list[TransitSegment]) -> float:
        """Calculate daily transit spend with the AED 20 1-day Nol pass cap."""
        return self.routes.calculate_daily_transit_cost(segments)

    def get_weather(
        self,
        travel_dates: TravelDates | None = None,
        *,
        reference_date: date | None = None,
    ) -> WeatherSummary:
        """Fetch Open-Meteo forecast (<=16 days) or seasonal Dubai climate fallback."""
        return self.weather.get_weather(travel_dates, reference_date=reference_date)

    def get_rta_fares(self) -> dict[str, Any]:
        """Return the official RTA fare rules and taxi meter heuristics."""
        return dict(self.dataset.rta_fares)
