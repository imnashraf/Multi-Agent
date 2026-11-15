"""Places & restaurant data client (Overpass API + SQLite cache + curated fallback)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from dubai_planner.data.cache import SQLiteCache
from dubai_planner.data.curated import CuratedDataset
from dubai_planner.models.schemas import (
    CrowdPreference,
    PlaceCandidate,
    PlaceCategory,
    Provenance,
)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DUBAI_BBOX = (24.79, 54.89, 25.36, 55.56)  # south, west, north, east

_INTEREST_EXPANSIONS: dict[str, set[str]] = {
    "cultural": {"cultural", "heritage", "museum", "art", "architecture", "history", "markets"},
    "cultural_attractions": {"cultural", "heritage", "museum", "art", "architecture", "history", "markets"},
    "culture": {"cultural", "heritage", "museum", "art", "architecture", "history"},
    "local_food": {"local_food", "emirati", "street_food", "heritage_eatery", "seafood"},
    "food": {"local_food", "emirati", "street_food", "seafood", "cafe"},
    "beaches": {"beach", "waterfront", "sunset", "swimming"},
    "beach": {"beach", "waterfront", "sunset", "swimming"},
    "art": {"art", "galleries", "museum", "indie_cafes"},
    "nature": {"nature", "park", "wildlife", "green_space"},
    "landmarks": {"landmarks", "skyline", "observation", "panoramic"},
}


def _expand_interests(interests: list[str] | None) -> set[str]:
    expanded: set[str] = set()
    for raw in interests or []:
        norm = raw.strip().lower().replace(" ", "_")
        if not norm:
            continue
        expanded.add(norm)
        expanded.update(_INTEREST_EXPANSIONS.get(norm, set()))
    return expanded


def _area_matches(record_neighborhood_id: str, area_name: str, query_area: str | None) -> bool:
    if not query_area:
        return True
    target = query_area.strip().lower()
    return target in record_neighborhood_id.lower() or target in area_name.lower()


def _crowd_allowed(crowd_level: str, crowd_preference: CrowdPreference) -> bool:
    if crowd_preference == "low":
        return crowd_level != "high"
    return True


def _crowd_rank(crowd_level: str, crowd_preference: CrowdPreference) -> int:
    if crowd_preference == "low":
        return {"low": 0, "medium": 1, "high": 2}.get(crowd_level, 1)
    if crowd_preference == "medium":
        return {"medium": 0, "low": 1, "high": 2}.get(crowd_level, 1)
    return 0


class PlacesClient:
    """Fetches Dubai attractions, beaches, and restaurants with offline-first fallback."""

    def __init__(
        self,
        dataset: CuratedDataset,
        cache: SQLiteCache | None = None,
        *,
        offline_mode: bool = False,
        http_client: httpx.Client | None = None,
        overpass_url: str = OVERPASS_URL,
        cache_ttl_seconds: int = 86400,
    ) -> None:
        self.dataset = dataset
        self.cache = cache
        self.offline_mode = offline_mode
        self._http_client = http_client
        self.overpass_url = overpass_url
        self.cache_ttl_seconds = cache_ttl_seconds

    def _query_overpass(self, kind: str) -> tuple[list[dict[str, Any]], datetime]:
        cache_key = f"overpass:{kind}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return list(cached.payload), cached.retrieved_at

        if self.offline_mode:
            return [], datetime.now(timezone.utc)

        s, w, n, e = DUBAI_BBOX
        if kind == "attractions":
            ql = (
                f"[out:json][timeout:20];("
                f'nwr["tourism"~"attraction|museum"]({s},{w},{n},{e});'
                f'nwr["natural"="beach"]({s},{w},{n},{e});'
                f");out center 40;"
            )
        else:
            ql = (
                f"[out:json][timeout:20];("
                f'nwr["amenity"~"restaurant|cafe"]({s},{w},{n},{e});'
                f");out center 40;"
            )

        try:
            if self._http_client is not None:
                resp = self._http_client.post(self.overpass_url, data={"data": ql}, timeout=15.0)
            else:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(self.overpass_url, data={"data": ql})
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
        except (httpx.HTTPError, ValueError):
            return [], datetime.now(timezone.utc)

        retrieved_at = datetime.now(timezone.utc)
        if self.cache is not None and elements:
            retrieved_at = self.cache.set(
                cache_key,
                namespace="overpass",
                payload=elements,
                source_name="openstreetmap_overpass",
                ttl_seconds=self.cache_ttl_seconds,
            )
        return elements, retrieved_at

    def _map_overpass_element(
        self,
        el: dict[str, Any],
        *,
        kind: str,
        retrieved_at: datetime,
    ) -> PlaceCandidate | None:
        tags = el.get("tags") or {}
        name = tags.get("name:en") or tags.get("name")
        if not name:
            return None

        center = el.get("center") or {}
        lat = el.get("lat") if el.get("lat") is not None else center.get("lat")
        lon = el.get("lon") if el.get("lon") is not None else center.get("lon")
        if lat is None or lon is None:
            return None

        lat_f, lon_f = float(lat), float(lon)
        nearest = self.dataset.nearest_neighborhood(lat_f, lon_f)
        area_name = nearest["name"] if nearest else "Dubai"

        if kind == "attractions":
            is_beach = tags.get("natural") == "beach"
            category: PlaceCategory = "beach" if is_beach else "attraction"
            poi_tags = [t for t in [tags.get("tourism"), tags.get("natural"), tags.get("historic")] if t]
            if is_beach:
                poi_tags.append("beach")
            est_cost = 0.0 if is_beach else 25.0
            duration_mins = 120 if is_beach else 90
        else:
            category = "restaurant"
            cuisine = tags.get("cuisine", "")
            poi_tags = ["local_food"] + [c.strip().lower() for c in cuisine.split(";") if c.strip()]
            est_cost = 65.0
            duration_mins = 60

        return PlaceCandidate(
            id=f"osm_{el.get('type', 'node')}_{el.get('id', abs(hash(name)))}",
            name=name,
            category=category,
            area=area_name,
            lat=lat_f,
            lon=lon_f,
            crowd_level="medium",
            estimated_cost_aed=est_cost,
            duration_mins=duration_mins,
            description=tags.get("description"),
            tags=poi_tags,
            opening_hours=tags.get("opening_hours"),
            provenance=Provenance(
                source_type="api_derived",
                source_name="openstreetmap_overpass",
                retrieved_at=retrieved_at,
            ),
        )

    def _curated_attraction_to_candidate(self, item: dict[str, Any]) -> PlaceCandidate:
        area_name = self.dataset.neighborhood_name(item["neighborhood_id"])
        category: PlaceCategory = "beach" if item.get("category") == "beach" else "attraction"
        return PlaceCandidate(
            id=item["id"],
            name=item["name"],
            category=category,
            area=area_name,
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            crowd_level=item.get("crowd_level", "medium"),
            estimated_cost_aed=float(item.get("ticket_aed", 0.0)),
            duration_mins=int(item.get("avg_duration_mins", 90)),
            description=item.get("description"),
            tags=list(item.get("tags", [])),
            indoor_outdoor=item.get("indoor_outdoor"),
            opening_hours=item.get("opening_hours"),
            provenance=Provenance(
                source_type="verified_dataset",
                source_name="dubai_curated.json",
                last_verified=item.get("last_verified") or self.dataset.last_verified,
                source_url=item.get("source_url"),
            ),
        )

    def _curated_restaurant_to_candidate(self, item: dict[str, Any]) -> PlaceCandidate:
        area_name = self.dataset.neighborhood_name(item["neighborhood_id"])
        tags = list(item.get("tags", []))
        if item.get("cuisine"):
            tags.append(str(item["cuisine"]).lower().replace(" ", "_"))
        return PlaceCandidate(
            id=item["id"],
            name=item["name"],
            category="restaurant",
            area=area_name,
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            crowd_level=item.get("crowd_level", "medium"),
            estimated_cost_aed=float(item.get("avg_meal_aed", 60.0)),
            duration_mins=60,
            description=item.get("description"),
            tags=tags,
            indoor_outdoor="mixed",
            opening_hours=item.get("opening_hours"),
            provenance=Provenance(
                source_type="estimated",
                source_name="dubai_curated.json",
                last_verified=self.dataset.last_verified,
            ),
        )

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
        """Return matching curated attractions/beaches plus optional Overpass discoveries."""
        expanded = _expand_interests(interests)
        candidates: list[tuple[int, PlaceCandidate]] = []

        for item in self.dataset.attractions:
            area_name = self.dataset.neighborhood_name(item["neighborhood_id"])
            if not _area_matches(item["neighborhood_id"], area_name, area):
                continue
            candidate = self._curated_attraction_to_candidate(item)
            if not _crowd_allowed(candidate.crowd_level, crowd_preference):
                continue
            if max_cost_aed is not None and candidate.estimated_cost_aed > max_cost_aed:
                continue

            item_tokens = {t.lower() for t in candidate.tags} | {candidate.category}
            overlap = len(expanded & item_tokens) if expanded else 1
            if expanded and overlap == 0:
                continue
            score = -overlap * 10 + _crowd_rank(candidate.crowd_level, crowd_preference)
            candidates.append((score, candidate))

        seen_names = {c.name.lower() for _, c in candidates}

        if include_live:
            elements, retrieved_at = self._query_overpass("attractions")
            for el in elements:
                live_candidate = self._map_overpass_element(
                    el, kind="attractions", retrieved_at=retrieved_at
                )
                if live_candidate is None or live_candidate.name.lower() in seen_names:
                    continue
                if area and area.strip().lower() not in live_candidate.area.lower():
                    continue
                if not _crowd_allowed(live_candidate.crowd_level, crowd_preference):
                    continue
                if max_cost_aed is not None and live_candidate.estimated_cost_aed > max_cost_aed:
                    continue
                seen_names.add(live_candidate.name.lower())
                candidates.append((50, live_candidate))

        candidates.sort(key=lambda pair: (pair[0], pair[1].estimated_cost_aed, pair[1].name))
        return [c for _, c in candidates[:limit]]

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
        """Return matching local restaurants with curated AED meal estimates."""
        expanded = _expand_interests(interests)
        candidates: list[tuple[int, PlaceCandidate]] = []

        for item in self.dataset.restaurants:
            if local_only and not item.get("is_local_specialty", False):
                continue
            area_name = self.dataset.neighborhood_name(item["neighborhood_id"])
            if not _area_matches(item["neighborhood_id"], area_name, area):
                continue
            candidate = self._curated_restaurant_to_candidate(item)
            if not _crowd_allowed(candidate.crowd_level, crowd_preference):
                continue
            if max_meal_aed is not None and candidate.estimated_cost_aed > max_meal_aed:
                continue

            item_tokens = {t.lower() for t in candidate.tags}
            overlap = len(expanded & item_tokens) if expanded else 0
            local_bonus = 5 if item.get("is_local_specialty") else 0
            score = -(overlap * 10 + local_bonus) + _crowd_rank(
                candidate.crowd_level, crowd_preference
            )
            candidates.append((score, candidate))

        seen_names = {c.name.lower() for _, c in candidates}

        if include_live:
            elements, retrieved_at = self._query_overpass("restaurants")
            for el in elements:
                live_candidate = self._map_overpass_element(
                    el, kind="restaurants", retrieved_at=retrieved_at
                )
                if live_candidate is None or live_candidate.name.lower() in seen_names:
                    continue
                if area and area.strip().lower() not in live_candidate.area.lower():
                    continue
                if max_meal_aed is not None and live_candidate.estimated_cost_aed > max_meal_aed:
                    continue
                seen_names.add(live_candidate.name.lower())
                candidates.append((50, live_candidate))

        candidates.sort(key=lambda pair: (pair[0], pair[1].estimated_cost_aed, pair[1].name))
        return [c for _, c in candidates[:limit]]
