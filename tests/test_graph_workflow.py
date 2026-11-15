"""Phase 4 tests: LangGraph multi-agent workflow, parallel execution, and revision loop."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from dubai_planner.config import load_settings
from dubai_planner.data import DataService
from dubai_planner.graph import should_revise
from dubai_planner.models.schemas import FinalItinerary, ReviewResult, TravelRequest
from dubai_planner.runner import run_planner, stream_planner
from dubai_planner.validation import ItineraryValidator


def _offline_service(tmp_path: Path) -> DataService:
    settings = load_settings()
    assert settings.curated_data_path is not None
    return DataService(
        curated_data_path=settings.curated_data_path,
        cache_db_path=tmp_path / "graph_cache.db",
        offline_mode=True,
    )


def test_primary_example_prompt_produces_valid_itinerary(tmp_path: Path) -> None:
    prompt = (
        "Plan a 4-day trip to Dubai with a budget of AED 5,000, excluding flights. "
        "I enjoy cultural attractions, local food, and beaches. "
        "I prefer affordable hotels and want to avoid crowded places."
    )
    service = _offline_service(tmp_path)
    itinerary = run_planner(prompt, data_service=service, use_llm=False)

    assert isinstance(itinerary, FinalItinerary)
    assert itinerary.request.duration_days == 4
    assert itinerary.request.budget_aed == 5000.0
    assert itinerary.request.hotel_preference == "budget"
    assert itinerary.request.crowd_preference == "low"
    assert len(itinerary.days) == 4
    assert itinerary.budget.total_aed <= 5000.0
    assert itinerary.budget.over_budget is False

    # Validate via deterministic rules
    check = ItineraryValidator().validate_itinerary(itinerary)
    assert check.passed is True

    # Verify all 5 specialized agents emitted events
    agents = {ev.agent_name for ev in itinerary.execution_trace.events}
    assert agents == {"orchestrator", "destination_research", "logistics", "budget", "review"}


def test_tight_budget_triggers_review_revise_pass_and_logs_state_diff(tmp_path: Path) -> None:
    prompt = (
        "Plan a 4-day trip to Dubai with a budget of AED 1,800, excluding flights. "
        "I enjoy cultural attractions, local food, and beaches. "
        "I want a mid-range hotel and quiet places."
    )
    service = _offline_service(tmp_path)
    itinerary = run_planner(prompt, data_service=service, use_llm=False, max_revisions=2)

    assert len(itinerary.review_history) >= 2
    assert itinerary.review_history[0].passed is False
    assert any(v.type == "budget_exceeded" for v in itinerary.review_history[0].violations)
    assert itinerary.review_history[-1].passed is True
    assert itinerary.budget.total_aed <= 1800.0
    assert itinerary.budget.over_budget is False

    revision_events = [
        ev for ev in itinerary.execution_trace.events if ev.node_name == "apply_revision_plan"
    ]
    assert len(revision_events) >= 1
    assert revision_events[0].state_diff is not None
    assert "budget_exceeded" in revision_events[0].state_diff["triggered_by"]

    final_events = [
        ev for ev in itinerary.execution_trace.events if ev.node_name == "finalize_itinerary"
    ]
    assert len(final_events) == 1
    assert final_events[0].state_diff is not None
    assert final_events[0].state_diff["savings_aed"] > 0

    check = ItineraryValidator().validate_itinerary(itinerary)
    assert check.passed is True


def test_stream_planner_emits_node_updates_and_final_itinerary(tmp_path: Path) -> None:
    prompt = "Plan a 2-day trip to Dubai with AED 3,500 for cultural attractions and beaches."
    service = _offline_service(tmp_path)

    seen_nodes: list[str] = []
    final_itinerary: FinalItinerary | None = None

    for update in stream_planner(prompt, data_service=service, use_llm=False):
        for node_name, payload in update.items():
            seen_nodes.append(node_name)
            if node_name == "finalize_itinerary":
                final_itinerary = payload.get("final_itinerary")

    assert "parse_request" in seen_nodes
    assert "destination_research" in seen_nodes
    assert "logistics_scout" in seen_nodes
    assert "logistics_sequence" in seen_nodes
    assert "budget" in seen_nodes
    assert "review" in seen_nodes
    assert "finalize_itinerary" in seen_nodes
    assert final_itinerary is not None
    assert len(final_itinerary.days) == 2


def test_should_revise_respects_max_revisions_bound() -> None:
    failed = ReviewResult(passed=False, revision_instructions="Lower hotel cost.")
    assert should_revise({"review_result": failed, "revision_count": 0, "max_revisions": 2}) == "revise"
    assert should_revise({"review_result": failed, "revision_count": 2, "max_revisions": 2}) == "finalize"
    assert (
        should_revise(
            {"review_result": ReviewResult(passed=True), "revision_count": 0, "max_revisions": 2}
        )
        == "finalize"
    )


def test_seven_day_and_summer_weather_workflow(tmp_path: Path) -> None:
    service = _offline_service(tmp_path)
    summer_itinerary = run_planner(
        "Plan a 7-day trip to Dubai in July with a budget of AED 6,500. "
        "I enjoy cultural attractions, local food, and beaches. Avoid crowded places.",
        data_service=service,
        use_llm=False,
    )
    assert len(summer_itinerary.days) == 7
    assert summer_itinerary.days[0].weather_note is not None
    assert "heat" in summer_itinerary.days[0].weather_note.lower()
    assert ItineraryValidator().validate_itinerary(summer_itinerary).passed is True


def test_role_based_llm_routing_uses_primary_and_review_models(tmp_path: Path) -> None:
    service = _offline_service(tmp_path)

    parsed_request = TravelRequest(
        duration_days=2,
        budget_aed=3500.0,
        interests=["cultural", "local_food", "beach"],
        hotel_preference="budget",
        crowd_preference="low",
    )
    structured_runner = MagicMock()
    structured_runner.invoke.return_value = parsed_request

    primary_llm = MagicMock()
    primary_llm.model_name = "openai/gpt-oss-120b"
    primary_llm.with_structured_output.return_value = structured_runner

    review_llm = MagicMock()
    review_llm.model = "gemini-3.8-flash"
    review_llm.invoke.return_value = SimpleNamespace(
        content=[{"type": "text", "text": "PASS Itinerary is well paced and respects quiet preferences."}]
    )

    itinerary = run_planner(
        "Plan a 2-day quiet cultural & beach Dubai trip for AED 3,500",
        data_service=service,
        llm=primary_llm,
        review_llm=review_llm,
    )

    primary_llm.with_structured_output.assert_called_once_with(TravelRequest)
    structured_runner.invoke.assert_called_once()
    review_llm.invoke.assert_called_once()

    review_events = [e for e in itinerary.execution_trace.events if e.node_name == "review"]
    assert review_events[-1].outputs_summary["model"] == "gemini-3.8-flash"
    assert "PASS Itinerary is well paced" in (itinerary.review_history[-1].summary or "")
