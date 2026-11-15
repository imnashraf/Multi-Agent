"""LangChain/LangGraph @tool wrappers over the Dubai DataService and budget math."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from langchain_core.tools import BaseTool, tool

from dubai_planner.data.provider import DataService
from dubai_planner.models.schemas import (
    BudgetAlternative,
    BudgetBreakdown,
    CrowdPreference,
    HotelPreference,
    Provenance,
    TransitMode,
    TravelDates,
)

_DEFAULT_DATA_SERVICE: DataService | None = None


def get_data_service() -> DataService:
    """Return the active DataService instance, lazily initializing from settings."""
    global _DEFAULT_DATA_SERVICE
    if _DEFAULT_DATA_SERVICE is None:
        _DEFAULT_DATA_SERVICE = DataService.from_settings()
    return _DEFAULT_DATA_SERVICE


def set_data_service(service: DataService | None) -> None:
    """Override the module-level DataService (useful for testing or offline runs)."""
    global _DEFAULT_DATA_SERVICE
    _DEFAULT_DATA_SERVICE = service


def _search_attractions_impl(
    service: DataService,
    *,
    interests: list[str] | None = None,
    crowd_preference: str = "any",
    max_cost_aed: float | None = None,
    area: str | None = None,
    include_live: bool = True,
    limit: int = 15,
) -> list[dict[str, Any]]:
    candidates = service.search_attractions(
        interests=interests,
        crowd_preference=cast(CrowdPreference, crowd_preference),
        max_cost_aed=max_cost_aed,
        area=area,
        include_live=include_live,
        limit=limit,
    )
    return [c.model_dump(mode="json") for c in candidates]


def _search_restaurants_impl(
    service: DataService,
    *,
    interests: list[str] | None = None,
    crowd_preference: str = "any",
    max_meal_aed: float | None = None,
    area: str | None = None,
    local_only: bool = False,
    include_live: bool = True,
    limit: int = 12,
) -> list[dict[str, Any]]:
    candidates = service.search_restaurants(
        interests=interests,
        crowd_preference=cast(CrowdPreference, crowd_preference),
        max_meal_aed=max_meal_aed,
        area=area,
        local_only=local_only,
        include_live=include_live,
        limit=limit,
    )
    return [c.model_dump(mode="json") for c in candidates]


def _get_accommodation_options_impl(
    service: DataService,
    *,
    tier: str | None = None,
    max_nightly_aed: float | None = None,
    crowd_preference: str = "any",
    preferred_areas: list[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    options = service.search_hotels(
        tier=cast(HotelPreference | None, tier),
        max_nightly_aed=max_nightly_aed,
        crowd_preference=cast(CrowdPreference, crowd_preference),
        preferred_areas=preferred_areas,
        limit=limit,
    )
    return [o.model_dump(mode="json") for o in options]


def _get_route_and_transit_impl(
    service: DataService,
    *,
    from_place: str,
    to_place: str,
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    mode: str | None = None,
) -> dict[str, Any]:
    segment = service.get_route(
        from_place=from_place,
        to_place=to_place,
        from_lat=from_lat,
        from_lon=from_lon,
        to_lat=to_lat,
        to_lon=to_lon,
        mode=cast(TransitMode | None, mode),
    )
    return segment.model_dump(mode="json")


def _estimate_trip_budget_impl(
    *,
    duration_days: int,
    nightly_rate_aed: float,
    budget_aed: float,
    activity_costs_aed: list[float] | None = None,
    meal_costs_aed: list[float] | None = None,
    transit_costs_aed: list[float] | None = None,
    nights: int | None = None,
    travelers: int = 1,
) -> dict[str, Any]:
    hotel_nights = nights if nights is not None else max(1, duration_days - 1)
    accommodation_aed = round(max(0.0, nightly_rate_aed) * hotel_nights, 2)

    activities_aed = round(sum(max(0.0, c) for c in (activity_costs_aed or [])) * travelers, 2)
    if meal_costs_aed is not None and len(meal_costs_aed) > 0:
        food_aed = round(sum(max(0.0, c) for c in meal_costs_aed) * travelers, 2)
    else:
        food_aed = round(120.0 * duration_days * travelers, 2)

    if transit_costs_aed is not None and len(transit_costs_aed) > 0:
        transportation_aed = round(sum(max(0.0, c) for c in transit_costs_aed) * travelers, 2)
    else:
        transportation_aed = round(20.0 * duration_days * travelers, 2)

    total_aed = round(
        accommodation_aed + food_aed + transportation_aed + activities_aed, 2
    )
    remaining_aed = round(budget_aed - total_aed, 2)
    over_budget = total_aed > budget_aed

    breakdown = BudgetBreakdown(
        accommodation_aed=accommodation_aed,
        food_aed=food_aed,
        transportation_aed=transportation_aed,
        activities_aed=activities_aed,
        total_aed=total_aed,
        remaining_aed=remaining_aed,
        over_budget=over_budget,
        alternatives=[],
        provenance=Provenance(
            source_type="estimated",
            source_name="deterministic_aed_calculator",
        ),
    )
    return breakdown.model_dump(mode="json")


def _find_budget_alternatives_impl(
    service: DataService,
    *,
    budget_aed: float,
    current_total_aed: float,
    duration_days: int = 4,
    current_hotel_nightly_aed: float | None = None,
    crowd_preference: str = "any",
    nights: int | None = None,
) -> list[dict[str, Any]]:
    alternatives: list[BudgetAlternative] = []
    hotel_nights = nights if nights is not None else max(1, duration_days - 1)
    deficit = max(0.0, round(current_total_aed - budget_aed, 2))

    # 1. Cheaper hotel swap
    budget_hotels = service.search_hotels(
        tier="budget",
        crowd_preference=cast(CrowdPreference, crowd_preference),
        limit=3,
    )
    for hotel in budget_hotels:
        if current_hotel_nightly_aed is not None and hotel.nightly_rate_aed >= current_hotel_nightly_aed:
            continue
        baseline = current_hotel_nightly_aed or 500.0
        savings = round((baseline - hotel.nightly_rate_aed) * hotel_nights, 2)
        if savings > 0:
            alternatives.append(
                BudgetAlternative(
                    description=(
                        f"Switch accommodation to {hotel.name} in {hotel.area} "
                        f"(AED {hotel.nightly_rate_aed:.0f}/night)."
                    ),
                    target_category="accommodation",
                    estimated_savings_aed=savings,
                    replacement_item=hotel.id,
                )
            )
            break

    # 2. Free / low-cost cultural & beach swaps
    free_places = service.search_attractions(
        crowd_preference=cast(CrowdPreference, crowd_preference),
        max_cost_aed=15.0,
        include_live=False,
        limit=3,
    )
    if free_places:
        pick = free_places[0]
        alternatives.append(
            BudgetAlternative(
                description=(
                    f"Replace high-ticket landmarks with {pick.name} in {pick.area} "
                    f"(AED {pick.estimated_cost_aed:.0f})."
                ),
                target_category="activities",
                estimated_savings_aed=min(179.0, max(50.0, deficit * 0.4 or 100.0)),
                replacement_item=pick.id,
            )
        )

    # 3. Budget local eateries
    cheap_eats = service.search_restaurants(
        crowd_preference=cast(CrowdPreference, crowd_preference),
        max_meal_aed=45.0,
        local_only=True,
        include_live=False,
        limit=2,
    )
    if cheap_eats:
        eatery = cheap_eats[0]
        alternatives.append(
            BudgetAlternative(
                description=(
                    f"Dine at {eatery.name} (~AED {eatery.estimated_cost_aed:.0f}/meal) "
                    "instead of mid-range tourist restaurants."
                ),
                target_category="food",
                estimated_savings_aed=round(max(40.0, (120.0 - eatery.estimated_cost_aed) * 2), 2),
                replacement_item=eatery.id,
            )
        )

    # 4. RTA 1-Day Nol Pass
    alternatives.append(
        BudgetAlternative(
            description="Use RTA Metro/Bus with the AED 20 1-Day Nol Pass instead of taxis.",
            target_category="transportation",
            estimated_savings_aed=round(25.0 * duration_days, 2),
            replacement_item="rta_one_day_nol_pass",
        )
    )

    return [alt.model_dump(mode="json") for alt in alternatives]


def _get_weather_forecast_impl(
    service: DataService,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    travel_dates: TravelDates | None = None
    if start_date:
        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date) if end_date else parsed_start
        travel_dates = TravelDates(start_date=parsed_start, end_date=parsed_end)
    summary = service.get_weather(travel_dates)
    return summary.model_dump(mode="json")


@tool
def search_attractions(
    interests: list[str] | None = None,
    crowd_preference: str = "any",
    max_cost_aed: float | None = None,
    area: str | None = None,
    include_live: bool = True,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Search Dubai cultural attractions, museums, parks, and beaches with AED pricing and provenance."""
    return _search_attractions_impl(
        get_data_service(),
        interests=interests,
        crowd_preference=crowd_preference,
        max_cost_aed=max_cost_aed,
        area=area,
        include_live=include_live,
        limit=limit,
    )


