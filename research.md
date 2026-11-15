# Data Research — Dubai AI Travel Planner

This document catalogs all data sources, APIs, open datasets, and fallback strategies for obtaining Dubai travel data in the **Dubai AI Travel Planner** project, aligned with [problemStatement.md](file:///Users/mna/Downloads/Multi-Agent/problemStatement.md) and [architecture.md](file:///Users/mna/Downloads/Multi-Agent/architecture.md).

---

## 1. Data Sourcing Strategy

The system uses a **100% free, zero-paid-API 2-tier data model** so it can run reliably for demonstrations without any paid API keys or billing risk:

1. **Tier 1 — Curated Local Seed Dataset (`data/dubai_curated.json` / SQLite):** Pre-verified Dubai attractions, neighborhoods, local restaurants, budget/mid-range hotels, official RTA fare rules, and AED price ranges. Guarantees 100% offline reliability.
2. **Tier 2 — Free / Open Public APIs & Open Data:** No-cost, no-key (or open-data) sources including **Open-Meteo**, **OpenStreetMap Overpass API**, **OSRM (Open Source Routing Machine)**, and **Dubai Pulse / Data.Dubai** open datasets.

Every record returned to an agent is tagged with provenance:
- `verified_dataset`: Fact-checked against official sources (e.g., RTA fares, published museum tickets).
- `api_derived`: Retrieved dynamically from an external API.
- `estimated`: Computed using heuristics or curated AED price ranges (e.g., taxi fares, average meal costs).

---

## 2. Data Sources by Domain

### 2.1 Attractions, Culture & Neighborhoods

#### A. Dubai Pulse / Data.Dubai (Official Government Open Data)
- **Portals:** [dubaipulse.gov.ae](https://www.dubaipulse.gov.ae) and [data.dubai.ae](https://data.dubai.ae) (managed by Digital Dubai & Dubai Data and Statistics Establishment).
- **Relevant Publishers:**
  - **Department of Economy and Tourism (DET):** Visitor statistics, hotel establishments, city POIs, and events.
  - **Dubai Culture & Arts Authority:** Museums, heritage sites, public libraries, and cultural centers.
  - **Dubai Municipality:** Public parks, beaches, landmarks, and geospatial facilities.
- **Access Formats:** Downloadable **CSV, JSON, KML** files for offline seeding, plus REST APIs (open datasets can be previewed/downloaded immediately; consumer API keys require a free developer account).
- **Best Use in Project:** Periodic offline ingestion into `dubai_curated.json` for authoritative cultural sites, museums, and parks.

#### B. Visit Dubai (Official Tourism Portal)
- **Source:** [visitdubai.com](https://www.visitdubai.com)
- **Data Available:** Neighborhood guides (Al Fahidi Historical Neighbourhood, Deira, Bur Dubai, Jumeirah, Downtown Dubai, Dubai Marina, Alserkal Avenue), attraction descriptions, visitor tips, and crowd/family suitability.
- **Best Use in Project:** Manual curation of rich descriptions, neighborhood vibes, and `crowd_level` tags (`low`, `medium`, `high`) into the local seed dataset (avoid fragile live web scraping at runtime).

#### C. OpenStreetMap / Overpass API (Free Live Geospatial Fallback)
- **Endpoint:** `POST https://overpass-api.de/api/interpreter`
- **Auth / Cost:** Free, **no API key required** (respect fair-use rate limits and cache responses).
- **Dubai Bounding Box:** `(24.79, 54.89, 25.36, 55.56)` (`south, west, north, east`).
- **Useful OSM Tags:**
  - Cultural & attractions: `tourism=attraction`, `tourism=museum`, `tourism=viewpoint`, `historic=*`
  - Beaches & parks: `natural=beach`, `leisure=park`
- **Example Query:**
  ```overpass
  [out:json][timeout:25];
  (
    nwr["tourism"~"attraction|museum"](24.79,54.89,25.36,55.56);
    nwr["natural"="beach"](24.79,54.89,25.36,55.56);
  );
  out center;
  ```
- **Limitations:** Lacks ticket prices, crowd ratings, and rich descriptions; best paired with local AED price tables.

---

### 2.2 Restaurants & Local Food

#### A. OpenStreetMap / Overpass API — Live Open Source
- **Endpoint:** `POST https://overpass-api.de/api/interpreter`
- **Auth / Cost:** Free, **no API key required** (respect fair-use rate limits and cache responses).
- **Tags:** `amenity=restaurant`, `amenity=cafe`, `amenity=fast_food`, optionally filtered by `cuisine=*` within Dubai's bounding box `(24.79, 54.89, 25.36, 55.56)`.
- **Strengths:** Zero-cost live discovery of restaurant names, cuisines, and coordinates.

#### B. Curated Local Food Dataset — Essential for AED Accuracy
- OSM does not provide exact AED meal costs for local Emirati/South Asian/Middle Eastern eateries.
- Maintain a curated list of authentic, non-touristy Dubai dining spots (e.g., Al Ustad Special Kabab, Arabian Tea House, Ravi Restaurant, Bu Qtair, Deira/Karama street food) with neighborhood, cuisine, and realistic AED per-person meal ranges.

---

### 2.3 Routes, Distances & Public Transportation (Dubai RTA)

#### A. OSRM (Open Source Routing Machine) — Free Live Routing
- **Public Demo Endpoint:** `GET https://router.project-osrm.org/route/v1/{profile}/{lon1},{lat1};{lon2},{lat2}?overview=false` (`profile`: `driving` or `foot`).
- **Auth / Cost:** Free, **no API key required** (best-effort public server; cache all responses locally).
- **Outputs:** Road/walking distance in meters and estimated travel duration in seconds.

#### B. Official Dubai RTA Fare & Transit Rules — Deterministic Costing
Dubai's Roads and Transport Authority (RTA) uses a unified **7-zone Nol card** system across Metro, Public Buses, Tram, and Marine transport:

| Journey Distance / Zones Crossed | Silver Nol Card (Standard) | Gold Nol Card | Red Ticket (Paper - Standard) |
| :--- | :---: | :---: | :---: |
| **Within 1 Zone** | **AED 3.00** | AED 6.00 | AED 4.00 |
| **2 Adjacent Zones** | **AED 5.00** | AED 10.00 | AED 6.00 |
| **3+ Zones** | **AED 7.50** | AED 15.00 | AED 8.50 |

- **1-Day Unlimited Pass (Red Ticket):** **AED 20.00** (Regular) / **AED 40.00** (Gold) — ideal for itinerary days with 3+ transit legs.
- **Transfer Rule:** Seamless transfers between Bus, Metro, and Tram count as a single journey if transfer time is **≤ 30 minutes** (max total journey duration **180 minutes**).
- **Minimum Nol Balance:** AED 7.50 required at Metro entry gates.
- **Marine (Traditional Abra):** Creek crossings (e.g., Bur Dubai ↔ Deira) cost **AED 1–2** per trip.

#### C. Offline Routing & Fare Fallback
When OSRM is unavailable or offline:
- Compute **Haversine distance** between `(lat1, lon1)` and `(lat2, lon2)` multiplied by a road-winding factor (`1.35x`).
- Estimate duration by mode:
  - `walk`: ~4.5 km/h (used for distances < 1.2 km)
  - `metro` / `transit`: ~30 km/h + 10 min station wait/transfer buffer
  - `taxi` / `drive`: ~35–45 km/h depending on time of day
- Estimate Dubai Taxi fare using RTA meter heuristics (flagfall ~AED 5–12 depending on booking/time + ~AED 2.19/km, minimum fare AED 12), tagged as `estimated`.

---

### 2.4 Accommodation (Areas & Hotels)

#### Curated Dubai Hotel & Area Dataset — Primary Source
- Grouped by key Dubai districts with metro connectivity, crowd profile, and typical nightly AED ranges:
  - **Al Fahidi / Bur Dubai / Deira:** Cultural, walkable, affordable boutique & 3-star hotels (e.g., AED 200–450/night), direct Green/Red Line Metro access.
  - **Al Barsha:** Mid-range value near Mall of the Emirates (AED 250–500/night), Red Line Metro.
  - **Jumeirah / Umm Suqeim:** Beach access, quieter residential vibe (AED 400–850/night).
  - **Downtown / Dubai Marina:** Higher cost & higher crowd levels (flagged when user prefers affordable/quiet stays).
- Live hotel bookings and real-time room pricing are outside the MVP scope (per [problemStatement.md](file:///Users/mna/Downloads/Multi-Agent/problemStatement.md)).

---

### 2.5 Activity & Food Costs (AED)

- **Published Attraction Ticket Prices:** Stored in `data/dubai_curated.json` with a `last_verified` timestamp (e.g., Dubai Frame ~AED 50, Al Fahidi / Shindagha museums ~AED 15–50, public beaches like Al Mamzar / Kite Beach / JBR AED 0–5, Abra ride AED 1–2, Burj Khalifa At The Top ~AED 179+).
- **Curated Meal Cost Tiers (per person):**
  - Street food / local cafeteria: **AED 20–45**
  - Casual local restaurant: **AED 50–110**
  - Mid-range dining: **AED 120–240**

---

### 2.6 Weather (Optional)

#### A. Open-Meteo Forecast API — Primary Live Source
- **Endpoint:** `GET https://api.open-meteo.com/v1/forecast`
- **Auth / Cost:** Free for non-commercial use, **no API key required**.
- **Dubai Coordinates:** `latitude=25.2048&longitude=55.2708`
- **Example Request:**
  ```text
  https://api.open-meteo.com/v1/forecast?latitude=25.2048&longitude=55.2708&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&timezone=auto&forecast_days=7
  ```
- **Limitations:** Forecasts extend up to **16 days** ahead.

#### B. Seasonal Climate Fallback Table
For trips more than 16 days in the future (or offline mode), use a monthly Dubai climate lookup:
- **Nov–Mar:** Mild (20–28 °C) — ideal for all-day outdoor cultural walks, Al Fahidi, beaches, and creekside dining.
- **Apr & Oct:** Warm (28–36 °C) — schedule beaches/outdoor walking in mornings or late afternoons.
- **May–Sep:** Very hot (37–45 °C) — prioritize indoor museums, malls, and evening outdoor activities.

---

## 3. Data Source Comparison Matrix

| Domain | Source | Auth Required | Cost / Quota | Role in MVP |
| :--- | :--- | :---: | :--- | :--- |
| **Attractions & Culture** | Curated JSON (from Visit Dubai & Dubai Pulse) | No | Free | **Primary** |
| **Attractions & Culture** | Dubai Pulse / Data.Dubai Open Datasets | Optional (for API) | Free | **Seed / Enrichment** |
| **POIs & Restaurants** | OpenStreetMap Overpass API | No | Free (fair-use limits) | **Live Open Fallback** |
| **Routes & Distances** | OSRM Public API (`router.project-osrm.org`) | No | Free (fair-use limits) | **Live Open Routing** |
| **Transit Fares** | Official Dubai RTA Nol Zone Table | No | Free (static rules) | **Primary** |
| **Routes Fallback** | Haversine + Speed/Taxi Heuristics | No | Free | **Offline Fallback** |
| **Accommodation** | Curated Dubai Hotel & Area Dataset | No | Free | **Primary** |
| **Weather** | Open-Meteo Forecast API | No | Free (non-commercial) | **Primary (≤16 days)** |
| **Weather** | Monthly Dubai Climate Table | No | Free | **Fallback (>16 days / offline)** |

---

## 4. Recommended Local Dataset Structure (`data/dubai_curated.json`)

To make LangGraph agent runs deterministic, fast, and verifiable, structure the seed JSON into five clean collections:

1. `neighborhoods`: `id`, `name`, `vibe`, `crowd_level`, `metro_lines`, `best_for`
2. `attractions`: `id`, `name`, `neighborhood_id`, `category`, `lat`, `lon`, `ticket_aed`, `avg_duration_mins`, `crowd_level`, `opening_hours`, `indoor_outdoor`, `source_url`, `last_verified`
3. `restaurants`: `id`, `name`, `neighborhood_id`, `cuisine`, `is_local_specialty`, `avg_meal_aed`, `lat`, `lon`, `crowd_level`
4. `hotels`: `id`, `name`, `neighborhood_id`, `tier`, `nightly_aed_min`, `nightly_aed_max`, `metro_walk_mins`, `crowd_level`
5. `rta_fares` & `seasonal_weather`: Official Nol zone rates, 1-day pass cost, taxi base/per-km rates, and monthly temperature bands.

---

## 5. Key Risks & Mitigations

- **Public API Rate Limits / Downtime:** Overpass API and OSRM are free shared services; cache all responses in `data/cache.db` (SQLite) and fall back seamlessly to `dubai_curated.json` and Haversine heuristics on timeout or HTTP 429.
- **Incomplete OSM Metadata:** OpenStreetMap entries may lack opening hours or AED pricing; enrich and validate against `dubai_curated.json` before passing candidates to the Budget Agent.
- **Stale Ticket Prices:** Store `last_verified` on every curated attraction price and surface a clear disclaimer in the UI that prices are estimates.
