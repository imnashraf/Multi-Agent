"""Dubai weather client (Open-Meteo <=16 days + SQLite cache + seasonal fallback)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from dubai_planner.data.cache import SQLiteCache
from dubai_planner.data.curated import CuratedDataset
from dubai_planner.models.schemas import Provenance, TravelDates

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DUBAI_LAT = 25.2048
DUBAI_LON = 55.2708


class WeatherSummary(BaseModel):
    """Weather forecast or seasonal climate advisory for itinerary scheduling."""

    temp_min_c: float
    temp_max_c: float
    precipitation_mm: float = Field(default=0.0, ge=0.0)
    season_band: Literal["mild", "warm", "hot"]
    advisory: str
    is_hot_season: bool
    forecast_days_covered: int = Field(default=0, ge=0)
    provenance: Provenance


def _classify_band(temp_max_c: float) -> Literal["mild", "warm", "hot"]:
    if temp_max_c >= 36.0:
        return "hot"
    if temp_max_c >= 29.0:
        return "warm"
    return "mild"


def _advisory_for_band(band: Literal["mild", "warm", "hot"], t_min: float, t_max: float) -> str:
    if band == "hot":
        return (
            f"Very hot ({t_min:.0f}-{t_max:.0f}°C) — prioritize indoor museums/galleries "
            "midday and schedule outdoor walks or beaches near sunset."
        )
    if band == "warm":
        return (
            f"Warm ({t_min:.0f}-{t_max:.0f}°C) — schedule beaches and outdoor heritage walks "
            "in the morning or late afternoon."
        )
    return (
        f"Mild ({t_min:.0f}-{t_max:.0f}°C) — ideal for all-day outdoor cultural walks, "
        "creek crossings, and beaches."
    )


class WeatherClient:
    """Retrieves Dubai weather via Open-Meteo (<=16 days) or curated seasonal climate bands."""

    def __init__(
        self,
        dataset: CuratedDataset,
        cache: SQLiteCache | None = None,
        *,
        offline_mode: bool = False,
        http_client: httpx.Client | None = None,
        open_meteo_url: str = OPEN_METEO_URL,
        cache_ttl_seconds: int = 21600,
    ) -> None:
        self.dataset = dataset
        self.cache = cache
        self.offline_mode = offline_mode
        self._http_client = http_client
        self.open_meteo_url = open_meteo_url
        self.cache_ttl_seconds = cache_ttl_seconds

    def _fetch_open_meteo(
        self, start_date: date, end_date: date
    ) -> tuple[dict[str, Any] | None, datetime]:
        cache_key = f"open_meteo:{start_date.isoformat()}:{end_date.isoformat()}"
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return dict(cached.payload), cached.retrieved_at

        if self.offline_mode:
            return None, datetime.now(timezone.utc)

        params = {
            "latitude": DUBAI_LAT,
            "longitude": DUBAI_LON,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": "auto",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        try:
            if self._http_client is not None:
                resp = self._http_client.get(self.open_meteo_url, params=params, timeout=10.0)
            else:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(self.open_meteo_url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            if "daily" not in payload:
                return None, datetime.now(timezone.utc)
        except (httpx.HTTPError, ValueError):
            return None, datetime.now(timezone.utc)

        retrieved_at = datetime.now(timezone.utc)
        if self.cache is not None:
            retrieved_at = self.cache.set(
                cache_key,
                namespace="open_meteo",
                payload=payload,
                source_name="open_meteo",
                ttl_seconds=self.cache_ttl_seconds,
            )
        return payload, retrieved_at

    def get_weather(
        self,
        travel_dates: TravelDates | None = None,
        *,
        reference_date: date | None = None,
    ) -> WeatherSummary:
        """Return Open-Meteo forecast for trips <=16 days ahead, else seasonal fallback."""
        today = reference_date or date.today()

        if travel_dates is not None:
            days_until_start = (travel_dates.start_date - today).days
            horizon_limit = today + timedelta(days=15)
            if 0 <= days_until_start <= 15:
                query_end = min(travel_dates.end_date, horizon_limit)
                data, retrieved_at = self._fetch_open_meteo(travel_dates.start_date, query_end)
                if data is not None:
                    daily = data.get("daily", {})
                    max_temps = [float(x) for x in daily.get("temperature_2m_max", []) if x is not None]
                    min_temps = [float(x) for x in daily.get("temperature_2m_min", []) if x is not None]
                    precip = [float(x) for x in daily.get("precipitation_sum", []) if x is not None]
                    if max_temps and min_temps:
                        avg_min = round(sum(min_temps) / len(min_temps), 1)
                        avg_max = round(sum(max_temps) / len(max_temps), 1)
                        total_precip = round(sum(precip), 1)
                        band = _classify_band(avg_max)
                        return WeatherSummary(
                            temp_min_c=avg_min,
                            temp_max_c=avg_max,
                            precipitation_mm=total_precip,
                            season_band=band,
                            advisory=_advisory_for_band(band, avg_min, avg_max),
                            is_hot_season=(band == "hot"),
                            forecast_days_covered=len(max_temps),
                            provenance=Provenance(
                                source_type="api_derived",
                                source_name="open_meteo",
                                retrieved_at=retrieved_at,
                            ),
                        )

        target_month = travel_dates.start_date.month if travel_dates is not None else today.month
        monthly = self.dataset.seasonal_weather.get(str(target_month), {})
        t_min = float(monthly.get("temp_min_c", 20.0))
        t_max = float(monthly.get("temp_max_c", 30.0))
        band_raw = monthly.get("band", _classify_band(t_max))
        band: Literal["mild", "warm", "hot"] = (
            band_raw if band_raw in {"mild", "warm", "hot"} else _classify_band(t_max)
        )
        return WeatherSummary(
            temp_min_c=t_min,
            temp_max_c=t_max,
            precipitation_mm=0.0,
            season_band=band,
            advisory=monthly.get("advisory", _advisory_for_band(band, t_min, t_max)),
            is_hot_season=(band == "hot"),
            forecast_days_covered=0,
            provenance=Provenance(
                source_type="verified_dataset",
                source_name="dubai_curated.json",
                last_verified=self.dataset.last_verified,
            ),
        )
