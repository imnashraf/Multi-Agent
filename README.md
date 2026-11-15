# Dubai AI Travel Planner (LangGraph Multi-Agent System)

A stateful multi-agent travel planner for **Dubai (1–7 days, AED budgets)** built with **LangGraph**, **Pydantic v2**, **Streamlit**, and an offline-first curated dataset enhanced by free open APIs (**OpenStreetMap Overpass**, **OSRM**, **Open-Meteo**).

## Architecture Overview

Five specialized agents collaborate in a LangGraph `StateGraph`:
1. **Orchestrator (`parse_request`, `apply_revision_plan`, `finalize_itinerary`)** — parses natural-language requests, coordinates bounded revision loops (`max_revisions = 2`), and assembles `FinalItinerary`.
2. **Destination Research (`destination_research`)** — queries attractions, beaches, local restaurants, and weather (runs in parallel with `logistics_scout`).
3. **Logistics (`logistics_scout`, `logistics_sequence`)** — scouts hotels/neighborhoods, clusters POIs geographically, schedules weather-aware non-overlapping slots, and computes transit segments with RTA Nol / taxi fares.
4. **Budget (`budget`)** — calculates category-wise AED costs (including the AED 20 1-day Nol pass cap) and proposes concrete swap alternatives when over budget.
5. **Review (`review`)** — runs deterministic validation (`ItineraryValidator`) plus optional LLM semantic checks to approve or trigger targeted revisions.

## Setup & Running

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # Optional: add an LLM provider API key
```

> **Zero-key mode:** The planner runs end-to-end without any paid travel API keys or LLM keys (using deterministic request parsing, deterministic validation, and `data/dubai_curated.json`). Adding an LLM key in `.env` enables LLM-enhanced request parsing and semantic review.

### Launch Streamlit UI

```bash
streamlit run app.py
```

### Run Test Suite

```bash
pytest
```
