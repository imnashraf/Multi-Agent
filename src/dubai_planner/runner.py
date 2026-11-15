"""Unified entry points for executing and streaming the Dubai Travel Planner."""

from __future__ import annotations

from typing import Any, Iterator

from langchain_core.language_models.chat_models import BaseChatModel

from dubai_planner.config import (
    Settings,
    get_chat_model,
    get_review_chat_model,
    load_settings,
)
from dubai_planner.data.provider import DataService
from dubai_planner.graph.state import TravelPlannerState
from dubai_planner.graph.workflow import build_planner_graph
from dubai_planner.models.schemas import FinalItinerary


def _resolve_runtime(
    *,
    settings: Settings | None = None,
    data_service: DataService | None = None,
    llm: BaseChatModel | None = None,
    review_llm: BaseChatModel | None = None,
    use_llm: bool = True,
    max_revisions: int | None = None,
) -> tuple[Settings, DataService, BaseChatModel | None, BaseChatModel | None, int]:
    cfg = settings or load_settings()
    service = data_service or DataService.from_settings(cfg)

    resolved_llm = llm
    if resolved_llm is None and use_llm and cfg.has_primary_llm_credentials():
        try:
            resolved_llm = get_chat_model(cfg)
        except Exception:
            resolved_llm = None

    resolved_review_llm = review_llm
    if resolved_review_llm is None and use_llm and cfg.has_review_llm_credentials():
        try:
            resolved_review_llm = get_review_chat_model(cfg)
        except Exception:
            resolved_review_llm = None

    rev_limit = max_revisions if max_revisions is not None else cfg.max_revisions
    return cfg, service, resolved_llm, resolved_review_llm, rev_limit


def _initial_state(prompt: str, max_revisions: int) -> TravelPlannerState:
    return {
        "user_prompt": prompt,
        "revision_count": 0,
        "max_revisions": max_revisions,
        "revision_instructions": [],
        "revision_strategy": {},
        "candidate_places": [],
        "candidate_restaurants": [],
        "accommodation_options": [],
        "review_history": [],
        "draft_snapshots": [],
        "execution_trace": [],
    }


def run_planner(
    prompt: str,
    *,
    settings: Settings | None = None,
    data_service: DataService | None = None,
    llm: BaseChatModel | None = None,
    review_llm: BaseChatModel | None = None,
    use_llm: bool = True,
    max_revisions: int | None = None,
) -> FinalItinerary:
    """Execute the multi-agent LangGraph workflow synchronously and return FinalItinerary."""
    _, service, resolved_llm, resolved_review_llm, rev_limit = _resolve_runtime(
        settings=settings,
        data_service=data_service,
        llm=llm,
        review_llm=review_llm,
        use_llm=use_llm,
        max_revisions=max_revisions,
    )
    app = build_planner_graph(
        data_service=service,
        llm=resolved_llm,
        review_llm=resolved_review_llm,
    )
    final_state = app.invoke(_initial_state(prompt, rev_limit))
    itinerary = final_state.get("final_itinerary")
    if not isinstance(itinerary, FinalItinerary):
        raise RuntimeError("Workflow completed without producing a FinalItinerary.")
    return itinerary


def stream_planner(
    prompt: str,
    *,
    settings: Settings | None = None,
    data_service: DataService | None = None,
    llm: BaseChatModel | None = None,
    review_llm: BaseChatModel | None = None,
    use_llm: bool = True,
    max_revisions: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream LangGraph node updates step-by-step for real-time UI visualization."""
    _, service, resolved_llm, resolved_review_llm, rev_limit = _resolve_runtime(
        settings=settings,
        data_service=data_service,
        llm=llm,
        review_llm=review_llm,
        use_llm=use_llm,
        max_revisions=max_revisions,
    )
    app = build_planner_graph(
        data_service=service,
        llm=resolved_llm,
        review_llm=resolved_review_llm,
    )
    yield from app.stream(_initial_state(prompt, rev_limit), stream_mode="updates")
