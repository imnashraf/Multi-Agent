"""Phase 2 tests: curated dataset, offline fallback, SQLite caching, and provenance tags."""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from dubai_planner.config import load_settings
from dubai_planner.data import DataService, SQLiteCache
from dubai_planner.models.schemas import TravelDates


def _curated_path() -> Path:
    settings = load_settings()
    assert settings.curated_data_path is not None
    return settings.curated_data_path


def _failing_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Simulated network outage", request=request)

    return httpx.MockTransport(handler)


def test_offline_mode_all_lookups_succeed_with_accurate_provenance(tmp_path: Path) -> None:
    service = DataService(
        curated_data_path=_curated_path(),
        cache_db_path=tmp_path / "cache.db",
        offline_mode=True,
        http_client=httpx.Client(transport=_failing_transport()),
    )

    neighborhoods = service.get_neighborhoods(crowd_preference="low")
    assert len(neighborhoods) >= 5
    assert all(n.crowd_level != "high" for n in neighborhoods)
    assert all(n.provenance.source_type == "verified_dataset" for n in neighborhoods)

    attractions = service.search_attractions(
        interests=["cultural", "beaches"],
        crowd_preference="low",
    )
    assert any(a.category == "attraction" for a in attractions)
    assert any(a.category == "beach" for a in attractions)
    assert all(a.crowd_level != "high" for a in attractions)
    assert all(a.provenance.source_type == "verified_dataset" for a in attractions)

    restaurants = service.search_restaurants(
        interests=["local_food"],
        crowd_preference="low",
        local_only=True,
    )
    assert len(restaurants) >= 4
    assert all(r.provenance.source_type == "estimated" for r in restaurants)
    assert all(r.provenance.source_name == "dubai_curated.json" for r in restaurants)

    hotels = service.search_hotels(tier="budget", crowd_preference="low")
    assert len(hotels) >= 2
    assert hotels[0].area == "Al Fahidi Historical Neighbourhood"
    assert all(h.provenance.source_type == "verified_dataset" for h in hotels)

    # RTA Nol zone fare checks (Zone 5 -> Zone 5 = 3.0, Zone 5 -> Zone 6 = 5.0, Zone 5 -> Zone 2 = 7.5)
    same_zone_seg = service.get_route(
        from_place="Al Fahidi",
        to_place="Deira Spice Souk",
        from_lat=25.2634,
        from_lon=55.2974,
        to_lat=25.2712,
        to_lon=55.2981,
        mode="metro",
    )
    assert same_zone_seg.estimated_fare_aed == 3.0
    assert same_zone_seg.provenance.source_type == "estimated"

    two_zone_seg = service.get_route(
        from_place="Al Fahidi",
        to_place="Downtown Dubai",
        from_lat=25.2634,
        from_lon=55.2974,
        to_lat=25.1972,
        to_lon=55.2744,
        mode="metro",
    )
    assert two_zone_seg.estimated_fare_aed == 5.0

    three_zone_seg = service.get_route(
        from_place="Al Fahidi",
        to_place="Dubai Marina",
        from_lat=25.2634,
        from_lon=55.2974,
        to_lat=25.0778,
        to_lon=55.1398,
        mode="metro",
    )
    assert three_zone_seg.estimated_fare_aed == 7.5

    # 1-day Nol pass cap at AED 20
    daily_cap = service.calculate_daily_transit_cost(
        [three_zone_seg, three_zone_seg, three_zone_seg]
    )
    assert daily_cap == 20.0

    # Abra fixed creek fare
    abra_seg = service.get_route(
        from_place="Bur Dubai Abra Station",
        to_place="Deira Old Souk Abra Station",
        from_lat=25.2654,
        from_lon=55.2977,
        to_lat=25.2693,
        to_lon=55.2972,
        mode="abra",
    )
    assert abra_seg.estimated_fare_aed == 1.0
    assert abra_seg.provenance.source_type == "verified_dataset"

    # Taxi heuristic minimum AED 12
    taxi_seg = service.get_route(
        from_place="Al Fahidi",
        to_place="Al Shindagha",
        from_lat=25.2634,
        from_lon=55.2974,
        to_lat=25.2673,
        to_lon=55.2916,
        mode="taxi",
    )
    assert taxi_seg.estimated_fare_aed >= 12.0
    assert taxi_seg.provenance.source_type == "estimated"

    # Seasonal weather fallback (>16 days ahead)
    summer = service.get_weather(
        TravelDates(start_date=date(2027, 7, 10), end_date=date(2027, 7, 13)),
        reference_date=date(2026, 11, 15),
    )
    assert summer.is_hot_season is True
    assert summer.season_band == "hot"
    assert summer.provenance.source_type == "verified_dataset"

    winter = service.get_weather(
        TravelDates(start_date=date(2027, 1, 15), end_date=date(2027, 1, 18)),
        reference_date=date(2026, 11, 15),
    )
    assert winter.is_hot_season is False
    assert winter.season_band == "mild"


