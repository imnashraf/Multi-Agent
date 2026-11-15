"""Streamlit Interactive UI & Observability for the Dubai AI Travel Planner."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import streamlit as st

_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from dubai_planner.config import load_settings
from dubai_planner.models.schemas import (
    FinalItinerary,
    Provenance,
)
from dubai_planner.models.trace import AgentEvent
from dubai_planner.runner import stream_planner


def _sync_streamlit_secrets_to_env() -> None:
    """Populate environment variables from Streamlit Cloud secrets when present."""
    try:
        for key, value in st.secrets.items():
            if isinstance(value, str) and value.strip() and key not in os.environ:
                os.environ[key] = value.strip()
    except Exception:
        pass

EXAMPLE_PROMPTS: dict[str, str] = {
    "4-Day Cultural & Beach (AED 5,000)": (
        "Plan a 4-day trip to Dubai with a budget of AED 5,000, excluding flights. "
        "I enjoy cultural attractions, local food, and beaches. "
        "I prefer affordable hotels and want to avoid crowded places."
    ),
    "Tight-Budget Revision Demo (AED 1,800)": (
        "Plan a 4-day trip to Dubai with a budget of AED 1,800, excluding flights. "
        "I enjoy cultural attractions, local food, and beaches. "
        "I want a mid-range hotel and quiet places."
    ),
    "Summer Heat-Aware Trip (July, 3 Days)": (
        "Plan a 3-day trip to Dubai in July with a budget of AED 3,500, excluding flights. "
        "I enjoy cultural attractions, local food, and beaches. Avoid crowded places."
    ),
}

_PROVENANCE_LABELS: dict[str, str] = {
    "verified_dataset": "verified_dataset",
    "api_derived": "api_derived",
    "estimated": "estimated",
}

_AGENT_LABELS: dict[str, str] = {
    "orchestrator": "Orchestrator",
    "destination_research": "Destination Research",
    "logistics": "Logistics",
    "budget": "Budget",
    "review": "Review",
}


def format_provenance_badge(provenance: Provenance | None) -> str:
    """Format a compact provenance badge string for UI display."""
    if provenance is None:
        return "`estimated`"
    label = _PROVENANCE_LABELS.get(provenance.source_type, provenance.source_type)
    if provenance.last_verified:
        return f"`{label}` ({provenance.source_name}, verified {provenance.last_verified})"
    return f"`{label}` ({provenance.source_name})"


def compute_revision_diff(snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Compute Draft 1 vs Final comparison metrics when >= 2 budget snapshots exist."""
    if len(snapshots) < 2:
        return None
    first = snapshots[0]
    last = snapshots[-1]
    return {
        "draft_1": first,
        "revised": last,
        "savings_aed": round(float(first["total_aed"]) - float(last["total_aed"]), 2),
        "hotel_changed": first.get("hotel_name") != last.get("hotel_name"),
    }


def _render_event_row(event: AgentEvent) -> str:
    agent = _AGENT_LABELS.get(event.agent_name, event.agent_name)
    tools = f" | Tools: {', '.join(event.tools_called)}" if event.tools_called else ""
    latency = f" ({event.duration_ms:.1f} ms)" if event.duration_ms is not None else ""
    rev = f" [Rev #{event.revision_number}]" if event.revision_number > 0 else ""
    return f"**{agent}** · `{event.node_name}`{rev}{latency}{tools}\n\n{event.summary}"


def _render_timeline(events: list[AgentEvent]) -> None:
    st.subheader("Live Agent Execution Timeline")
    if not events:
        st.info("Run the planner to view step-by-step LangGraph node execution.")
        return

    parallel_note = any(
        ev.node_name in {"destination_research", "logistics_scout"} for ev in events
    )
    if parallel_note:
        st.caption(
            "Parallel fan-out: `destination_research` and `logistics_scout` execute concurrently "
            "before joining at `logistics_sequence`."
        )

    for idx, ev in enumerate(events, start=1):
        icon = "🔄" if ev.step_type == "revision" else "✅"
        with st.container(border=True):
            st.markdown(f"{icon} **Step {idx}** — {_render_event_row(ev)}")


