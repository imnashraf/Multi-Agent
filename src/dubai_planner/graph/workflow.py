"""LangGraph StateGraph assembly with parallel fan-out and bounded revision loop."""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from dubai_planner.data.provider import DataService
from dubai_planner.graph.nodes import PlannerNodes
from dubai_planner.graph.state import TravelPlannerState
from dubai_planner.validation import ItineraryValidator


def should_revise(state: TravelPlannerState) -> Literal["revise", "finalize"]:
    """Route to revision when ReviewResult fails and revision_count < max_revisions."""
    review_result = state.get("review_result")
    revision_count = state.get("revision_count", 0)
    max_revisions = state.get("max_revisions", 2)

    if review_result is not None and not review_result.passed and revision_count < max_revisions:
        return "revise"
    return "finalize"


def build_planner_graph(
    data_service: DataService,
    llm: BaseChatModel | None = None,
    *,
    review_llm: BaseChatModel | None = None,
    validator: ItineraryValidator | None = None,
) -> CompiledStateGraph:
    """Construct and compile the 5-agent Dubai Travel Planner LangGraph StateGraph."""
    nodes = PlannerNodes(
        data_service=data_service,
        llm=llm,
        review_llm=review_llm,
        validator=validator,
    )

    graph = StateGraph(TravelPlannerState)
    graph.add_node("parse_request", nodes.parse_request)
    graph.add_node("destination_research", nodes.destination_research)
    graph.add_node("logistics_scout", nodes.logistics_scout)
    graph.add_node("logistics_sequence", nodes.logistics_sequence)
    graph.add_node("budget", nodes.budget)
    graph.add_node("review", nodes.review)
    graph.add_node("apply_revision_plan", nodes.apply_revision_plan)
    graph.add_node("finalize_itinerary", nodes.finalize_itinerary)

    # Initial parse -> parallel fan-out to Destination Research and Logistics Scout
    graph.add_edge(START, "parse_request")
    graph.add_edge("parse_request", "destination_research")
    graph.add_edge("parse_request", "logistics_scout")

    # Barrier join -> Logistics Sequence -> Budget -> Review
    graph.add_edge(["destination_research", "logistics_scout"], "logistics_sequence")
    graph.add_edge("logistics_sequence", "budget")
    graph.add_edge("budget", "review")

    # Bounded feedback loop
    graph.add_conditional_edges(
        "review",
        should_revise,
        {
            "revise": "apply_revision_plan",
            "finalize": "finalize_itinerary",
        },
    )
    graph.add_edge("apply_revision_plan", "destination_research")
    graph.add_edge("apply_revision_plan", "logistics_scout")
    graph.add_edge("finalize_itinerary", END)

    return graph.compile()
