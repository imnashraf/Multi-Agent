"""Offline-first Dubai curated dataset, SQLite cache, and free open API clients."""

from dubai_planner.data.cache import CacheEntry, SQLiteCache
from dubai_planner.data.curated import CuratedDataset, haversine_km
from dubai_planner.data.hotels_client import HotelsClient
from dubai_planner.data.places_client import PlacesClient
from dubai_planner.data.provider import DataService
from dubai_planner.data.routes_client import RoutesClient
from dubai_planner.data.weather_client import WeatherClient, WeatherSummary

__all__ = [
    "CacheEntry",
    "CuratedDataset",
    "DataService",
    "HotelsClient",
    "PlacesClient",
    "RoutesClient",
    "SQLiteCache",
    "WeatherClient",
    "WeatherSummary",
    "haversine_km",
]
