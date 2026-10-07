"""Open-Meteo API client for weather and marine data."""

import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WeatherData:
    """Weather data for a single time point, broken down by NWP model.

    Each numeric field is a dict mapping model name -> value. Models that
    didn't return a value for this slot are simply absent from the dict
    (rather than mapping to None) so callers can iterate values() directly.
    """
    time: datetime
    temperature: dict[str, float]  # model -> Celsius
    wind_speed: dict[str, float]  # model -> knots
    wind_gusts: dict[str, float]  # model -> knots
    wind_direction: dict[str, float]  # model -> degrees
    precipitation_probability: dict[str, int]  # model -> percentage
    pressure: dict[str, float]  # model -> hPa
    visibility: dict[str, float]  # model -> meters
    weather_code: dict[str, int]  # model -> WMO weather code


@dataclass
class MarineData:
    """Marine data for a single time point, broken down by wave model."""
    time: datetime
    wave_height: dict[str, float]  # model -> meters
    wave_direction: dict[str, float]  # model -> degrees
    wave_period: dict[str, float]  # model -> seconds
    swell_height: dict[str, float]  # model -> meters
    swell_direction: dict[str, float]  # model -> degrees
    swell_period: dict[str, float]  # model -> seconds


@dataclass
class ForecastData:
    """Combined weather and marine forecast."""
    latitude: float
    longitude: float
    weather: list[WeatherData]
    marine: list[MarineData]
    sunrise: Optional[datetime] = None
    sunset: Optional[datetime] = None


