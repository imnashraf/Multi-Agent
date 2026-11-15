"""LangGraph shared state definition and parallel branch reducers."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from dubai_planner.data.weather_client import WeatherSummary
from dubai_planner.models.schemas import (
    AccommodationOption,
    BudgetBreakdown,
    DayPlan,
    FinalItinerary,
    PlaceCandidate,
    ReviewResult,
    TravelRequest,
)
from dubai_planner.models.trace import AgentEvent


def _merge_places(
    existing: list[PlaceCandidate], updates: list[PlaceCandidate]
) -> list[PlaceCandidate]:
    """Merge PlaceCandidate lists by id, placing latest updates first without duplicates."""
    if not updates:
        return list(existing)
    merged: list[PlaceCandidate] = []
    seen: set[str] = set()
    for item in updates:
        if item.id not in seen:
            seen.add(item.id)
            merged.append(item)
    for item in existing:
        if item.id not in seen:
            seen.add(item.id)
            merged.append(item)
    return merged


def _merge_accommodations(
    existing: list[AccommodationOption], updates: list[AccommodationOption]
) -> list[AccommodationOption]:
    """Merge AccommodationOption lists by id/name, placing latest updates first."""
    if not updates:
        return list(existing)
    merged: list[AccommodationOption] = []
    seen: set[str] = set()
    for item in updates:
        key = item.id or item.name.lower()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    for item in existing:
        key = item.id or item.name.lower()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


class TravelPlannerState(TypedDict, total=False):
    """Unified LangGraph state shared across all 5 travel-planning agents."""

    user_prompt: str
    travel_request: TravelRequest | None
    weather_summary: WeatherSummary | None
    candidate_places: Annotated[list[PlaceCandidate], _merge_places]
    candidate_restaurants: Annotated[list[PlaceCandidate], _merge_places]
    accommodation_options: Annotated[list[AccommodationOption], _merge_accommodations]
    selected_accommodation: AccommodationOption | None
    day_plans: list[DayPlan]
    budget_breakdown: BudgetBreakdown | None
    review_result: ReviewResult | None
    review_history: Annotated[list[ReviewResult], operator.add]
    revision_count: int
    max_revisions: int
    revision_instructions: list[str]
    revision_strategy: dict[str, Any]
    draft_snapshots: Annotated[list[dict[str, Any]], operator.add]
    execution_trace: Annotated[list[AgentEvent], operator.add]
    final_itinerary: FinalItinerary | None