@tool
def search_restaurants(
    interests: list[str] | None = None,
    crowd_preference: str = "any",
    max_meal_aed: float | None = None,
    area: str | None = None,
    local_only: bool = False,
    include_live: bool = True,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Search authentic local Dubai restaurants and cafes with estimated AED meal prices."""
    return _search_restaurants_impl(
        get_data_service(),
        interests=interests,
        crowd_preference=crowd_preference,
        max_meal_aed=max_meal_aed,
        area=area,
        local_only=local_only,
        include_live=include_live,
        limit=limit,
    )


@tool
def get_accommodation_options(
    tier: str | None = None,
    max_nightly_aed: float | None = None,
    crowd_preference: str = "any",
    preferred_areas: list[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Find curated Dubai hotels and neighborhoods filtered by tier, nightly AED budget, and crowd level."""
    return _get_accommodation_options_impl(
        get_data_service(),
        tier=tier,
        max_nightly_aed=max_nightly_aed,
        crowd_preference=crowd_preference,
        preferred_areas=preferred_areas,
        limit=limit,
    )


@tool
def get_route_and_transit(
    from_place: str,
    to_place: str,
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    mode: str | None = None,
) -> dict[str, Any]:
    """Compute point-to-point Dubai travel duration, distance (km), and official RTA Nol or taxi fare in AED."""
    return _get_route_and_transit_impl(
        get_data_service(),
        from_place=from_place,
        to_place=to_place,
        from_lat=from_lat,
        from_lon=from_lon,
        to_lat=to_lat,
        to_lon=to_lon,
        mode=mode,
    )


@tool
def estimate_trip_budget(
    duration_days: int,
    nightly_rate_aed: float,
    budget_aed: float,
    activity_costs_aed: list[float] | None = None,
    meal_costs_aed: list[float] | None = None,
    transit_costs_aed: list[float] | None = None,
    nights: int | None = None,
    travelers: int = 1,
) -> dict[str, Any]:
    """Calculate category-wise AED trip expenditure and check whether the itinerary is within budget."""
    return _estimate_trip_budget_impl(
        duration_days=duration_days,
        nightly_rate_aed=nightly_rate_aed,
        budget_aed=budget_aed,
        activity_costs_aed=activity_costs_aed,
        meal_costs_aed=meal_costs_aed,
        transit_costs_aed=transit_costs_aed,
        nights=nights,
        travelers=travelers,
    )


@tool
def find_budget_alternatives(
    budget_aed: float,
    current_total_aed: float,
    duration_days: int = 4,
    current_hotel_nightly_aed: float | None = None,
    crowd_preference: str = "any",
    nights: int | None = None,
) -> list[dict[str, Any]]:
    """Suggest concrete AED cost-saving swaps across hotels, attractions, food, and RTA transit."""
    return _find_budget_alternatives_impl(
        get_data_service(),
        budget_aed=budget_aed,
        current_total_aed=current_total_aed,
        duration_days=duration_days,
        current_hotel_nightly_aed=current_hotel_nightly_aed,
        crowd_preference=crowd_preference,
        nights=nights,
    )


@tool
def get_weather_forecast(
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Retrieve Dubai weather forecast (Open-Meteo <=16 days) or seasonal climate guidance."""
    return _get_weather_forecast_impl(
        get_data_service(),
        start_date=start_date,
        end_date=end_date,
    )


ALL_TOOLS: list[BaseTool] = [
    search_attractions,
    search_restaurants,
    get_accommodation_options,
    get_route_and_transit,
    estimate_trip_budget,
    find_budget_alternatives,
    get_weather_forecast,
]


def create_tools(data_service: DataService) -> list[BaseTool]:
    """Bind the default DataService to the provided instance and return all agent tools."""
    set_data_service(data_service)
    return list(ALL_TOOLS)