class OpenMeteoClient:
    """Client for Open-Meteo Weather and Marine APIs."""

    WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"
    MARINE_API_URL = "https://marine-api.open-meteo.com/v1/marine"

    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 5  # seconds
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=60.0)  # Increased timeout

    async def close(self):
        await self.client.aclose()

    async def _request_with_retry(self, url: str, params: dict) -> dict:
        """Make HTTP request with exponential backoff retry for transient errors."""
        last_exception = None
        last_response = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.get(url, params=params)

                # Check if we should retry based on status code
                if response.status_code in self.RETRYABLE_STATUS_CODES:
                    last_response = response
                    delay = self.RETRY_DELAY_BASE * (2 ** attempt)
                    logger.warning("API returned %d, retrying in %ds (attempt %d/%d)", response.status_code, delay, attempt + 1, self.MAX_RETRIES)
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response.json()

            except ValueError as e:
                # Empty/truncated body with a 200 (seen 2026-10-05) — transient, retry.
                last_exception = e
                delay = self.RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning("Invalid JSON from API (%s), retrying in %ds (attempt %d/%d)", e, delay, attempt + 1, self.MAX_RETRIES)
                await asyncio.sleep(delay)

            except httpx.TransportError as e:
                last_exception = e
                delay = self.RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning("Request failed (%s), retrying in %ds (attempt %d/%d)", type(e).__name__, delay, attempt + 1, self.MAX_RETRIES)
                await asyncio.sleep(delay)

            except httpx.HTTPStatusError as e:
                # Don't retry client errors (4xx except 429)
                if e.response.status_code < 500 and e.response.status_code != 429:
                    raise
                last_exception = e
                delay = self.RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning("Server error %d, retrying in %ds (attempt %d/%d)", e.response.status_code, delay, attempt + 1, self.MAX_RETRIES)
                await asyncio.sleep(delay)

        # All retries exhausted
        if last_exception:
            raise last_exception
        # Status code retries exhausted — raise using the last response
        if last_response:
            raise httpx.HTTPStatusError(
                f"API returned {last_response.status_code} after {self.MAX_RETRIES} retries",
                request=last_response.request,
                response=last_response,
            )

    # Map of API field name -> WeatherData attribute name. Drives both the
    # `hourly` request param and per-model field parsing below.
    _WEATHER_FIELDS = {
        "temperature_2m": "temperature",
        "wind_speed_10m": "wind_speed",
        "wind_gusts_10m": "wind_gusts",
        "wind_direction_10m": "wind_direction",
        "precipitation_probability": "precipitation_probability",
        "surface_pressure": "pressure",
        "visibility": "visibility",
        "weather_code": "weather_code",
    }

    _MARINE_FIELDS = {
        "wave_height": "wave_height",
        "wave_direction": "wave_direction",
        "wave_period": "wave_period",
        "swell_wave_height": "swell_height",
        "swell_wave_direction": "swell_direction",
        "swell_wave_period": "swell_period",
    }

    async def get_weather(
        self,
        latitude: float,
        longitude: float,
        models: list[str],
        hours: int = 36,
    ) -> list[WeatherData]:
        """Fetch multi-model weather forecast from Open-Meteo.

        Models that don't cover the queried lat/lon return null and are
        silently dropped per-slot — no region detection needed.
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": list(self._WEATHER_FIELDS.keys()),
            "forecast_hours": hours,
            "timezone": "UTC",
            "wind_speed_unit": "kn",
            "models": ",".join(models),
        }

        data = await self._request_with_retry(self.WEATHER_API_URL, params)
        return self._parse_per_model(
            data, models, self._WEATHER_FIELDS, WeatherData
        )

    async def get_marine(
        self,
        latitude: float,
        longitude: float,
        models: list[str],
        hours: int = 36,
    ) -> list[MarineData]:
        """Fetch multi-model marine forecast from Open-Meteo Marine API."""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": list(self._MARINE_FIELDS.keys()),
            "forecast_hours": hours,
            "timezone": "UTC",
            "models": ",".join(models),
        }

        data = await self._request_with_retry(self.MARINE_API_URL, params)
        return self._parse_per_model(
            data, models, self._MARINE_FIELDS, MarineData
        )

    @staticmethod
    def _parse_per_model(data: dict, models: list[str], fields: dict, cls):
        """Parse a multi-model Open-Meteo response into per-slot dataclasses.

        Open-Meteo suffixes each requested field with `_<model>` when models
        are listed explicitly. We invert that into per-slot dicts of
        {model: value}, omitting models that returned null for the slot.
        """
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])

        records = []
        for i, time_str in enumerate(times):
            attrs = {attr: {} for attr in fields.values()}
            for model in models:
                for api_field, attr in fields.items():
                    arr = hourly.get(f"{api_field}_{model}")
                    if not arr or i >= len(arr):
                        continue
                    value = arr[i]
                    if value is None:
                        continue
                    attrs[attr][model] = value
            records.append(cls(
                time=datetime.fromisoformat(time_str),
                **attrs,
            ))
        return records

    async def get_forecast(
        self,
        latitude: float,
        longitude: float,
        weather_models: list[str],
        marine_models: list[str],
        hours: int = 36,
    ) -> ForecastData:
        """Get combined multi-model weather and marine forecast."""
        weather = await self.get_weather(latitude, longitude, weather_models, hours)
        marine = await self.get_marine(latitude, longitude, marine_models, hours)
        sunrise, sunset = await self._get_today_sun_times(latitude, longitude)

        return ForecastData(
            latitude=latitude,
            longitude=longitude,
            weather=weather,
            marine=marine,
            sunrise=sunrise,
            sunset=sunset,
        )

    async def _get_today_sun_times(
        self, latitude: float, longitude: float,
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        """Fetch today's sunrise/sunset in vessel-local time.

        Uses timezone=auto so Open-Meteo returns local clock-time strings
        based on the queried lat/lon — the right thing for a sailor
        anywhere in the world. Best-effort: returns (None, None) on error.
        """
        try:
            params = {
                "latitude": latitude, "longitude": longitude,
                "daily": ["sunrise", "sunset"],
                "timezone": "auto",
                "forecast_days": 1,
            }
            data = await self._request_with_retry(self.WEATHER_API_URL, params)
            daily = data.get("daily", {})
            sr = daily.get("sunrise", [None])[0]
            ss = daily.get("sunset", [None])[0]
            return (
                datetime.fromisoformat(sr) if sr else None,
                datetime.fromisoformat(ss) if ss else None,
            )
        except Exception as e:
            logger.warning("Sunrise/sunset fetch failed: %s", e)
            return None, None


# Module-level test
if __name__ == "__main__":
    import asyncio

    async def test():
        client = OpenMeteoClient()
        try:
            forecast = await client.get_forecast(
                36.7264, -2.8755,
                weather_models=["ecmwf_ifs025", "icon_eu", "gfs_global"],
                marine_models=["ecmwf_wam025", "gwam"],
                hours=12,
            )
            print(f"Weather data points: {len(forecast.weather)}")
            print(f"Marine data points: {len(forecast.marine)}")
            if forecast.weather:
                w = forecast.weather[0]
                print(f"First weather: temp {w.temperature}, wind {w.wind_speed}")
            if forecast.marine:
                m = forecast.marine[0]
                print(f"First marine: waves {m.wave_height}, swell {m.swell_height}")
        finally:
            await client.close()

    asyncio.run(test())
