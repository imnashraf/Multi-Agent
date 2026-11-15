"""Loader and lookup helpers for the curated Dubai JSON dataset."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance in kilometers between two WGS84 points."""
    earth_radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return earth_radius_km * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


class CuratedDataset:
    """In-memory view of data/dubai_curated.json with neighborhood lookups."""

    def __init__(self, json_path: str | Path) -> None:
        self.json_path = Path(json_path)
        if not self.json_path.exists():
            raise FileNotFoundError(f"Curated Dubai dataset not found at {self.json_path}")
        with self.json_path.open("r", encoding="utf-8") as fh:
            self.raw: dict[str, Any] = json.load(fh)

        self.metadata: dict[str, Any] = self.raw.get("metadata", {})
        self.neighborhoods: list[dict[str, Any]] = self.raw.get("neighborhoods", [])
        self.neighborhood_by_id: dict[str, dict[str, Any]] = {
            n["id"]: n for n in self.neighborhoods
        }
        self.attractions: list[dict[str, Any]] = self.raw.get("attractions", [])
        self.restaurants: list[dict[str, Any]] = self.raw.get("restaurants", [])
        self.hotels: list[dict[str, Any]] = self.raw.get("hotels", [])
        self.rta_fares: dict[str, Any] = self.raw.get("rta_fares", {})
        self.seasonal_weather: dict[str, dict[str, Any]] = self.raw.get("seasonal_weather", {})

    @property
    def last_verified(self) -> str | None:
        return self.metadata.get("last_verified")

    def neighborhood_name(self, neighborhood_id: str) -> str:
        """Resolve a neighborhood ID to its display name."""
        record = self.neighborhood_by_id.get(neighborhood_id)
        return record["name"] if record else neighborhood_id

    def neighborhood_zone(self, neighborhood_id: str) -> int | None:
        """Resolve a neighborhood ID to its RTA fare zone number."""
        record = self.neighborhood_by_id.get(neighborhood_id)
        return record.get("rta_zone") if record else None

    def nearest_neighborhood(self, lat: float, lon: float) -> dict[str, Any] | None:
        """Find the closest curated neighborhood to given coordinates."""
        if not self.neighborhoods:
            return None
        return min(
            self.neighborhoods,
            key=lambda n: haversine_km(lat, lon, float(n["lat"]), float(n["lon"])),
        )
