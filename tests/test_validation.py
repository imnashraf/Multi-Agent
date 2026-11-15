"""Phase 3 tests: deterministic itinerary/budget validators and LangGraph @tool wrappers."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from dubai_planner.config import load_settings
from dubai_planner.data import DataService
from dubai_planner.models.schemas import (
    AccommodationOption,
    BudgetAlternative,
    BudgetBreakdown,
    DayPlan,
    PlaceCandidate,
    Provenance,
    ScheduledActivity,
    TransitSegment,
    TravelRequest,
)
from dubai_planner.tools import (
    ALL_TOOLS,
    estimate_trip_budget,
    find_budget_alternatives,
    get_accommodation_options,
    get_route_and_transit,
    get_weather_forecast,
    search_attractions,
    search_restaurants,
    set_data_service,
)
from dubai_planner.validation import ItineraryValidator


def _prov(source_type: str = "verified_dataset", source_name: str = "dubai_curated.json") -> Provenance:
    return Provenance(source_type=source_type, source_name=source_name)  # type: ignore[arg-type]


def _make_place(
    pid: str,
    name: str,
    *,
    category: str = "attraction",
    crowd_level: str = "low",
    cost: float = 0.0,
    tags: list[str] | None = None,
) -> PlaceCandidate:
    return PlaceCandidate(
        id=pid,
        name=name,
        category=category,  # type: ignore[arg-type]
        area="Al Fahidi",
        lat=25.2634,
        lon=55.2974,
        crowd_level=crowd_level,  # type: ignore[arg-type]
        estimated_cost_aed=cost,
        duration_mins=90,
        tags=tags or ["cultural"],
        provenance=_prov(),
    )


def _make_transit(from_place: str, to_place: str, duration_mins: int = 15) -> TransitSegment:
    return TransitSegment(
        from_place=from_place,
        to_place=to_place,
        mode="walk",
        duration_mins=duration_mins,
        distance_km=0.8,
        estimated_fare_aed=0.0,
        provenance=_prov("estimated", "haversine_rta_fallback"),
    )


def _valid_fixture() -> tuple[TravelRequest, AccommodationOption, list[DayPlan], BudgetBreakdown]:
    request = TravelRequest(
        duration_days=2,
        budget_aed=2000.0,
        interests=["cultural", "local_food", "beach"],
        hotel_preference="budget",
        crowd_preference="low",
    )
    hotel = AccommodationOption(
        id="xva_art_hotel",
        name="XVA Art Hotel",
        area="Al Fahidi Historical Neighbourhood",
        tier="budget",
        nightly_rate_aed=350.0,
        metro_access="Sharaf DG Metro (6 min walk)",
        crowd_level="low",
        provenance=_prov(),
    )
    museum = _make_place("shindagha_museum", "Al Shindagha Museum", cost=50.0, tags=["cultural", "museum"])
    tea_house = _make_place(
        "arabian_tea_house",
        "Arabian Tea House",
        category="restaurant",
        crowd_level="medium",
        cost=85.0,
        tags=["local_food", "emirati"],
    )
    beach = _make_place("al_sufouh_beach", "Al Sufouh Beach", category="beach", cost=0.0, tags=["beach"])
    cafe = _make_place(
        "bu_qtair",
        "Bu Qtair Seafood",
        category="restaurant",
        crowd_level="medium",
        cost=75.0,
        tags=["local_food", "seafood"],
    )

    days = [
        DayPlan(
            day_number=1,
            theme="Old Dubai Heritage",
            area_focus="Al Fahidi & Al Shindagha",
            activities=[
                ScheduledActivity(
                    start_time=time(10, 0),
                    end_time=time(11, 30),
                    place=museum,
                    transit_to_next=_make_transit(museum.name, tea_house.name, duration_mins=15),
                ),
                ScheduledActivity(
                    start_time=time(11, 50),
                    end_time=time(13, 0),
                    place=tea_house,
                ),
            ],
        ),
        DayPlan(
            day_number=2,
            theme="Quiet Coast & Local Seafood",
            area_focus="Jumeirah",
            activities=[
                ScheduledActivity(
                    start_time=time(9, 30),
                    end_time=time(11, 30),
                    place=beach,
                    transit_to_next=_make_transit(beach.name, cafe.name, duration_mins=20),
                ),
                ScheduledActivity(
                    start_time=time(12, 0),
                    end_time=time(13, 15),
                    place=cafe,
                ),
            ],
        ),
    ]
    budget = BudgetBreakdown(
        accommodation_aed=350.0,
        food_aed=160.0,
        transportation_aed=40.0,
        activities_aed=50.0,
        total_aed=600.0,
        remaining_aed=1400.0,
        over_budget=False,
    )
    return request, hotel, days, budget


def test_validator_passes_valid_itinerary() -> None:
    request, hotel, days, budget = _valid_fixture()
    result = ItineraryValidator().validate(
        request=request, days=days, budget=budget, accommodation=hotel
    )
    assert result.passed is True
    assert result.violations == []
    assert result.revision_instructions == []


def test_validator_detects_over_budget_and_sum_mismatch() -> None:
    request, hotel, days, budget = _valid_fixture()
    bad_budget = budget.model_copy(
        update={
            "accommodation_aed": 1800.0,
            "total_aed": 2400.0,
            "remaining_aed": -400.0,
            "over_budget": True,
            "alternatives": [
                BudgetAlternative(
                    description="Switch to Al Fahidi budget stay",
                    target_category="accommodation",
                    estimated_savings_aed=700.0,
                )
            ],
        }
    )
    result = ItineraryValidator().validate(
        request=request, days=days, budget=bad_budget, accommodation=hotel
    )
    assert result.passed is False
    assert any(v.type == "budget_exceeded" for v in result.violations)
    assert any("AED 2000" in inst for inst in result.revision_instructions)

    mismatched = budget.model_copy(update={"total_aed": 999.0})
    res_mismatch = ItineraryValidator().validate(
        request=request, days=days, budget=mismatched, accommodation=hotel
    )
    assert res_mismatch.passed is False
    assert any("does not match total_aed" in v.message for v in res_mismatch.violations)


def test_validator_detects_duration_mismatch_and_missing_accommodation() -> None:
    request, _, days, budget = _valid_fixture()
    result = ItineraryValidator().validate(
        request=request, days=days[:1], budget=budget, accommodation=None
    )
    assert result.passed is False
    missing_msgs = [v.message for v in result.violations if v.type == "missing_info"]
    assert any("expected 2 day(s)" in m for m in missing_msgs)
    assert any("Accommodation option is missing" in m for m in missing_msgs)


def test_validator_detects_duplicate_poi_across_days() -> None:
    request, hotel, days, budget = _valid_fixture()
    dup_place = days[0].activities[0].place
    days[1].activities[0] = ScheduledActivity(
        start_time=time(9, 30),
        end_time=time(11, 30),
        place=dup_place,
        transit_to_next=days[1].activities[0].transit_to_next,
    )
    result = ItineraryValidator().validate(
        request=request, days=days, budget=budget, accommodation=hotel
    )
    assert result.passed is False
    assert any(
        v.type == "duplicate_activity" and v.item_id == dup_place.id
        for v in result.violations
    )


def test_validator_detects_overlapping_times_and_insufficient_transit_buffer() -> None:
    request, hotel, days, budget = _valid_fixture()
    # Overlapping activity on Day 1
    days[0].activities[1] = ScheduledActivity(
        start_time=time(11, 0),
        end_time=time(12, 30),
        place=days[0].activities[1].place,
    )
    # Insufficient transit buffer on Day 2 (10 min gap < 20 min transit)
    days[1].activities[1] = ScheduledActivity(
        start_time=time(11, 40),
        end_time=time(13, 0),
        place=days[1].activities[1].place,
    )

    result = ItineraryValidator().validate(
        request=request, days=days, budget=budget, accommodation=hotel
    )
    assert result.passed is False
    schedule_violations = [v for v in result.violations if v.type == "schedule_conflict"]
    assert len(schedule_violations) == 2
    assert any("overlaps" in v.message for v in schedule_violations)
    assert any("Only 10 min gap" in v.message for v in schedule_violations)


def test_validator_detects_zero_transit_buffer_and_missing_transit() -> None:
    request, hotel, days, budget = _valid_fixture()
    days[0].activities[0].transit_to_next = None
    days[1].activities[0].transit_to_next = _make_transit("A", "B", duration_mins=0)

    result = ItineraryValidator().validate(
        request=request, days=days, budget=budget, accommodation=hotel
    )
    assert result.passed is False
    msgs = [v.message for v in result.violations if v.type == "schedule_conflict"]
    assert any("Missing transit segment" in m for m in msgs)
    assert any("must be > 0 mins" in m for m in msgs)


def test_validator_detects_crowd_and_interest_mismatches() -> None:
    request, hotel, days, budget = _valid_fixture()
    high_crowd_hotel = hotel.model_copy(update={"crowd_level": "high"})
    crowded_mall_tower = _make_place(
        "burj_khalifa",
        "Burj Khalifa At The Top",
        category="attraction",
        crowd_level="high",
        cost=179.0,
        tags=["landmarks"],
    )
    # Replace beach on Day 2 with high-crowd landmark so both 'beach' interest & 'low' crowd fail
    days[1].activities[0] = ScheduledActivity(
        start_time=time(9, 30),
        end_time=time(11, 30),
        place=crowded_mall_tower,
        transit_to_next=_make_transit(crowded_mall_tower.name, days[1].activities[1].place.name),
    )

    result = ItineraryValidator().validate(
        request=request, days=days, budget=budget, accommodation=high_crowd_hotel
    )
    assert result.passed is False
    pref_violations = [v for v in result.violations if v.type == "preference_mismatch"]
    assert len(pref_violations) == 3
    assert any("beach" in v.message for v in pref_violations)
    assert any("XVA Art Hotel" in v.message for v in pref_violations)
    assert any("Burj Khalifa" in v.message for v in pref_violations)


def test_agent_tools_execute_cleanly_in_offline_mode(tmp_path: Path) -> None:
    settings = load_settings()
    assert settings.curated_data_path is not None
    service = DataService(
        curated_data_path=settings.curated_data_path,
        cache_db_path=tmp_path / "tools_cache.db",
        offline_mode=True,
    )
    set_data_service(service)
    try:
        assert len(ALL_TOOLS) == 7

        attractions = search_attractions.invoke(
            {"interests": ["cultural", "beach"], "crowd_preference": "low"}
        )
        assert len(attractions) >= 5
        assert all(item["crowd_level"] != "high" for item in attractions)

        restaurants = search_restaurants.invoke(
            {"interests": ["local_food"], "local_only": True, "crowd_preference": "low"}
        )
        assert len(restaurants) >= 3

        hotels = get_accommodation_options.invoke(
            {"tier": "budget", "crowd_preference": "low"}
        )
        assert len(hotels) >= 2

        route = get_route_and_transit.invoke(
            {
                "from_place": "Al Fahidi",
                "to_place": "Dubai Frame",
                "from_lat": 25.2634,
                "from_lon": 55.2974,
                "to_lat": 25.2355,
                "to_lon": 55.3003,
            }
        )
        assert route["duration_mins"] > 0

        budget = estimate_trip_budget.invoke(
            {
                "duration_days": 4,
                "nightly_rate_aed": 650.0,
                "budget_aed": 2500.0,
                "activity_costs_aed": [179.0, 50.0],
                "meal_costs_aed": [180.0, 150.0, 160.0, 140.0],
                "transit_costs_aed": [30.0, 30.0, 30.0, 30.0],
            }
        )
        assert budget["over_budget"] is True
        assert budget["total_aed"] == pytest.approx(650.0 * 3 + 229.0 + 630.0 + 120.0)

        alts = find_budget_alternatives.invoke(
            {
                "budget_aed": 2500.0,
                "current_total_aed": budget["total_aed"],
                "duration_days": 4,
                "current_hotel_nightly_aed": 650.0,
                "crowd_preference": "low",
            }
        )
        assert len(alts) >= 3
        assert any(a["target_category"] == "accommodation" for a in alts)

        weather = get_weather_forecast.invoke(
            {"start_date": "2027-07-15", "end_date": "2027-07-18"}
        )
        assert weather["is_hot_season"] is True
        assert weather["provenance"]["source_type"] == "verified_dataset"
    finally:
        set_data_service(None)