def _render_revision_diff(
    itinerary: FinalItinerary,
    snapshots: list[dict[str, Any]],
) -> None:
    st.subheader("Review & Revision Diff")
    reviews = itinerary.review_history
    diff = compute_revision_diff(snapshots)

    if diff is None and len(reviews) <= 1:
        st.success(
            "Passed on Draft 1 — all budget, duration, schedule, duplicate, and preference checks succeeded."
        )
        return

    if reviews and not reviews[0].passed:
        first_review = reviews[0]
        with st.container(border=True):
            st.warning(
                f"Draft 1 flagged {len(first_review.violations)} violation(s): "
                + "; ".join(v.message for v in first_review.violations)
            )
            if first_review.revision_instructions:
                st.markdown(
                    "**Revision Instructions:** "
                    + " | ".join(first_review.revision_instructions)
                )

    if diff is not None:
        d1 = diff["draft_1"]
        rev = diff["revised"]
        col1, col2, col3 = st.columns(3)
        col1.metric("Draft 1 Total", f"AED {d1['total_aed']:,.0f}")
        col2.metric(
            "Revised Total",
            f"AED {rev['total_aed']:,.0f}",
            delta=f"-AED {diff['savings_aed']:,.0f}" if diff["savings_aed"] >= 0 else f"+AED {-diff['savings_aed']:,.0f}",
            delta_color="normal" if diff["savings_aed"] <= 0 else "inverse",
        )
        col3.metric("Total Savings", f"AED {diff['savings_aed']:,.0f}")

        st.table(
            [
                {
                    "Version": "Draft 1 (Initial)",
                    "Hotel": f"{d1['hotel_name']} ({d1['hotel_area']})",
                    "Accommodation (AED)": f"{d1['accommodation_aed']:,.2f}",
                    "Food (AED)": f"{d1['food_aed']:,.2f}",
                    "Activities (AED)": f"{d1['activities_aed']:,.2f}",
                    "Transit (AED)": f"{d1['transportation_aed']:,.2f}",
                    "Total (AED)": f"{d1['total_aed']:,.2f}",
                },
                {
                    "Version": f"Revised (Pass #{rev['revision']})",
                    "Hotel": f"{rev['hotel_name']} ({rev['hotel_area']})",
                    "Accommodation (AED)": f"{rev['accommodation_aed']:,.2f}",
                    "Food (AED)": f"{rev['food_aed']:,.2f}",
                    "Activities (AED)": f"{rev['activities_aed']:,.2f}",
                    "Transit (AED)": f"{rev['transportation_aed']:,.2f}",
                    "Total (AED)": f"{rev['total_aed']:,.2f}",
                },
            ]
        )


def _render_itinerary(itinerary: FinalItinerary) -> None:
    req = itinerary.request
    hotel = itinerary.accommodation
    budget = itinerary.budget

    st.subheader("Structured Dubai Itinerary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Duration", f"{req.duration_days} Day(s)")
    m2.metric("Projected Total", f"AED {budget.total_aed:,.2f}")
    m3.metric("Budget Remaining", f"AED {budget.remaining_aed:,.2f}")
    m4.metric("Revisions", str(itinerary.execution_trace.total_revisions))

    left, right = st.columns([1, 1])
    with left:
        with st.container(border=True):
            st.markdown("#### Recommended Accommodation")
            st.markdown(f"**{hotel.name}** — {hotel.area}")
            st.markdown(
                f"- **Tier:** `{hotel.tier}` | **Crowd:** `{hotel.crowd_level}`\n"
                f"- **Nightly Rate:** AED {hotel.nightly_rate_aed:,.2f}/night\n"
                f"- **Transit Access:** {hotel.metro_access}\n"
                f"- **Provenance:** {format_provenance_badge(hotel.provenance)}"
            )

    with right:
        with st.container(border=True):
            st.markdown("#### AED Budget Breakdown")
            st.table(
                [
                    {"Category": "Accommodation", "Amount (AED)": f"{budget.accommodation_aed:,.2f}"},
                    {"Category": "Food & Local Dining", "Amount (AED)": f"{budget.food_aed:,.2f}"},
                    {"Category": "Transportation (RTA / Taxi)", "Amount (AED)": f"{budget.transportation_aed:,.2f}"},
                    {"Category": "Activities & Attractions", "Amount (AED)": f"{budget.activities_aed:,.2f}"},
                    {"Category": "Total Projected", "Amount (AED)": f"{budget.total_aed:,.2f}"},
                    {"Category": "Target Budget Cap", "Amount (AED)": f"{req.budget_aed:,.2f}"},
                ]
            )
            st.caption(f"Budget Provenance: {format_provenance_badge(budget.provenance)}")
            if budget.alternatives:
                st.markdown("**Cost-Saving Alternatives Identified:**")
                for alt in budget.alternatives:
                    st.markdown(
                        f"- {alt.description} *(est. savings ~AED {alt.estimated_savings_aed:,.0f})*"
                    )

    st.markdown("#### Day-by-Day Schedule")
    for day in itinerary.days:
        with st.expander(
            f"Day {day.day_number}: {day.theme} — Focus: {day.area_focus}",
            expanded=True,
        ):
            if day.weather_note:
                st.info(f"Weather Advisory: {day.weather_note}")

            for act in day.activities:
                p = act.place
                time_range = f"{act.start_time.strftime('%H:%M')} – {act.end_time.strftime('%H:%M')}"
                cost_text = "Free" if p.estimated_cost_aed == 0 else f"AED {p.estimated_cost_aed:,.0f}"
                st.markdown(
                    f"**{time_range}** · **{p.name}** (`{p.category}`, {p.area}) — "
                    f"**{cost_text}** · crowd: `{p.crowd_level}` · {format_provenance_badge(p.provenance)}"
                )
                if p.description:
                    st.caption(p.description)

                if act.transit_to_next is not None:
                    tr = act.transit_to_next
                    st.markdown(
                        f"&nbsp;&nbsp;&nbsp;&nbsp;↳ *{tr.mode.upper()}* to **{tr.to_place}**: "
                        f"{tr.duration_mins} min ({tr.distance_km:.2f} km), "
                        f"AED {tr.estimated_fare_aed:,.2f} · {format_provenance_badge(tr.provenance)}"
                    )

    st.caption(
        "Note: Attraction tickets, meal estimates, and hotel nightly bands use curated Dubai datasets "
        "and open APIs (Overpass, OSRM, Open-Meteo) for planning demonstration only."
    )


