# Project: Dubai AI Travel Planner — Multi-Agent Systems

## 1. Background

Planning a trip involves multiple interconnected decisions, including selecting attractions, finding accommodation, organizing transportation, estimating expenses, and ensuring the itinerary matches the traveler's preferences.

A traditional AI chatbot may generate an itinerary in a single response, but it does not necessarily verify travel times, check budget constraints, or validate recommendations against external information.

This project explores how multiple specialized AI agents can collaborate to solve a real-world travel-planning problem, demonstrating the capabilities of graph-based multi-agent orchestration.

---

## 2. Problem Statement

Build a **Dubai-focused AI Travel Planning System** that converts a natural-language travel request into a personalized, budget-conscious, and practical travel itinerary.

Instead of relying on a single AI agent, the system will divide the planning process among specialized agents responsible for destination research, logistics, budgeting, and quality validation.

The application will use **LangGraph** to demonstrate:

- **Graph-based orchestration:** Modeling specialized agents as nodes in a stateful workflow graph.
- **Shared state & parallel execution:** Running independent agent tasks concurrently and merging findings into a unified state.
- **Conditional decisions & feedback loops:** Detecting constraint violations and triggering automated itinerary revisions.

> **Goal:** The goal is not to build a commercial travel-booking platform, but to demonstrate how multi-agent architectures solve problems through coordinated decision-making.

---

## 3. Example User Request

> *"Plan a 4-day trip to Dubai with a budget of AED 5,000, excluding flights. I enjoy cultural attractions, local food, and beaches. I prefer affordable hotels and want to avoid crowded places."*

The system should generate:

- A personalized day-by-day itinerary with suggested timings.
- Recommended attractions, restaurants, and accommodation areas.
- Transportation options and estimated travel durations.
- A category-wise budget breakdown in **AED**.
- Alternatives when the proposed itinerary exceeds the budget.
- A final itinerary validated against the user's preferences and constraints.

---

## 4. Multi-Agent System Design

### Agent 1 — Orchestrator Agent
Understands the travel request, extracts constraints, coordinates specialized agents, manages dependencies, and assembles the final itinerary.

### Agent 2 — Destination Research Agent
Researches Dubai attractions, restaurants, cultural experiences, and neighborhoods. Recommends activities based on user interests, location, and preferences.

### Agent 3 — Logistics Agent
Suggests accommodation areas, calculates distances and travel times, identifies transportation options, and organizes activities into a practical geographical sequence.

### Agent 4 — Budget Agent
Estimates accommodation, food, transportation, and activity costs. Calculates the total projected expenditure and recommends alternatives when costs exceed the budget.

### Agent 5 — Review Agent
Evaluates the complete itinerary for budget compliance, schedule feasibility, preference alignment, duplicate activities, and missing information. Requests revisions when necessary.

---

## 5. Dubai Travel Data — Sources and Integration

The system will combine publicly available Dubai information, external APIs, and a small curated dataset to generate recommendations.

| Data Requirement | Proposed Source | Usage |
| :--- | :--- | :--- |
| Attractions and tourism information | Visit Dubai and Data.Dubai / Dubai Pulse | Research attractions, neighborhoods, and cultural experiences |
| Restaurants and location details | Google Places API or OpenStreetMap / Overpass | Discover places, categories, geographical coordinates, and available place details |
| Routes and transportation | Google Routes API and official Dubai RTA information | Estimate distances, journey durations, transit options, and public transport fares |
| Accommodation | Curated hotel dataset; optionally Amadeus Hotel APIs | Suggest hotel areas and estimate accommodation expenses |
| Activity and food costs | Published attraction prices and curated AED price ranges | Calculate estimated trip expenditure |
| Weather (optional) | Open-Meteo API | Recommend weather-appropriate activities for supported travel dates |

### Data Integration Approach

- Begin with a small, curated Dubai dataset stored in **JSON** or **SQLite** for reliable demonstrations.
- Integrate **Google Places** and **Google Routes** APIs for location discovery and travel-time estimates where API access is configured.
- Use official **RTA** fare information and curated attraction prices for budgeting.
- Clearly distinguish verified information, API-derived results, and estimated costs.
- Provide a fallback to the local dataset when external APIs are unavailable.
- Live hotel bookings, ticket purchases, and guaranteed real-time prices are outside the initial scope.

---

## 6. Framework Implementation — LangGraph

The application will implement the multi-agent workflow using **LangGraph**:

- **Stateful Graph:** Implement the specialized agents as nodes in a typed `StateGraph` sharing a unified state.
- **Parallel Execution:** Run destination research and initial logistics scouting concurrently before merging into route sequencing and budgeting.
- **Conditional Routing & Feedback Loop:** Use a review-feedback loop that routes back for targeted revisions whenever budget, schedule, or preference constraints are violated, producing a validated structured itinerary.

---

## 7. End-to-End Workflow

```text
User Travel Request
        |
Parse Requirements
        |
        v
   Orchestrator
        |
        +-------------------+
        |                   |
        v                   v
 Destination Agent     Logistics Agent
        |                   |
        +---------+---------+
                  |
                  v
             Budget Agent
                  |
                  v
              Review Agent
                  |
           +------+------+
           |             |
         Pass          Revise
           |             |
           v             |
     Final Itinerary <---+
```

The application should also display which agent is executing, what information it produces, and how the overall plan changes during review.

---

## 8. Technology Stack

- **Development environment:** Cursor
- **Programming language:** Python
- **Multi-agent framework:** LangGraph
- **LLM:** Configurable LLM provider
- **Frontend:** Streamlit for a simple interactive demonstration
- **Data:** JSON / SQLite with optional external API integrations
- **Validation:** Pydantic schemas and rule-based budget and itinerary checks

---

## 9. MVP Scope and Success Criteria

- **Initial scope:** Dubai only, covering short trips of **1–7 days**, with budget calculations in **AED**.

### Success Criteria

The project will be considered successful when it can:

1. Accept natural-language travel requirements and produce a structured Dubai itinerary.
2. Demonstrate meaningful collaboration among specialized agents.
3. Generate realistic travel schedules and estimated budgets using available data.
4. Detect and revise at least one itinerary constraint violation.
5. Visualize agent execution, state transitions, and review-triggered itinerary revisions.

### Expected Outcome

A working, interactive multi-agent prototype that helps technical and nontechnical audiences understand agent specialization, orchestration, collaboration, shared context, and validation through a practical travel-planning use case.
