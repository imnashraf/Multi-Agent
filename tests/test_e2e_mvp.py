"""Phase 6 E2E verification tests: 1-7 day edge cases, seasons, online/offline, and Streamlit UI."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from dubai_planner.config import load_settings
from dubai_planner.data import DataService
from dubai_planner.models.schemas import FinalItinerary
from dubai_planner.runner import run_planner
from dubai_planner.validation import ItineraryValidator


def _service(tmp_path: Path, *, offline_mode: bool = True, http_client: httpx.Client | None = None) -> DataService:
    settings = load_settings()
    assert settings.curated_data_path is not None
    return DataService(
        curated_data_path=settings.curated_data_path,
        cache_db_path=tmp_path / "e2e_cache.db",
        offline_mode=offline_mode,
        http_client=http_client,
    )


@pytest.mark.parametrize("days", [1, 2, 3, 4, 5, 6, 7])
def test_e2e_all_durations_one_to_seven_days(tmp_path: Path, days: int) -> None:
    service = _service(tmp_path, offline_mode=True)
    budget = max(1200, days * 1100)
    prompt = (
        f"Plan a {days}-day trip to Dubai with a budget of AED {budget}, excluding flights. "
        "I enjoy cultural attractions, local food, and beaches. "
        "I prefer affordable hotels and want to avoid crowded places."
    )
    itinerary = run_planner(prompt, data_service=service, use_llm=False)

    assert isinstance(itinerary, FinalItinerary)
    assert len(itinerary.days) == days
    assert itinerary.budget.total_aed <= budget
    assert ItineraryValidator().validate_itinerary(itinerary).passed is True

    # Verify zero duplicates across all scheduled days
    seen_ids = [act.place.id for d in itinerary.days for act in d.activities]
    assert len(seen_ids) == len(set(seen_ids))


def test_e2e_summer_vs_winter_weather_adjustments(tmp_path: Path) -> None:
    service = _service(tmp_path, offline_mode=True)

    summer = run_planner(
        "Plan a 3-day trip to Dubai in July with a budget of AED 4,000. "
        "I enjoy cultural attractions, local food, and beaches.",
        data_service=service,
        use_llm=False,
    )
    winter = run_planner(
        "Plan a 3-day trip to Dubai in December with a budget of AED 4,000. "
        "I enjoy cultural attractions, local food, and beaches.",
        data_service=service,
        use_llm=False,
    )

    assert summer.days[0].weather_note is not None
    assert "heat" in summer.days[0].weather_note.lower()
    assert winter.days[0].weather_note is not None
    assert "mild" in winter.days[0].weather_note.lower()

    # In hot season, outdoor afternoon slots are shifted to >= 16:30 or avoided midday
    for day in summer.days:
        for act in day.activities:
            if act.place.indoor_outdoor == "outdoor" and act.start_time.hour >= 11:
                assert act.start_time.hour >= 16


def test_e2e_online_mode_enriches_routes_and_weather_with_api_derived_provenance(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "overpass-api.de" in url:
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 77701,
                            "lat": 25.2650,
                            "lon": 55.2985,
                            "tags": {"name:en": "Al Fahidi Wind Tower Gallery", "tourism": "museum"},
                        }
                    ]
                },
            )
        if "router.project-osrm.org" in url:
            return httpx.Response(
                200,
                json={"routes": [{"distance": 1800.0, "duration": 240.0}]},
            )
        if "api.open-meteo.com" in url:
            return httpx.Response(
                200,
                json={
                    "daily": {
                        "temperature_2m_max": [26.0, 27.0, 26.5],
                        "temperature_2m_min": [18.0, 18.5, 19.0],
                        "precipitation_sum": [0.0, 0.0, 0.0],
                    }
                },
            )
        return httpx.Response(404)

    start = date.today() + timedelta(days=2)
    end = start + timedelta(days=2)
    prompt = (
        f"Plan a 3-day trip to Dubai from {start.isoformat()} to {end.isoformat()} "
        "with a budget of AED 4,500. I enjoy cultural attractions, local food, and beaches."
    )

    service = _service(
        tmp_path,
        offline_mode=False,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    itinerary = run_planner(prompt, data_service=service, use_llm=False)

    assert ItineraryValidator().validate_itinerary(itinerary).passed is True
    transit_provenances = {
        act.transit_to_next.provenance.source_type
        for day in itinerary.days
        for act in day.activities
        if act.transit_to_next is not None
    }
    assert "api_derived" in transit_provenances


def test_streamlit_app_renders_primary_and_revision_demo_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OFFLINE_MODE", "true")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("REVIEW_LLM_API_KEY", "")
    app_path = str(Path(__file__).resolve().parents[1] / "app.py")

    # 1. Primary 4-day AED 5,000 prompt
    at = AppTest.from_file(app_path, default_timeout=30)
    at.run()
    assert not at.exception
    at.button[3].click().run()  # "Plan Dubai Trip" primary button
    assert not at.exception
    assert len(at.tabs) == 3
    assert any("Structured Dubai Itinerary" in s.value for s in at.subheader)
    assert any("Passed on Draft 1" in s.value for s in at.success)

    # 2. Tight-budget revision demo button (index 1) -> Plan Dubai Trip (index 3)
    at.button[1].click().run()
    assert not at.exception
    at.button[3].click().run()
    assert not at.exception
    assert any("Draft 1 flagged" in w.value for w in at.warning)
    assert any(m.label == "Total Savings" for m in at.metric)