def test_network_failure_falls_back_gracefully_even_when_online_enabled(tmp_path: Path) -> None:
    service = DataService(
        curated_data_path=_curated_path(),
        cache_db_path=tmp_path / "cache.db",
        offline_mode=False,
        http_client=httpx.Client(transport=_failing_transport()),
    )

    attractions = service.search_attractions(interests=["cultural"])
    assert len(attractions) >= 5
    assert all(a.provenance.source_type == "verified_dataset" for a in attractions)

    route = service.get_route(
        from_place="Al Fahidi",
        to_place="Al Seef",
        from_lat=25.2634,
        from_lon=55.2974,
        to_lat=25.2597,
        to_lon=55.3059,
    )
    assert route.duration_mins > 0
    assert route.provenance.source_type == "estimated"

    today = date(2026, 11, 15)
    weather = service.get_weather(
        TravelDates(start_date=today + timedelta(days=2), end_date=today + timedelta(days=5)),
        reference_date=today,
    )
    assert weather.provenance.source_type == "verified_dataset"


def test_live_api_calls_populate_sqlite_cache_and_tag_api_derived(tmp_path: Path) -> None:
    calls = {"overpass": 0, "osrm": 0, "open_meteo": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "overpass-api.de" in url:
            calls["overpass"] += 1
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 999001,
                            "lat": 25.2649,
                            "lon": 55.2991,
                            "tags": {
                                "name:en": "Al Fahidi Pottery Courtyard",
                                "tourism": "attraction",
                            },
                        }
                    ]
                },
            )
        if "router.project-osrm.org" in url:
            calls["osrm"] += 1
            return httpx.Response(
                200,
                json={"routes": [{"distance": 6400.0, "duration": 540.0}]},
            )
        if "api.open-meteo.com" in url:
            calls["open_meteo"] += 1
            return httpx.Response(
                200,
                json={
                    "daily": {
                        "temperature_2m_max": [27.0, 28.0, 26.5],
                        "temperature_2m_min": [18.0, 19.0, 18.5],
                        "precipitation_sum": [0.0, 0.0, 0.0],
                    }
                },
            )
        return httpx.Response(404)

    cache_path = tmp_path / "cache.db"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = DataService(
        curated_data_path=_curated_path(),
        cache_db_path=cache_path,
        offline_mode=False,
        http_client=client,
    )

    # 1. Overpass call + cache hit
    attractions = service.search_attractions(interests=["cultural"], include_live=True, limit=30)
    live_pois = [a for a in attractions if a.provenance.source_type == "api_derived"]
    assert len(live_pois) == 1
    assert live_pois[0].name == "Al Fahidi Pottery Courtyard"
    assert live_pois[0].provenance.source_name == "openstreetmap_overpass"
    assert calls["overpass"] == 1

    service.search_attractions(interests=["cultural"], include_live=True, limit=30)
    assert calls["overpass"] == 1  # served from SQLite cache

    # 2. OSRM call + cache hit
    seg1 = service.get_route(
        from_place="Al Fahidi",
        to_place="Downtown Dubai",
        from_lat=25.2634,
        from_lon=55.2974,
        to_lat=25.1972,
        to_lon=55.2744,
        mode="metro",
    )
    assert seg1.distance_km == pytest.approx(6.4)
    assert seg1.estimated_fare_aed == 5.0  # Zone 5 -> Zone 6 RTA Nol fare
    assert seg1.provenance.source_type == "api_derived"
    assert seg1.provenance.source_name == "osrm"
    assert calls["osrm"] == 1

    seg2 = service.get_route(
        from_place="Al Fahidi",
        to_place="Downtown Dubai",
        from_lat=25.2634,
        from_lon=55.2974,
        to_lat=25.1972,
        to_lon=55.2744,
        mode="metro",
    )
    assert seg2.distance_km == seg1.distance_km
    assert calls["osrm"] == 1  # served from SQLite cache

    # 3. Open-Meteo call + cache hit
    today = date(2026, 11, 15)
    dates = TravelDates(start_date=today + timedelta(days=3), end_date=today + timedelta(days=5))
    w1 = service.get_weather(dates, reference_date=today)
    assert w1.provenance.source_type == "api_derived"
    assert w1.provenance.source_name == "open_meteo"
    assert w1.forecast_days_covered == 3
    assert w1.season_band == "mild"
    assert calls["open_meteo"] == 1

    w2 = service.get_weather(dates, reference_date=today)
    assert w2.temp_max_c == w1.temp_max_c
    assert calls["open_meteo"] == 1  # served from SQLite cache


def test_sqlite_cache_ttl_expiration(tmp_path: Path) -> None:
    cache = SQLiteCache(tmp_path / "ttl_test.db")
    cache.set("k1", namespace="test", payload={"ok": True}, source_name="unit", ttl_seconds=1)
    assert cache.get("k1") is not None
    time.sleep(1.1)
    assert cache.get("k1") is None
