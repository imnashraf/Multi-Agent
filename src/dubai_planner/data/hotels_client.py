"""Curated Dubai hotel and neighborhood accommodation lookup client."""

from __future__ import annotations

from dubai_planner.data.curated import CuratedDataset
from dubai_planner.models.schemas import (
    AccommodationOption,
    CrowdPreference,
    HotelPreference,
    Provenance,
)


class HotelsClient:
    """Queries curated Dubai hotels filtered by tier, nightly budget, and crowd preference."""

    def __init__(self, dataset: CuratedDataset) -> None:
        self.dataset = dataset

    def search_hotels(
        self,
        *,
        tier: HotelPreference | None = None,
        max_nightly_aed: float | None = None,
        crowd_preference: CrowdPreference = "any",
        preferred_areas: list[str] | None = None,
        limit: int = 10,
    ) -> list[AccommodationOption]:
        """Return matching AccommodationOption records sorted by relevance and value."""
        area_tokens = {a.strip().lower() for a in (preferred_areas or []) if a.strip()}
        scored: list[tuple[int, float, AccommodationOption]] = []

        for item in self.dataset.hotels:
            crowd_level = item.get("crowd_level", "medium")
            if crowd_preference == "low" and crowd_level == "high":
                continue

            item_tier: HotelPreference = item["tier"]
            if tier is not None and item_tier != tier:
                # Allow budget stays when mid_range is requested if affordable, skip luxury mismatch.
                if not (tier == "mid_range" and item_tier == "budget"):
                    continue

            nightly_min = float(item["nightly_aed_min"])
            nightly_max = float(item["nightly_aed_max"])
            representative_rate = round((nightly_min + nightly_max) / 2.0, 2)

            if max_nightly_aed is not None:
                if nightly_min > max_nightly_aed:
                    continue
                representative_rate = round(min(representative_rate, max_nightly_aed), 2)

            area_name = self.dataset.neighborhood_name(item["neighborhood_id"])
            option = AccommodationOption(
                id=item["id"],
                name=item["name"],
                area=area_name,
                tier=item_tier,
                nightly_rate_aed=representative_rate,
                metro_access=item["metro_access"],
                metro_walk_mins=item.get("metro_walk_mins"),
                crowd_level=crowd_level,
                lat=float(item["lat"]) if item.get("lat") is not None else None,
                lon=float(item["lon"]) if item.get("lon") is not None else None,
                provenance=Provenance(
                    source_type="verified_dataset",
                    source_name="dubai_curated.json",
                    last_verified=self.dataset.last_verified,
                ),
            )

            score = 0
            if tier is not None and item_tier == tier:
                score -= 20
            if crowd_preference == "low" and crowd_level == "low":
                score -= 10
            if area_tokens and any(
                tok in item["neighborhood_id"].lower() or tok in area_name.lower()
                for tok in area_tokens
            ):
                score -= 8
            scored.append((score, representative_rate, option))

        scored.sort(key=lambda entry: (entry[0], entry[1], entry[2].name))
        return [option for _, _, option in scored[:limit]]
