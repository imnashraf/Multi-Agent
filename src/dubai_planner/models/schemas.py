"""Shared Pydantic v2 domain schemas for the Dubai AI Travel Planner."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from dubai_planner.models.trace import ExecutionTrace

SourceType = Literal["verified_dataset", "api_derived", "estimated"]
HotelPreference = Literal["budget", "mid_range", "luxury"]
CrowdPreference = Literal["low", "medium", "any"]
CrowdLevel = Literal["low", "medium", "high"]
PlaceCategory = Literal["attraction", "restaurant", "beach", "neighborhood"]
IndoorOutdoor = Literal["indoor", "outdoor", "mixed"]
TransitMode = Literal["metro", "bus", "tram", "taxi", "walk", "abra"]
BudgetCategory = Literal["accommodation", "food", "transportation", "activities"]
ViolationType = Literal[
    "budget_exceeded",
    "schedule_conflict",
    "preference_mismatch",
    "duplicate_activity",
    "missing_info",
]


class Provenance(BaseModel):
    """Tracks origin and verification status of every fact, route, or cost."""

    source_type: SourceType
    source_name: str = Field(min_length=1)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_verified: str | None = None
    source_url: str | None = None


class TravelDates(BaseModel):
    """Optional travel date window used for Open-Meteo or seasonal weather lookup."""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_date_order(self) -> TravelDates:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class TravelRequest(BaseModel):
    """Structured travel constraints parsed from a natural-language user prompt."""

    destination: str = Field(default="Dubai", min_length=1)
    duration_days: int = Field(ge=1, le=7)
    budget_aed: float = Field(gt=0.0)
    excludes_flights: bool = True
    interests: list[str] = Field(default_factory=list)
    hotel_preference: HotelPreference = "budget"
    crowd_preference: CrowdPreference = "any"
    travel_dates: TravelDates | None = None
    travelers: int = Field(default=1, ge=1)
    raw_prompt: str | None = None

    @field_validator("hotel_preference", mode="before")
    @classmethod
    def normalize_hotel_preference(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "affordable": "budget",
            "cheap": "budget",
            "economy": "budget",
            "midrange": "mid_range",
            "moderate": "mid_range",
            "standard": "mid_range",
            "upscale": "luxury",
            "high_end": "luxury",
        }
        return aliases.get(normalized, normalized)

    @field_validator("crowd_preference", mode="before")
    @classmethod
    def normalize_crowd_preference(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "quiet": "low",
            "avoid_crowds": "low",
            "avoid_crowded": "low",
            "less_crowded": "low",
            "moderate": "medium",
            "no_preference": "any",
            "none": "any",
        }
        return aliases.get(normalized, normalized)

    @field_validator("interests", mode="before")
    @classmethod
    def normalize_interests(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            aliases = {
                "cultural_attractions": "cultural",
                "cultural_sites": "cultural",
                "cultural_experiences": "cultural",
                "culture": "cultural",
                "local_cuisine": "local_food",
                "local_dining": "local_food",
                "emirati_food": "local_food",
            }
            seen: set[str] = set()
            result: list[str] = []
            for item in value:
                cleaned = str(item).strip().lower().replace(" ", "_")
                cleaned = aliases.get(cleaned, cleaned)
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    result.append(cleaned)
            return result
        return value

    @field_validator("travel_dates", mode="before")
    @classmethod
    def coerce_travel_dates(cls, value: Any) -> Any:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return {"start_date": value[0], "end_date": value[1]}
        return value


class PlaceCandidate(BaseModel):
    """Candidate attraction, restaurant, beach, or neighborhood in Dubai."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: PlaceCategory
    area: str = Field(min_length=1)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    crowd_level: CrowdLevel = "medium"
    estimated_cost_aed: float = Field(ge=0.0)
    duration_mins: int = Field(gt=0)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    indoor_outdoor: IndoorOutdoor | None = None
    opening_hours: str | None = None
    provenance: Provenance


class AccommodationOption(BaseModel):
    """Curated hotel and neighborhood stay option."""

    id: str | None = None
    name: str = Field(min_length=1)
    area: str = Field(min_length=1)
    tier: HotelPreference
    nightly_rate_aed: float = Field(ge=0.0)
    metro_access: str = Field(min_length=1)
    metro_walk_mins: int | None = Field(default=None, ge=0)
    crowd_level: CrowdLevel = "medium"
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    provenance: Provenance


class TransitSegment(BaseModel):
    """Point-to-point transit leg with duration, distance, and RTA/taxi fare."""

    from_place: str = Field(min_length=1)
    to_place: str = Field(min_length=1)
    mode: TransitMode
    duration_mins: int = Field(ge=0)
    distance_km: float = Field(ge=0.0)
    estimated_fare_aed: float = Field(ge=0.0)
    provenance: Provenance


class ScheduledActivity(BaseModel):
    """A time-bounded stop within a day's itinerary and transit to the next stop."""

    start_time: time
    end_time: time
    place: PlaceCandidate
    transit_to_next: TransitSegment | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_time_window(self) -> ScheduledActivity:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be strictly after start_time")
        return self


class DayPlan(BaseModel):
    """Single day schedule clustered by area with weather guidance."""

    day_number: int = Field(ge=1, le=7)
    theme: str = Field(min_length=1)
    area_focus: str = Field(min_length=1)
    activities: list[ScheduledActivity] = Field(default_factory=list)
    weather_note: str | None = None


class BudgetAlternative(BaseModel):
    """Concrete cost-saving swap proposed when an itinerary exceeds budget."""

    description: str = Field(min_length=1)
    target_category: BudgetCategory
    estimated_savings_aed: float = Field(ge=0.0)
    replacement_item: str | None = None


class BudgetBreakdown(BaseModel):
    """Category-wise AED expenditure breakdown and budget compliance status."""

    accommodation_aed: float = Field(ge=0.0)
    food_aed: float = Field(ge=0.0)
    transportation_aed: float = Field(ge=0.0)
    activities_aed: float = Field(ge=0.0)
    total_aed: float = Field(ge=0.0)
    remaining_aed: float
    over_budget: bool
    alternatives: list[BudgetAlternative] = Field(default_factory=list)
    provenance: Provenance | None = None


class ValidationViolation(BaseModel):
    """Typed issue flagged by deterministic or LLM review checks."""

    type: ViolationType
    severity: Literal["error", "warning"] = "error"
    message: str = Field(min_length=1)
    day_number: int | None = Field(default=None, ge=1, le=7)
    item_id: str | None = None


class ReviewResult(BaseModel):
    """Output of the Review Agent governing pass/revise conditional routing."""

    passed: bool
    violations: list[ValidationViolation] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)
    summary: str | None = None
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("revision_instructions", mode="before")
    @classmethod
    def coerce_revision_instructions(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return value


class FinalItinerary(BaseModel):
    """Complete validated Dubai itinerary output rendered by the UI."""

    request: TravelRequest
    accommodation: AccommodationOption
    days: list[DayPlan] = Field(min_length=1)
    budget: BudgetBreakdown
    review_history: list[ReviewResult] = Field(default_factory=list)
    execution_trace: ExecutionTrace = Field(default_factory=ExecutionTrace)
