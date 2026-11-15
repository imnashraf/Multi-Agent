"""LangGraph node implementations for all 5 specialized Dubai travel agents."""

from __future__ import annotations

import re
import time as perf_time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from dubai_planner.data.curated import haversine_km
from dubai_planner.data.provider import DataService
from dubai_planner.data.weather_client import WeatherSummary
from dubai_planner.graph.state import TravelPlannerState
from dubai_planner.models.schemas import (
    AccommodationOption,
    BudgetBreakdown,
    CrowdPreference,
    DayPlan,
    FinalItinerary,
    HotelPreference,
    PlaceCandidate,
    ReviewResult,
    ScheduledActivity,
    TransitSegment,
    TravelDates,
    TravelRequest,
)
from dubai_planner.models.trace import AgentEvent, ExecutionTrace
from dubai_planner.tools.agent_tools import (
    _estimate_trip_budget_impl,
    _find_budget_alternatives_impl,
)
from dubai_planner.validation import ItineraryValidator

_MONTH_NAMES: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _add_minutes(clock: time, minutes: int) -> time:
    total = min(23 * 60 + 55, clock.hour * 60 + clock.minute + minutes)
    return time(total // 60, total % 60)


def _round_up_to_5(clock: time) -> time:
    total = clock.hour * 60 + clock.minute
    rem = total % 5
    if rem != 0:
        total += 5 - rem
    total = min(23 * 60 + 50, total)
    return time(total // 60, total % 60)


def _parse_prompt_deterministically(prompt: str) -> TravelRequest:
    """Extract structured TravelRequest fields from natural language without an LLM."""
    lowered = prompt.lower()

    # Duration (1-7 days)
    day_match = re.search(r"\b(\d+)\s*[-\s]*(?:day|days)\b", lowered)
    if day_match:
        duration_days = max(1, min(7, int(day_match.group(1))))
    else:
        night_match = re.search(r"\b(\d+)\s*[-\s]*(?:night|nights)\b", lowered)
        duration_days = max(1, min(7, int(night_match.group(1)) + 1)) if night_match else 4

    # Budget in AED
    aed_match = re.search(
        r"(?:aed|dhs|dirhams?)\s*[:\-]?\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:aed|dhs|dirhams?)",
        lowered,
    )
    if aed_match:
        raw_num = aed_match.group(1) or aed_match.group(2)
        budget_aed = max(300.0, float(raw_num.replace(",", "")))
    else:
        budget_aed = 5000.0

    # Interests
    interests: list[str] = []
    if any(k in lowered for k in ("cultur", "heritage", "museum", "history", "souk", "mosque")):
        interests.append("cultural")
    if any(k in lowered for k in ("food", "eat", "dining", "cuisine", "emirati", "restaurant")):
        interests.append("local_food")
    if any(k in lowered for k in ("beach", "coast", "sea", "swim")):
        interests.append("beach")
    if any(k in lowered for k in ("art", "gallery", "alserkal")):
        interests.append("art")
    if any(k in lowered for k in ("nature", "park", "wildlife")):
        interests.append("nature")
    if any(k in lowered for k in ("landmark", "burj", "skyline", "downtown")):
        interests.append("landmarks")
    if not interests:
        interests = ["cultural", "local_food", "beach"]

    # Hotel preference (check explicit hotel/tier phrases so "with a budget of AED ..." isn't misread)
    hotel_pref: HotelPreference = "mid_range"
    if any(
        k in lowered
        for k in (
            "luxury",
            "5-star",
            "5 star",
            "upscale",
            "resort",
        )
    ):
        hotel_pref = "luxury"
    elif any(
        k in lowered
        for k in (
            "mid-range",
            "midrange",
            "mid range",
            "moderate hotel",
            "standard hotel",
        )
    ):
        hotel_pref = "mid_range"
    elif any(
        k in lowered
        for k in (
            "affordable",
            "cheap",
            "econom",
            "inexpensive",
            "budget hotel",
            "budget stay",
            "budget accommodation",
            "budget-friendly",
            "low-cost hotel",
        )
    ):
        hotel_pref = "budget"

    # Crowd preference
    crowd_pref: CrowdPreference = "any"
    if any(
        k in lowered
        for k in ("avoid crowd", "avoid crowded", "quiet", "less crowded", "peaceful", "not crowded")
    ):
        crowd_pref = "low"
    elif "moderate crowd" in lowered:
        crowd_pref = "medium"

    # Optional dates (ISO YYYY-MM-DD or month mention)
    travel_dates: TravelDates | None = None
    iso_dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", prompt)
    if len(iso_dates) >= 2:
        d1, d2 = date.fromisoformat(iso_dates[0]), date.fromisoformat(iso_dates[1])
        if d2 >= d1:
            travel_dates = TravelDates(start_date=d1, end_date=d2)
    elif len(iso_dates) == 1:
        d1 = date.fromisoformat(iso_dates[0])
        travel_dates = TravelDates(
            start_date=d1, end_date=d1 + timedelta(days=max(0, duration_days - 1))
        )
    else:
        for month_name, month_num in _MONTH_NAMES.items():
            if re.search(rf"\b{month_name}\b", lowered):
                today = date.today()
                year = today.year if month_num >= today.month else today.year + 1
                start = date(year, month_num, 10)
                travel_dates = TravelDates(
                    start_date=start,
                    end_date=start + timedelta(days=max(0, duration_days - 1)),
                )
                break

    excludes_flights = "including flight" not in lowered and "with flight" not in lowered

    return TravelRequest(
        destination="Dubai",
        duration_days=duration_days,
        budget_aed=budget_aed,
        excludes_flights=excludes_flights,
        interests=interests,
        hotel_preference=hotel_pref,
        crowd_preference=crowd_pref,
        travel_dates=travel_dates,
        raw_prompt=prompt,
    )


def _place_matches_interest(place: PlaceCandidate, interest: str) -> bool:
    norm = interest.strip().lower().replace(" ", "_")
    tokens = {t.lower() for t in place.tags} | {place.category.lower()}
    synonyms = {
        "cultural": {"cultural", "heritage", "museum", "art", "architecture", "history", "markets"},
        "cultural_attractions": {"cultural", "heritage", "museum", "art", "architecture", "history", "markets"},
        "local_food": {"local_food", "emirati", "street_food", "heritage_eatery", "seafood", "restaurant"},
        "beach": {"beach", "beaches", "swimming"},
        "beaches": {"beach", "beaches", "swimming"},
    }
    return bool(tokens & synonyms.get(norm, {norm}))


def _model_label(model: BaseChatModel | None) -> str | None:
    if model is None:
        return None
    for attr in ("model_name", "model"):
        val = getattr(model, attr, None)
        if isinstance(val, str) and val:
            return val
    return type(model).__name__


def _extract_llm_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return " ".join(p.strip() for p in parts if p.strip()).strip()
    return str(content).strip()


class PlannerNodes:
    """Encapsulates all 5 LangGraph agent node methods with role-specific LLMs."""

    def __init__(
        self,
        data_service: DataService,
        llm: BaseChatModel | None = None,
        *,
        review_llm: BaseChatModel | None = None,
        validator: ItineraryValidator | None = None,
    ) -> None:
        self.data_service = data_service
        self.llm = llm
        self.review_llm = review_llm if review_llm is not None else llm
        self.validator = validator or ItineraryValidator()

    def parse_request(self, state: TravelPlannerState) -> dict[str, Any]:
        """Agent 1 (Orchestrator): Parse natural-language prompt into TravelRequest."""
        t0 = perf_time.perf_counter()
        prompt = state.get("user_prompt", "").strip()
        if not prompt:
            raise ValueError("user_prompt must be non-empty")

        request: TravelRequest | None = None
        if self.llm is not None:
            try:
                structured_llm = self.llm.with_structured_output(TravelRequest)
                parsed = structured_llm.invoke(
                    "Extract a structured Dubai TravelRequest (duration_days 1..7, budget_aed, "
                    "interests using canonical tags like 'cultural', 'local_food', 'beach', 'art', "
                    "'nature', 'landmarks', hotel_preference, crowd_preference, optional travel_dates) from:\n"
                    f"{prompt}"
                )
                if isinstance(parsed, TravelRequest):
                    request = parsed.model_copy(update={"raw_prompt": prompt})
            except Exception:
                request = None

        if request is None:
            request = _parse_prompt_deterministically(prompt)

        elapsed_ms = round((perf_time.perf_counter() - t0) * 1000.0, 2)
        event = AgentEvent(
            agent_name="orchestrator",
            node_name="parse_request",
            step_type="complete",
            duration_ms=elapsed_ms,
            summary=(
                f"Parsed {request.duration_days}-day Dubai request "
                f"(AED {request.budget_aed:,.0f}, hotel={request.hotel_preference}, "
                f"crowd={request.crowd_preference}, interests={request.interests})."
            ),
            inputs_summary={"prompt": prompt[:140]},
            outputs_summary={
                "duration_days": request.duration_days,
                "budget_aed": request.budget_aed,
                "hotel_preference": request.hotel_preference,
                "crowd_preference": request.crowd_preference,
                "interests": request.interests,
            },
        )
        return {
            "travel_request": request,
            "revision_count": state.get("revision_count", 0),
            "revision_instructions": state.get("revision_instructions", []),
            "revision_strategy": state.get("revision_strategy", {}),
            "execution_trace": [event],
        }

    def destination_research(self, state: TravelPlannerState) -> dict[str, Any]:
        """Agent 2 (Destination Research): Query attractions, beaches, local food, and weather."""
        t0 = perf_time.perf_counter()
        request = state["travel_request"]
        assert request is not None
        strategy = state.get("revision_strategy") or {}
        rev = state.get("revision_count", 0)

        crowd_pref: CrowdPreference = (
            "low" if strategy.get("exclude_high_crowd") else request.crowd_preference
        )
        max_attr_cost = strategy.get("max_attraction_cost_aed")
        max_meal_cost = strategy.get("max_meal_aed")

        weather = self.data_service.get_weather(request.travel_dates)

        needed_places = max(6, request.duration_days * 3)
        places = self.data_service.search_attractions(
            interests=request.interests,
            crowd_preference=crowd_pref,
            max_cost_aed=max_attr_cost,
            include_live=not self.data_service.offline_mode,
            limit=max(20, needed_places),
        )
        # Ensure enough places for multi-day trips if cost filter was very tight
        if len(places) < request.duration_days * 2:
            fallback_places = self.data_service.search_attractions(
                interests=None,
                crowd_preference=crowd_pref,
                max_cost_aed=max_attr_cost,
                include_live=False,
                limit=25,
            )
            seen_ids = {p.id for p in places}
            places.extend(p for p in fallback_places if p.id not in seen_ids)

        # In hot season, float indoor/mixed venues ahead of purely outdoor midday spots
        if weather.is_hot_season:
            places.sort(
                key=lambda p: (
                    0 if p.indoor_outdoor in {"indoor", "mixed"} else 1,
                    p.estimated_cost_aed,
                )
            )
        elif strategy.get("prefer_low_cost_places"):
            places.sort(key=lambda p: (p.estimated_cost_aed, p.name))

        restaurants = self.data_service.search_restaurants(
            interests=request.interests,
            crowd_preference=crowd_pref,
            max_meal_aed=max_meal_cost,
            local_only=True,
            include_live=not self.data_service.offline_mode,
            limit=max(16, request.duration_days * 2 + 2),
        )
        if len(restaurants) < request.duration_days * 2:
            extra_restaurants = self.data_service.search_restaurants(
                crowd_preference=crowd_pref,
                max_meal_aed=None if len(restaurants) < request.duration_days * 2 else max_meal_cost,
                local_only=False,
                include_live=False,
                limit=20,
            )
            seen_r = {r.id for r in restaurants}
            restaurants.extend(r for r in extra_restaurants if r.id not in seen_r)

        if strategy.get("prefer_low_cost_places"):
            restaurants.sort(key=lambda r: (r.estimated_cost_aed, r.name))

        elapsed_ms = round((perf_time.perf_counter() - t0) * 1000.0, 2)
        event = AgentEvent(
            agent_name="destination_research",
            node_name="destination_research",
            step_type="complete",
            duration_ms=elapsed_ms,
            tools_called=["get_weather_forecast", "search_attractions", "search_restaurants"],
            revision_number=rev,
            summary=(
                f"Found {len(places)} attractions/beaches and {len(restaurants)} restaurants "
                f"(weather={weather.season_band}, {weather.temp_min_c:.0f}-{weather.temp_max_c:.0f}°C)."
            ),
            outputs_summary={
                "attractions_count": len(places),
                "restaurants_count": len(restaurants),
                "weather_band": weather.season_band,
            },
        )
        return {
            "weather_summary": weather,
            "candidate_places": places,
            "candidate_restaurants": restaurants,
            "execution_trace": [event],
        }

    def logistics_scout(self, state: TravelPlannerState) -> dict[str, Any]:
        """Agent 3a (Logistics Scout): Select area & hotel options in parallel."""
        t0 = perf_time.perf_counter()
        request = state["travel_request"]
        assert request is not None
        strategy = state.get("revision_strategy") or {}
        rev = state.get("revision_count", 0)

        crowd_pref: CrowdPreference = (
            "low" if strategy.get("exclude_high_crowd") else request.crowd_preference
        )
        target_tier: HotelPreference = (
            "budget" if strategy.get("prefer_cheapest_hotel") else request.hotel_preference
        )

        preferred_areas = ["al_fahidi", "bur_dubai", "deira"]
        if "beach" in request.interests or "beaches" in request.interests:
            preferred_areas.append("jumeirah")

        hotels = self.data_service.search_hotels(
            tier=target_tier,
            crowd_preference=crowd_pref,
            preferred_areas=preferred_areas,
            limit=10,
        )
        if not hotels:
            hotels = self.data_service.search_hotels(
                tier=None, crowd_preference=crowd_pref, limit=10
            )

        if strategy.get("prefer_cheapest_hotel"):
            hotels.sort(key=lambda h: (h.nightly_rate_aed, h.metro_walk_mins or 99))

        elapsed_ms = round((perf_time.perf_counter() - t0) * 1000.0, 2)
        top_hotel = hotels[0] if hotels else None
        event = AgentEvent(
            agent_name="logistics",
            node_name="logistics_scout",
            step_type="complete",
            duration_ms=elapsed_ms,
            tools_called=["get_accommodation_options"],
            revision_number=rev,
            summary=(
                f"Scouted {len(hotels)} hotel option(s) (tier={target_tier}); "
                f"top pick={top_hotel.name if top_hotel else 'none'} "
                f"(AED {top_hotel.nightly_rate_aed:.0f}/night)."
                if top_hotel
                else "No matching hotels found."
            ),
            outputs_summary={
                "options_count": len(hotels),
                "top_hotel": top_hotel.name if top_hotel else None,
                "top_nightly_aed": top_hotel.nightly_rate_aed if top_hotel else None,
            },
        )
        return {
            "accommodation_options": hotels,
            "execution_trace": [event],
        }

    def logistics_sequence(self, state: TravelPlannerState) -> dict[str, Any]:
        """Agent 3b (Logistics Sequencer): Cluster POIs by area and build DayPlans with transit."""
        t0 = perf_time.perf_counter()
        request = state["travel_request"]
        assert request is not None
        strategy = state.get("revision_strategy") or {}
        rev = state.get("revision_count", 0)
        weather = state.get("weather_summary")
        is_hot = weather.is_hot_season if weather is not None else False

        hotels = list(state.get("accommodation_options") or [])
        crowd_pref: CrowdPreference = (
            "low" if strategy.get("exclude_high_crowd") else request.crowd_preference
        )
        eligible_hotels = [
            h for h in hotels if not (crowd_pref == "low" and h.crowd_level == "high")
        ] or hotels
        if strategy.get("prefer_cheapest_hotel"):
            eligible_hotels.sort(key=lambda h: h.nightly_rate_aed)
        selected_hotel = eligible_hotels[0]

        places_pool = [
            p
            for p in (state.get("candidate_places") or [])
            if not (crowd_pref == "low" and p.crowd_level == "high")
        ]
        restaurants_pool = [
            r
            for r in (state.get("candidate_restaurants") or [])
            if not (crowd_pref == "low" and r.crowd_level == "high")
        ]

        if strategy.get("prefer_low_cost_places"):
            places_pool.sort(key=lambda p: (p.estimated_cost_aed, p.name))
            restaurants_pool.sort(key=lambda r: (r.estimated_cost_aed, r.name))

        used_ids: set[str] = set()

        # Ensure at least one POI covers each non-food requested interest across the trip
        # (local_food/food is covered by the scheduled restaurants on every day).
        required_seed_places: list[PlaceCandidate] = []
        for interest in request.interests:
            norm_interest = interest.strip().lower().replace(" ", "_")
            if norm_interest in {"local_food", "food"} and restaurants_pool:
                continue
            if any(_place_matches_interest(p, interest) for p in required_seed_places):
                continue
            match = next(
                (
                    p
                    for p in places_pool
                    if p.id not in {x.id for x in required_seed_places}
                    and _place_matches_interest(p, interest)
                ),
                None,
            )
            if match is not None:
                required_seed_places.append(match)

        # Build prioritized place sequence (seed places first, then remainder)
        seed_ids = {p.id for p in required_seed_places}
        ordered_places = required_seed_places + [p for p in places_pool if p.id not in seed_ids]

        day_plans: list[DayPlan] = []
        route_calls = 0

        for day_num in range(1, request.duration_days + 1):
            # Pick primary attraction for the day from remaining places
            remaining_places = [p for p in ordered_places if p.id not in used_ids]
            if not remaining_places:
                break
            primary = remaining_places[0]
            used_ids.add(primary.id)

            # Pick secondary attraction: pull an unvisited interest seed if needed to cover all interests in remaining days,
            # otherwise pick geographically closest to primary.
            remaining_after_first = [p for p in ordered_places if p.id not in used_ids]
            unvisited_seeds = [p for p in required_seed_places if p.id not in used_ids]
            remaining_days_after_today = request.duration_days - day_num
            secondary: PlaceCandidate | None = None
            if unvisited_seeds and len(unvisited_seeds) > remaining_days_after_today:
                secondary = min(
                    unvisited_seeds,
                    key=lambda p: haversine_km(primary.lat, primary.lon, p.lat, p.lon),
                )
                used_ids.add(secondary.id)
            elif remaining_after_first:
                secondary = min(
                    remaining_after_first,
                    key=lambda p: (
                        0 if p.area == primary.area else 1,
                        haversine_km(primary.lat, primary.lon, p.lat, p.lon),
                    ),
                )
                used_ids.add(secondary.id)

            # Pick lunch & dinner restaurants geographically near the day's area
            remaining_rests = [r for r in restaurants_pool if r.id not in used_ids]
            lunch: PlaceCandidate | None = None
            dinner: PlaceCandidate | None = None
            if remaining_rests:
                lunch = min(
                    remaining_rests,
                    key=lambda r: (
                        0 if r.area == primary.area else 1,
                        haversine_km(primary.lat, primary.lon, r.lat, r.lon),
                    ),
                )
                used_ids.add(lunch.id)
                remaining_rests = [r for r in restaurants_pool if r.id not in used_ids]
            if remaining_rests:
                anchor = secondary or primary
                dinner = min(
                    remaining_rests,
                    key=lambda r: (
                        0 if r.area == anchor.area else 1,
                        haversine_km(anchor.lat, anchor.lon, r.lat, r.lon),
                    ),
                )
                used_ids.add(dinner.id)

            # Order attractions for weather: in hot season put indoor/mixed at midday and outdoor morning/late afternoon
            stops: list[PlaceCandidate] = []
            if secondary is not None:
                if is_hot and primary.indoor_outdoor == "indoor" and secondary.indoor_outdoor == "outdoor":
                    first_attr, second_attr = secondary, primary
                else:
                    first_attr, second_attr = primary, secondary
                stops = [s for s in (first_attr, lunch, second_attr, dinner) if s is not None]
            else:
                stops = [s for s in (primary, lunch, dinner) if s is not None]

            # Assign non-overlapping chronological times + transit segments
            activities: list[ScheduledActivity] = []
            cursor = time(9, 0) if is_hot else time(9, 30)

            for idx, stop in enumerate(stops):
                # Shift afternoon outdoor activity slightly later in hot weather
                if is_hot and idx >= 2 and stop.indoor_outdoor == "outdoor" and cursor < time(16, 30):
                    cursor = time(16, 30)
                elif idx == len(stops) - 1 and stop.category == "restaurant" and cursor < time(18, 30):
                    cursor = time(18, 30)

                start_t = _round_up_to_5(cursor)
                dur = min(150, max(45, stop.duration_mins))
                end_t = _add_minutes(start_t, dur)

                transit: TransitSegment | None = None
                if idx < len(stops) - 1:
                    nxt = stops[idx + 1]
                    transit = self.data_service.get_route(
                        from_place=stop.name,
                        to_place=nxt.name,
                        from_lat=stop.lat,
                        from_lon=stop.lon,
                        to_lat=nxt.lat,
                        to_lon=nxt.lon,
                    )
                    route_calls += 1
                    buffer_mins = max(transit.duration_mins + 5, 10)
                    cursor = _add_minutes(end_t, buffer_mins)

                activities.append(
                    ScheduledActivity(
                        start_time=start_t,
                        end_time=end_t,
                        place=stop,
                        transit_to_next=transit,
                    )
                )

            day_plans.append(
                DayPlan(
                    day_number=day_num,
                    theme=f"{primary.area} Exploration & Local Flavors",
                    area_focus=primary.area,
                    activities=activities,
                    weather_note=weather.advisory if weather is not None else None,
                )
            )

        elapsed_ms = round((perf_time.perf_counter() - t0) * 1000.0, 2)
        event = AgentEvent(
            agent_name="logistics",
            node_name="logistics_sequence",
            step_type="complete",
            duration_ms=elapsed_ms,
            tools_called=["get_route_and_transit"] if route_calls else [],
            revision_number=rev,
            summary=(
                f"Sequenced {len(day_plans)} day(s) anchored at {selected_hotel.name} "
                f"with {route_calls} transit segment(s)."
            ),
            outputs_summary={
                "days_planned": len(day_plans),
                "selected_hotel": selected_hotel.name,
                "transit_segments": route_calls,
            },
        )
        return {
            "selected_accommodation": selected_hotel,
            "day_plans": day_plans,
            "execution_trace": [event],
        }

    def budget(self, state: TravelPlannerState) -> dict[str, Any]:
        """Agent 4 (Budget Agent): Compute AED breakdown and budget-saving alternatives."""
        t0 = perf_time.perf_counter()
        request = state["travel_request"]
        hotel = state["selected_accommodation"]
        days = state.get("day_plans") or []
        rev = state.get("revision_count", 0)
        assert request is not None and hotel is not None

        activity_costs: list[float] = []
        meal_costs: list[float] = []
        daily_transit_costs: list[float] = []

        for day in days:
            day_segments: list[TransitSegment] = []
            for act in day.activities:
                if act.place.category == "restaurant":
                    meal_costs.append(act.place.estimated_cost_aed)
                else:
                    activity_costs.append(act.place.estimated_cost_aed)
                if act.transit_to_next is not None:
                    day_segments.append(act.transit_to_next)
            daily_transit_costs.append(self.data_service.calculate_daily_transit_cost(day_segments))

        raw_budget = _estimate_trip_budget_impl(
            duration_days=request.duration_days,
            nightly_rate_aed=hotel.nightly_rate_aed,
            budget_aed=request.budget_aed,
            activity_costs_aed=activity_costs,
            meal_costs_aed=meal_costs if meal_costs else None,
            transit_costs_aed=daily_transit_costs if daily_transit_costs else None,
            travelers=request.travelers,
        )
        breakdown = BudgetBreakdown.model_validate(raw_budget)

        tools_used = ["estimate_trip_budget"]
        if breakdown.over_budget:
            alts = _find_budget_alternatives_impl(
                self.data_service,
                budget_aed=request.budget_aed,
                current_total_aed=breakdown.total_aed,
                duration_days=request.duration_days,
                current_hotel_nightly_aed=hotel.nightly_rate_aed,
                crowd_preference=request.crowd_preference,
            )
            breakdown = BudgetBreakdown.model_validate(
                {**breakdown.model_dump(mode="python"), "alternatives": alts}
            )
            tools_used.append("find_budget_alternatives")

        snapshot = {
            "revision": rev,
            "hotel_name": hotel.name,
            "hotel_area": hotel.area,
            "hotel_nightly_aed": hotel.nightly_rate_aed,
            "accommodation_aed": breakdown.accommodation_aed,
            "food_aed": breakdown.food_aed,
            "transportation_aed": breakdown.transportation_aed,
            "activities_aed": breakdown.activities_aed,
            "total_aed": breakdown.total_aed,
            "over_budget": breakdown.over_budget,
        }

        elapsed_ms = round((perf_time.perf_counter() - t0) * 1000.0, 2)
        event = AgentEvent(
            agent_name="budget",
            node_name="budget",
            step_type="complete",
            duration_ms=elapsed_ms,
            tools_called=tools_used,
            revision_number=rev,
            summary=(
                f"Projected total AED {breakdown.total_aed:,.2f} vs budget AED {request.budget_aed:,.2f} "
                f"({'OVER BUDGET' if breakdown.over_budget else 'WITHIN BUDGET'})."
            ),
            outputs_summary=snapshot,
        )
        return {
            "budget_breakdown": breakdown,
            "draft_snapshots": [snapshot],
            "execution_trace": [event],
        }

    def review(self, state: TravelPlannerState) -> dict[str, Any]:
        """Agent 5 (Review Agent): Deterministic rule validation + optional LLM semantic check."""
        t0 = perf_time.perf_counter()
        request = state["travel_request"]
        days = state.get("day_plans") or []
        budget = state["budget_breakdown"]
        hotel = state.get("selected_accommodation")
        rev = state.get("revision_count", 0)
        assert request is not None and budget is not None

        result = self.validator.validate(
            request=request,
            days=days,
            budget=budget,
            accommodation=hotel,
        )

        # Optional semantic check when Review LLM (e.g. Gemini) is configured and deterministic checks passed
        if self.review_llm is not None and result.passed:
            try:
                day_summary = "; ".join(
                    f"Day {d.day_number} ({d.area_focus}): "
                    + ", ".join(a.place.name for a in d.activities)
                    for d in days
                )
                llm_response = self.review_llm.invoke(
                    "Briefly verify if this Dubai itinerary is coherent and aligned with "
                    f"interests={request.interests}, crowd_preference={request.crowd_preference}: "
                    f"{day_summary}. Reply in one short sentence starting with PASS or NOTE."
                )
                note = _extract_llm_text(llm_response)
                if note:
                    result = result.model_copy(
                        update={"summary": f"{result.summary} LLM review: {note[:160]}"}
                    )
            except Exception:
                pass

        elapsed_ms = round((perf_time.perf_counter() - t0) * 1000.0, 2)
        event = AgentEvent(
            agent_name="review",
            node_name="review",
            step_type="complete",
            duration_ms=elapsed_ms,
            revision_number=rev,
            summary=(
                f"Review {'PASSED' if result.passed else 'FAILED'}: "
                f"{result.summary or 'Completed checks.'}"
            ),
            outputs_summary={
                "passed": result.passed,
                "violation_count": len(result.violations),
                "violations": [v.type for v in result.violations],
                "model": _model_label(self.review_llm),
            },
        )
        return {
            "review_result": result,
            "review_history": [result],
            "revision_instructions": result.revision_instructions,
            "execution_trace": [event],
        }

    def apply_revision_plan(self, state: TravelPlannerState) -> dict[str, Any]:
        """Orchestrator Revision Node: Translate ReviewResult violations into targeted adjustments."""
        t0 = perf_time.perf_counter()
        next_rev = state.get("revision_count", 0) + 1
        review_result = state.get("review_result")
        request = state["travel_request"]
        budget = state.get("budget_breakdown")
        assert request is not None

        strategy: dict[str, Any] = dict(state.get("revision_strategy") or {})
        violation_types = {v.type for v in (review_result.violations if review_result else [])}

        if "budget_exceeded" in violation_types:
            strategy["prefer_cheapest_hotel"] = True
            strategy["prefer_low_cost_places"] = True
            if next_rev == 1:
                strategy["max_attraction_cost_aed"] = 15.0
                strategy["max_meal_aed"] = 45.0
            else:
                strategy["max_attraction_cost_aed"] = 0.0
                strategy["max_meal_aed"] = 35.0

        if "preference_mismatch" in violation_types or request.crowd_preference == "low":
            strategy["exclude_high_crowd"] = True

        state_diff = {
            "revision_number": next_rev,
            "triggered_by": sorted(violation_types),
            "previous_total_aed": budget.total_aed if budget else None,
            "target_budget_aed": request.budget_aed,
            "applied_strategy": dict(strategy),
            "instructions": review_result.revision_instructions if review_result else [],
        }

        elapsed_ms = round((perf_time.perf_counter() - t0) * 1000.0, 2)
        event = AgentEvent(
            agent_name="orchestrator",
            node_name="apply_revision_plan",
            step_type="revision",
            duration_ms=elapsed_ms,
            revision_number=next_rev,
            summary=(
                f"Triggered revision #{next_rev} for {', '.join(sorted(violation_types)) or 'review'}; "
                "re-routing to Destination & Logistics agents."
            ),
            state_diff=state_diff,
        )
        return {
            "revision_count": next_rev,
            "revision_strategy": strategy,
            "execution_trace": [event],
        }

    def finalize_itinerary(self, state: TravelPlannerState) -> dict[str, Any]:
        """Orchestrator Finalizer: Assemble validated FinalItinerary and execution trace."""
        t0 = perf_time.perf_counter()
        request = state["travel_request"]
        hotel = state["selected_accommodation"]
        days = state.get("day_plans") or []
        budget = state["budget_breakdown"]
        review_history = list(state.get("review_history") or [])
        snapshots = list(state.get("draft_snapshots") or [])
        events = list(state.get("execution_trace") or [])
        assert request is not None and hotel is not None and budget is not None

        diff_summary: dict[str, Any] | None = None
        if len(snapshots) >= 2:
            first_draft = snapshots[0]
            final_draft = snapshots[-1]
            diff_summary = {
                "draft_1_total_aed": first_draft["total_aed"],
                "final_total_aed": final_draft["total_aed"],
                "savings_aed": round(first_draft["total_aed"] - final_draft["total_aed"], 2),
                "draft_1_hotel": first_draft["hotel_name"],
                "final_hotel": final_draft["hotel_name"],
            }

        elapsed_ms = round((perf_time.perf_counter() - t0) * 1000.0, 2)
        final_event = AgentEvent(
            agent_name="orchestrator",
            node_name="finalize_itinerary",
            step_type="complete",
            duration_ms=elapsed_ms,
            revision_number=state.get("revision_count", 0),
            summary=(
                f"Assembled {len(days)}-day Dubai FinalItinerary "
                f"(total AED {budget.total_aed:,.2f}, revisions={state.get('revision_count', 0)})."
            ),
            state_diff=diff_summary,
        )

        trace = ExecutionTrace(completed_at=datetime.now(timezone.utc))
        for ev in [*events, final_event]:
            trace.add_event(ev)

        itinerary = FinalItinerary(
            request=request,
            accommodation=hotel,
            days=days,
            budget=budget,
            review_history=review_history,
            execution_trace=trace,
        )
        return {
            "final_itinerary": itinerary,
            "execution_trace": [final_event],
        }