def main() -> None:
    st.set_page_config(
        page_title="Dubai AI Travel Planner (LangGraph)",
        page_icon="🕌",
        layout="wide",
    )
    _sync_streamlit_secrets_to_env()
    settings = load_settings()

    if "prompt_input" not in st.session_state:
        st.session_state["prompt_input"] = EXAMPLE_PROMPTS["4-Day Cultural & Beach (AED 5,000)"]

    st.title("Dubai AI Travel Planner — Multi-Agent LangGraph")
    st.caption(
        "Orchestrates 5 specialized agents (Orchestrator, Destination Research, Logistics, Budget, Review) "
        "with parallel execution, deterministic validation, and bounded revision loops."
    )

    with st.sidebar:
        st.header("Planner Settings")
        offline_mode = st.toggle(
            "Offline Mode (100% Curated Local Dataset)",
            value=settings.offline_mode,
            help="Skip Overpass, OSRM, and Open-Meteo calls and use dubai_curated.json + Haversine fallbacks.",
        )
        llm_available = settings.has_llm_credentials()
        use_llm = st.toggle(
            "Use Configured Chat LLMs",
            value=llm_available,
            disabled=not llm_available,
            help=(
                f"Primary (Orchestrator/Research/Logistics/Budget): "
                f"{settings.llm_provider} ({settings.llm_model}) | "
                f"Review: {settings.review_llm_provider} ({settings.review_llm_model})"
            ),
        )
        st.caption(
            f"**Planning Agents:** `{settings.llm_provider}:{settings.llm_model}`  \n"
            f"**Review Agent:** `{settings.review_llm_provider}:{settings.review_llm_model}`"
        )
        max_revisions = st.slider(
            "Max Review Revisions",
            min_value=0,
            max_value=3,
            value=settings.max_revisions,
        )
        if not llm_available:
            st.info(
                "Running in zero-key mode using deterministic parsing/validation and curated Dubai data. "
                "Add `GROQ_API_KEY` and `GOOGLE_API_KEY` in `.env` to enable Groq OSS-120B + Gemini Review."
            )

    st.markdown("**Quick-Load Examples:**")
    btn_cols = st.columns(len(EXAMPLE_PROMPTS))
    for col, (label, sample_prompt) in zip(btn_cols, EXAMPLE_PROMPTS.items()):
        if col.button(label, use_container_width=True):
            st.session_state["prompt_input"] = sample_prompt

    prompt = st.text_area(
        "Describe your Dubai trip (1–7 days, AED budget, interests, hotel & crowd preferences):",
        key="prompt_input",
        height=100,
    )

    if st.button("Plan Dubai Trip", type="primary"):
        if not prompt.strip():
            st.error("Please enter a travel request prompt.")
        else:
            run_settings = settings.model_copy(
                update={"offline_mode": offline_mode, "max_revisions": max_revisions}
            )
            collected_events: list[AgentEvent] = []
            collected_snapshots: list[dict[str, Any]] = []
            final_itinerary: FinalItinerary | None = None

            with st.status("Running multi-agent LangGraph workflow...", expanded=True) as status:
                live_log = st.empty()
                for update in stream_planner(
                    prompt.strip(),
                    settings=run_settings,
                    use_llm=use_llm,
                    max_revisions=max_revisions,
                ):
                    for node_name, payload in update.items():
                        for ev in payload.get("execution_trace") or []:
                            collected_events.append(ev)
                        for snap in payload.get("draft_snapshots") or []:
                            collected_snapshots.append(snap)
                        if node_name == "finalize_itinerary":
                            final_itinerary = payload.get("final_itinerary")

                    with live_log.container():
                        for ev in collected_events[-4:]:
                            st.write(f"• `{ev.node_name}` — {ev.summary}")

                status.update(label="Multi-agent planning complete!", state="complete", expanded=False)

            if final_itinerary is not None:
                st.session_state["last_itinerary"] = final_itinerary
                st.session_state["last_snapshots"] = collected_snapshots

    saved_itinerary: FinalItinerary | None = st.session_state.get("last_itinerary")
    saved_snapshots: list[dict[str, Any]] = st.session_state.get("last_snapshots", [])

    if saved_itinerary is not None:
        tab_itinerary, tab_diff, tab_trace = st.tabs(
            ["Structured Itinerary", "Revision Diff", "Agent Execution Timeline"]
        )
        with tab_itinerary:
            _render_itinerary(saved_itinerary)
        with tab_diff:
            _render_revision_diff(saved_itinerary, saved_snapshots)
        with tab_trace:
            _render_timeline(saved_itinerary.execution_trace.events)


if __name__ == "__main__":
    main()
