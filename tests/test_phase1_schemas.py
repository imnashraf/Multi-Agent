"""Phase 1 tests: configuration, core Pydantic v2 schemas, and execution trace models."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest
from pydantic import ValidationError

from dubai_planner.config import get_chat_model, get_review_chat_model, load_settings
from dubai_planner.models import (
    AccommodationOption,
    AgentEvent,
    BudgetAlternative,
    BudgetBreakdown,
    DayPlan,
    ExecutionTrace,
    FinalItinerary,
    PlaceCandidate,
    Provenance,
    ReviewResult,
    ScheduledActivity,
    TransitSegment,
    TravelRequest,
    ValidationViolation,
)


def _sample_provenance(source_type: str = "verified_dataset", source_name: str = "dubai_curated.json") -> Provenance:
    return Provenance(
        source_type=source_type,  # type: ignore[arg-type]
        source_name=source_name,
        last_verified="2026-01-15",
    )


def test_travel_request_normalization_and_validation() -> None:
    req = TravelRequest(
        duration_days=4,
        budget_aed=5000,
        interests=["Cultural", "local food", "beaches", "cultural"],
        hotel_preference="affordable",  # type: ignore[arg-type]
        crowd_preference="avoid crowded",  # type: ignore[arg-type]
        travel_dates=(date(2026, 12, 10), date(2026, 12, 13)),  # type: ignore[arg-type]
        raw_prompt="Plan a 4-day trip to Dubai with a budget of AED 5,000, excluding flights.",
    )
    assert req.destination == "Dubai"
    assert req.hotel_preference == "budget"
    assert req.crowd_preference == "low"
    assert req.interests == ["cultural", "local_food", "beaches"]
    assert req.travel_dates is not None
    assert req.travel_dates.start_date == date(2026, 12, 10)
    assert req.travel_dates.end_date == date(2026, 12, 13)

    roundtrip = TravelRequest.model_validate_json(req.model_dump_json())
    assert roundtrip == req


@pytest.mark.parametrize("bad_days", [0, 8])
def test_travel_request_duration_bounds(bad_days: int) -> None:
    with pytest.raises(ValidationError):
        TravelRequest(duration_days=bad_days, budget_aed=2500)


def test_travel_dates_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError):
        TravelRequest(
            duration_days=3,
            budget_aed=3000,
            travel_dates=(date(2026, 12, 15), date(2026, 12, 10)),  # type: ignore[arg-type]
        )


def test_scheduled_activity_requires_end_after_start() -> None:
    place = PlaceCandidate(
        id="al_fahidi_fort",
        name="Al Fahidi Historical Neighbourhood",
        category="attraction",
        area="Al Fahidi",
        lat=25.2632,
        lon=55.2972,
        crowd_level="low",
        estimated_cost_aed=0.0,
        duration_mins=90,
        tags=["cultural", "heritage"],
        indoor_outdoor="mixed",
        provenance=_sample_provenance(),
    )
    with pytest.raises(ValidationError):
        ScheduledActivity(start_time=time(11, 0), end_time=time(10, 0), place=place)


def test_final_itinerary_roundtrip_serialization() -> None:
    request = TravelRequest(
        duration_days=1,
        budget_aed=1500,
        interests=["cultural", "local_food"],
        hotel_preference="budget",
        crowd_preference="low",
    )
    accommodation = AccommodationOption(
        id="xva_art_hotel",
        name="XVA Art Hotel",
        area="Al Fahidi",
        tier="budget",
        nightly_rate_aed=380.0,
        metro_access="Sharaf DG Metro (Green Line, 6 min walk)",
        metro_walk_mins=6,
        crowd_level="low",
        lat=25.2636,
        lon=55.2981,
        provenance=_sample_provenance(),
    )
    museum = PlaceCandidate(
        id="shindagha_museum",
        name="Al Shindagha Museum",
        category="attraction",
        area="Al Shindagha",
        lat=25.2673,
        lon=55.2916,
        crowd_level="low",
        estimated_cost_aed=50.0,
        duration_mins=120,
        tags=["cultural", "museum"],
        indoor_outdoor="mixed",
        opening_hours="10:00-20:00",
        provenance=_sample_provenance(),
    )
    cafe = PlaceCandidate(
        id="arabian_tea_house",
        name="Arabian Tea House",
        category="restaurant",
        area="Al Fahidi",
        lat=25.2634,
        lon=55.2980,
        crowd_level="medium",
        estimated_cost_aed=85.0,
        duration_mins=75,
        tags=["local_food", "emirati"],
        indoor_outdoor="mixed",
        provenance=_sample_provenance("estimated", "curated_meal_tiers"),
    )
    transit = TransitSegment(
        from_place="Al Shindagha Museum",
        to_place="Arabian Tea House",
        mode="walk",
        duration_mins=12,
        distance_km=0.9,
        estimated_fare_aed=0.0,
        provenance=_sample_provenance("api_derived", "osrm"),
    )
    day_1 = DayPlan(
        day_number=1,
        theme="Old Dubai Heritage & Emirati Flavors",
        area_focus="Al Fahidi & Al Shindagha",
        activities=[
            ScheduledActivity(
                start_time=time(10, 0),
                end_time=time(12, 0),
                place=museum,
                transit_to_next=transit,
            ),
            ScheduledActivity(
                start_time=time(12, 15),
                end_time=time(13, 30),
                place=cafe,
            ),
        ],
        weather_note="Mild 24°C — comfortable for walking tours.",
    )
    budget = BudgetBreakdown(
        accommodation_aed=380.0,
        food_aed=85.0,
        transportation_aed=20.0,
        activities_aed=50.0,
        total_aed=535.0,
        remaining_aed=965.0,
        over_budget=False,
        alternatives=[
            BudgetAlternative(
                description="Use RTA 1-Day Nol Pass for multi-leg transit days.",
                target_category="transportation",
                estimated_savings_aed=10.0,
            )
        ],
        provenance=_sample_provenance(),
    )
    review_fail = ReviewResult(
        passed=False,
        violations=[
            ValidationViolation(
                type="budget_exceeded",
                message="Initial draft exceeded budget by AED 120.",
            )
        ],
        revision_instructions="Swap Marina dinner for Al Fahidi local restaurant.",
        summary="Draft 1 failed budget check.",
    )
    review_pass = ReviewResult(passed=True, summary="All deterministic and semantic checks passed.")

    trace = ExecutionTrace()
    trace.add_event(
        AgentEvent(
            agent_name="orchestrator",
            node_name="parse_request",
            step_type="complete",
            duration_ms=42.5,
            summary="Parsed 1-day Dubai trip request.",
        )
    )
    trace.add_event(
        AgentEvent(
            agent_name="orchestrator",
            node_name="apply_revision_plan",
            step_type="revision",
            duration_ms=15.0,
            revision_number=1,
            summary="Applied budget revision.",
            state_diff={"total_aed": {"before": 1620.0, "after": 535.0}},
        )
    )
    assert trace.total_revisions == 1
    assert trace.total_duration_ms() == pytest.approx(57.5)

    itinerary = FinalItinerary(
        request=request,
        accommodation=accommodation,
        days=[day_1],
        budget=budget,
        review_history=[review_fail, review_pass],
        execution_trace=trace,
    )

    payload = itinerary.model_dump_json()
    restored = FinalItinerary.model_validate_json(payload)
    assert restored == itinerary
    assert restored.days[0].activities[0].place.provenance.source_type == "verified_dataset"
    assert restored.execution_trace.total_revisions == 1


def test_config_loading_and_chat_model_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "LLM_BASE_URL",
        "REVIEW_LLM_PROVIDER",
        "REVIEW_LLM_MODEL",
        "REVIEW_LLM_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    # 1. Verify default Groq (openai/gpt-oss-120b) + Gemini Review settings
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    defaults = load_settings(env_file=empty_env, override=True)
    assert defaults.llm_provider == "groq"
    assert defaults.llm_model == "openai/gpt-oss-120b"
    assert defaults.review_llm_provider == "google_genai"
    assert defaults.review_llm_model == "gemini-3.8-flash"

    # 2. Verify placeholder keys are ignored and missing keys raise ValueError
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_PROVIDER=groq\n"
        "LLM_MODEL=openai/gpt-oss-120b\n"
        "GROQ_API_KEY=your-groq-api-key-here\n"
        "REVIEW_LLM_PROVIDER=google_genai\n"
        "REVIEW_LLM_MODEL=gemini-3.8-flash\n"
        "GOOGLE_API_KEY=your-google-api-key-here\n"
        "MAX_REVISIONS=3\n"
        "OFFLINE_MODE=true\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file, override=True)
    assert settings.llm_api_key is None
    assert settings.review_llm_api_key is None
    assert settings.has_llm_credentials() is False
    assert settings.max_revisions == 3
    assert settings.offline_mode is True

    with pytest.raises(ValueError, match="Missing API key"):
        get_chat_model(settings)
    with pytest.raises(ValueError, match="Missing API key"):
        get_review_chat_model(settings)

    configured = settings.model_copy(
        update={
            "llm_api_key": "gsk_test_placeholder_123456",
            "review_llm_api_key": "AIza_test_placeholder_123456",
        }
    )
    assert configured.has_primary_llm_credentials() is True
    assert configured.has_review_llm_credentials() is True
    assert configured.has_llm_credentials() is True

    primary_model = get_chat_model(configured)
    review_model = get_review_chat_model(configured)
    assert getattr(primary_model, "model_name", None) == "openai/gpt-oss-120b"
    assert getattr(review_model, "model", None) == "gemini-3.8-flash"
