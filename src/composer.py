"""Email composer - builds HTML forecast emails from data."""

from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass
from jinja2 import Environment, FileSystemLoader, select_autoescape
import os

from .weather import ForecastData, WeatherData, MarineData
from .utils import (
    degrees_to_compass,
    format_wind_range,
    format_height_range,
    format_swell_range,
    format_visibility,
    confidence_from_spread,
)

# Per-metric absolute spread thresholds (chosen for marine relevance, not %).
# HIGH if model max-spread <= first value, MED if <= second, else LOW.
_WIND_KN_HIGH = 5.0
_WIND_KN_MED = 10.0
_WAVE_M_HIGH = 0.3
_WAVE_M_MED = 0.6
_SWELL_M_HIGH = 0.3
_SWELL_M_MED = 0.6
_PRESSURE_HPA_HIGH = 3.0
_PRESSURE_HPA_MED = 6.0
_TEMP_C_HIGH = 2.0
_TEMP_C_MED = 4.0


def _flatten(records, attr):
    """Flatten per-model dicts across hours into a single list of values."""
    return [v for r in records for v in getattr(r, attr).values()]


def _per_model_max(records, attr) -> dict[str, float]:
    """For each model that appears in any record, return its max across the block."""
    by_model: dict[str, list[float]] = {}
    for r in records:
        for model, v in getattr(r, attr).items():
            by_model.setdefault(model, []).append(v)
    return {m: max(vs) for m, vs in by_model.items() if vs}


def _median(values):
    return sorted(values)[len(values) // 2]


@dataclass
class TimeBlock:
    """Aggregated data for a time block (e.g., 6 hours)."""
    label: str  # e.g., "Thu Morning 06-12"
    start_time: datetime
    end_time: datetime

    # Wind (ranges)
    wind_min_kn: float
    wind_max_kn: float
    wind_gust_max_kn: float
    wind_direction: str  # Compass direction

    # Waves (range across hours x models)
    wave_height_min: float
    wave_height_max: float
    wave_direction: str

    # Swell (range across hours x models)
    swell_height_min: float
    swell_height_max: float
    swell_direction: str
    swell_period: float

    # Conditions
    pressure: float
    visibility: str
    rain_probability: int
    temperature: float

    # Alerts
    wind_alert: bool = False

    # Confidence (per metric, derived from cross-model agreement)
    wind_confidence: str = "HIGH"
    wave_confidence: str = "HIGH"
    swell_confidence: str = "HIGH"
    pressure_confidence: str = "HIGH"
    temperature_confidence: str = "HIGH"

    # False if no models returned data for this block — render dashes instead
    # of the fake fallback numbers that the dataclass defaults imply.
    has_data: bool = True


@dataclass
class ForecastEmail:
    """All data needed to render a forecast email."""
    subject: str
    location_name: str
    latitude: float
    longitude: float
    generated_at: datetime

    # Summary
    summary_wind: str
    summary_wind_confidence: str
    summary_swell: str
    summary_swell_confidence: str
    summary_waves: str
    summary_waves_confidence: str
    summary_pressure: str
    summary_visibility: str
    summary_conditions: str

    # Confidence for the conditions row (pressure/temperature). Visibility
    # has no badge — its summary value is already a pessimistic floor across
    # models, and absolute spread thresholds don't fit (clear vs fog).
    summary_pressure_confidence: str = "HIGH"
    summary_conditions_confidence: str = "HIGH"

    # Per-model breakdowns (only populated when confidence < HIGH)
    summary_wind_breakdown: Optional[dict[str, float]] = None
    summary_waves_breakdown: Optional[dict[str, float]] = None
    summary_swell_breakdown: Optional[dict[str, float]] = None

    # Always-on transparency tables for the next-6h block: every surviving
    # model with its max wind/gust (weather) or max wave/swell (marine).
    # Lets the reader cross-check against another forecast source and verify
    # the email isn't fabricating numbers.
    weather_model_table: Optional[list[tuple[str, Optional[float], Optional[float]]]] = None
    marine_model_table: Optional[list[tuple[str, Optional[float], Optional[float]]]] = None

    # True if any summary metric is LOW — drives the warning banner
    low_confidence_warning: bool = False

    # When true, the email shows a "TEST" banner and the subject is prefixed
    # so the recipient knows this is a manual preview, not a scheduled send.
    test_mode: bool = False

    # Time blocks
    time_blocks: list[TimeBlock] = None

    # Position source info
    position_source: str = "unknown"  # "ais_live", "ais_stale", "default"
    position_updated_at: Optional[datetime] = None

    # Sunrise/sunset
    sunrise: Optional[str] = None
    sunset: Optional[str] = None

    # Synoptic charts (base64 encoded)
    chart_na_b64: Optional[str] = None
    chart_eu_b64: Optional[str] = None
    chart_na_fetched_at: Optional[datetime] = None
    chart_eu_fetched_at: Optional[datetime] = None

    # Destination (for passage mode)
    destination_name: Optional[str] = None
    destination_blocks: Optional[list[TimeBlock]] = None


class ForecastComposer:
    """Composes forecast emails from raw data."""

    def __init__(self, template_dir: str = "templates"):
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def _aggregate_time_block(
        self,
        weather: list[WeatherData],
        marine: list[MarineData],
        start_time: datetime,
        hours: int,
        wind_threshold_kn: float,
    ) -> TimeBlock:
        """Aggregate hourly multi-model data into a single time block.

        Pessimistic for safety-relevant metrics (wind/gust/wave/swell max,
        visibility floor, rain prob ceiling). Median for direction, mean for
        temperature/pressure. Confidence is the cross-model spread of each
        model's own block-max for the metric.
        """
        end_time = start_time + timedelta(hours=hours)

        block_weather = [w for w in weather if start_time <= w.time < end_time]
        block_marine = [m for m in marine if start_time <= m.time < end_time]

        if not block_weather:
            # No model data at all — flag has_data=False so the template
            # renders dashes rather than the placeholder numbers.
            return TimeBlock(
                label=self._format_block_label(start_time, end_time),
                start_time=start_time, end_time=end_time,
                wind_min_kn=0, wind_max_kn=0, wind_gust_max_kn=0, wind_direction="-",
                wave_height_min=0, wave_height_max=0, wave_direction="-",
                swell_height_min=0, swell_height_max=0, swell_direction="-",
                swell_period=0,
                pressure=0, visibility="-", rain_probability=0, temperature=0,
                wind_confidence="LOW", wave_confidence="LOW", swell_confidence="LOW",
                pressure_confidence="LOW", temperature_confidence="LOW",
                has_data=False,
            )

        # Pessimistic union over (hours x models) for ranges and ceilings.
        all_winds = _flatten(block_weather, "wind_speed")
        all_gusts = _flatten(block_weather, "wind_gusts")
        all_dirs = _flatten(block_weather, "wind_direction")

        wind_min = min(all_winds) if all_winds else 0
        wind_max = max(all_winds) if all_winds else 0
        wind_gust_max = max(all_gusts) if all_gusts else 0
        wind_dir_compass = degrees_to_compass(_median(all_dirs)) if all_dirs else "N"

        wind_confidence = confidence_from_spread(
            list(_per_model_max(block_weather, "wind_speed").values()),
            high_max=_WIND_KN_HIGH, med_max=_WIND_KN_MED,
        )

        # Marine: same pattern, fall through to zeros if marine missing
        all_wave_heights = _flatten(block_marine, "wave_height")
        all_wave_dirs = _flatten(block_marine, "wave_direction")
        all_swell_heights = _flatten(block_marine, "swell_height")
        all_swell_dirs = _flatten(block_marine, "swell_direction")
        all_swell_periods = _flatten(block_marine, "swell_period")

        wave_height_max = max(all_wave_heights) if all_wave_heights else 0
        wave_height_min = min(all_wave_heights) if all_wave_heights else 0
        wave_dir = _median(all_wave_dirs) if all_wave_dirs else 0
        swell_height_max = max(all_swell_heights) if all_swell_heights else 0
        swell_height_min = min(all_swell_heights) if all_swell_heights else 0
        swell_dir = _median(all_swell_dirs) if all_swell_dirs else 0
        swell_period = (
            sum(all_swell_periods) / len(all_swell_periods)
            if all_swell_periods else 0
        )

        wave_confidence = confidence_from_spread(
            list(_per_model_max(block_marine, "wave_height").values()),
            high_max=_WAVE_M_HIGH, med_max=_WAVE_M_MED,
        ) if block_marine else "LOW"

        swell_confidence = confidence_from_spread(
            list(_per_model_max(block_marine, "swell_height").values()),
            high_max=_SWELL_M_HIGH, med_max=_SWELL_M_MED,
        ) if block_marine else "LOW"

        # Mean for these (less safety-critical, mean is more representative)
        all_pressures = _flatten(block_weather, "pressure")
        all_temps = _flatten(block_weather, "temperature")
        all_visibilities = _flatten(block_weather, "visibility")
        all_rain_probs = _flatten(block_weather, "precipitation_probability")

        pressure = sum(all_pressures) / len(all_pressures) if all_pressures else 1013
        temperature = sum(all_temps) / len(all_temps) if all_temps else 15
        # Pessimistic floor for visibility (worst-case fog), ceiling for rain prob
        visibility = format_visibility(min(all_visibilities)) if all_visibilities else ">10km"
        rain_probability = int(max(all_rain_probs)) if all_rain_probs else 0

        pressure_confidence = confidence_from_spread(
            list(_per_model_max(block_weather, "pressure").values()),
            high_max=_PRESSURE_HPA_HIGH, med_max=_PRESSURE_HPA_MED,
        )
        temperature_confidence = confidence_from_spread(
            list(_per_model_max(block_weather, "temperature").values()),
            high_max=_TEMP_C_HIGH, med_max=_TEMP_C_MED,
        )

        wind_alert = wind_max >= wind_threshold_kn

        return TimeBlock(
            label=self._format_block_label(start_time, end_time),
            start_time=start_time, end_time=end_time,
            wind_min_kn=wind_min, wind_max_kn=wind_max,
            wind_gust_max_kn=wind_gust_max, wind_direction=wind_dir_compass,
            wave_height_min=wave_height_min, wave_height_max=wave_height_max,
            wave_direction=degrees_to_compass(wave_dir),
            swell_height_min=swell_height_min, swell_height_max=swell_height_max,
            swell_direction=degrees_to_compass(swell_dir),
            swell_period=swell_period,
            pressure=pressure, visibility=visibility,
            rain_probability=rain_probability, temperature=temperature,
            wind_alert=wind_alert,
            wind_confidence=wind_confidence,
            wave_confidence=wave_confidence,
            swell_confidence=swell_confidence,
            pressure_confidence=pressure_confidence,
            temperature_confidence=temperature_confidence,
            has_data=True,
        )

    def _format_block_label(self, start: datetime, end: datetime) -> str:
        """Format time block label like 'Thu Morning 06-12'."""
        day = start.strftime("%a")
        start_hour = start.hour
        end_hour = end.hour

        if 0 <= start_hour < 6:
            period = "Night"
        elif 6 <= start_hour < 12:
            period = "Morning"
        elif 12 <= start_hour < 18:
            period = "Afternoon"
        else:
            period = "Evening"

        return f"{day} {period} {start_hour:02d}-{end_hour:02d}"

    def compose(
        self,
        forecast: ForecastData,
        location_name: str,
        forecast_hours: int,
        wind_threshold_kn: float,
        block_hours: int = 6,
        chart_na_b64: Optional[str] = None,
        chart_eu_b64: Optional[str] = None,
        is_evening: bool = True,
        position_source: str = "unknown",
        position_updated_at: Optional[datetime] = None,
        chart_na_fetched_at: Optional[datetime] = None,
        chart_eu_fetched_at: Optional[datetime] = None,
        test_mode: bool = False,
    ) -> ForecastEmail:
        """
        Compose a forecast email from raw data.

        Args:
            forecast: Raw forecast data
            location_name: Human-readable location name
            forecast_hours: 18 or 36
            wind_threshold_kn: Wind alert threshold
            block_hours: Hours per time block (default 6)
            chart_na_b64: Base64-encoded North Atlantic synoptic chart
            chart_eu_b64: Base64-encoded Western Europe synoptic chart
            is_evening: True for evening (36hr), False for morning (18hr)
            position_source: Source of position data ("ais_live", "ais_stale", "default")
            position_updated_at: When the position was last updated (for AIS positions)

        Returns:
            ForecastEmail ready for rendering
        """
        now = datetime.utcnow()

        # Generate time blocks
        blocks = []
        current_time = now.replace(minute=0, second=0, microsecond=0)

        # Round to next block boundary
        current_hour = current_time.hour
        block_start_hour = (current_hour // block_hours) * block_hours
        if block_start_hour < current_hour:
            block_start_hour += block_hours
        current_time = current_time.replace(hour=block_start_hour % 24)
        if block_start_hour >= 24:
            current_time += timedelta(days=1)

        num_blocks = forecast_hours // block_hours
        for _ in range(num_blocks):
            block = self._aggregate_time_block(
                forecast.weather, forecast.marine,
                current_time, block_hours, wind_threshold_kn,
            )
            blocks.append(block)
            current_time += timedelta(hours=block_hours)

        # First block drives the summary section. Compute the same per-model
        # breakdowns we used for confidence so we can show them when models
        # disagree.
        first_start = blocks[0].start_time if blocks else None
        first_end = blocks[0].end_time if blocks else None
        first_weather = [
            w for w in forecast.weather
            if first_start and first_start <= w.time < first_end
        ] if first_start else []
        first_marine = [
            m for m in forecast.marine
            if first_start and first_start <= m.time < first_end
        ] if first_start else []

        first = blocks[0] if blocks else None
        if first:
            summary_wind = (
                f"{first.wind_direction} "
                f"{format_wind_range(first.wind_min_kn, first.wind_max_kn)}, "
                f"gusts to {int(first.wind_gust_max_kn)}kn"
            )
            swell_dirs_first = _flatten(first_marine, "swell_direction")
            wave_dirs_first = _flatten(first_marine, "wave_direction")
            summary_swell = format_swell_range(
                first.swell_height_min, first.swell_height_max,
                _median(swell_dirs_first) if swell_dirs_first else 0,
                first.swell_period,
            )
            summary_waves = format_height_range(
                first.wave_height_min, first.wave_height_max,
                _median(wave_dirs_first) if wave_dirs_first else None,
            )
            summary_pressure = f"{int(first.pressure)} hPa"
            summary_visibility = first.visibility
            summary_conditions = f"{int(first.temperature)}°C"

            summary_wind_confidence = first.wind_confidence
            summary_waves_confidence = first.wave_confidence
            summary_swell_confidence = first.swell_confidence
            summary_pressure_confidence = first.pressure_confidence
            summary_conditions_confidence = first.temperature_confidence

            # Per-model breakdown — only render when this metric isn't HIGH
            wind_breakdown = _per_model_max(first_weather, "wind_speed")
            waves_breakdown = _per_model_max(first_marine, "wave_height")
            swell_breakdown = _per_model_max(first_marine, "swell_height")
            summary_wind_breakdown = wind_breakdown if summary_wind_confidence != "HIGH" else None
            summary_waves_breakdown = waves_breakdown if summary_waves_confidence != "HIGH" else None
            summary_swell_breakdown = swell_breakdown if summary_swell_confidence != "HIGH" else None

            # Always-on per-model footer tables for the next-6h block.
            gust_breakdown = _per_model_max(first_weather, "wind_gusts")
            weather_models = sorted(set(wind_breakdown) | set(gust_breakdown))
            weather_model_table = [
                (m, wind_breakdown.get(m), gust_breakdown.get(m))
                for m in weather_models
            ]
            marine_models = sorted(set(waves_breakdown) | set(swell_breakdown))
            marine_model_table = [
                (m, waves_breakdown.get(m), swell_breakdown.get(m))
                for m in marine_models
            ]
        else:
            summary_wind = summary_swell = summary_waves = "N/A"
            summary_pressure = summary_visibility = summary_conditions = "N/A"
            summary_wind_confidence = summary_waves_confidence = summary_swell_confidence = "LOW"
            summary_pressure_confidence = summary_conditions_confidence = "LOW"
            summary_wind_breakdown = summary_waves_breakdown = summary_swell_breakdown = None
            weather_model_table = None
            marine_model_table = None

        low_confidence_warning = "LOW" in (
            summary_wind_confidence, summary_waves_confidence, summary_swell_confidence,
        )

        forecast_type = "Evening Forecast" if is_evening else "Morning Update"
        subject = f"Marine Weather - {location_name} - {forecast_type} ({forecast_hours}hr)"
        if test_mode:
            subject = f"[TEST — please ignore] {subject}"

        sunrise_str = forecast.sunrise.strftime("%H:%M local") if forecast.sunrise else None
        sunset_str = forecast.sunset.strftime("%H:%M local") if forecast.sunset else None

        return ForecastEmail(
            subject=subject,
            location_name=location_name,
            latitude=forecast.latitude,
            longitude=forecast.longitude,
            generated_at=now,
            summary_wind=summary_wind,
            summary_wind_confidence=summary_wind_confidence,
            summary_swell=summary_swell,
            summary_swell_confidence=summary_swell_confidence,
            summary_waves=summary_waves,
            summary_waves_confidence=summary_waves_confidence,
            summary_pressure=summary_pressure,
            summary_visibility=summary_visibility,
            summary_conditions=summary_conditions,
            summary_pressure_confidence=summary_pressure_confidence,
            summary_conditions_confidence=summary_conditions_confidence,
            summary_wind_breakdown=summary_wind_breakdown,
            weather_model_table=weather_model_table,
            marine_model_table=marine_model_table,
            summary_waves_breakdown=summary_waves_breakdown,
            summary_swell_breakdown=summary_swell_breakdown,
            low_confidence_warning=low_confidence_warning,
            test_mode=test_mode,
            time_blocks=blocks,
            position_source=position_source,
            position_updated_at=position_updated_at,
            chart_na_b64=chart_na_b64,
            chart_eu_b64=chart_eu_b64,
            chart_na_fetched_at=chart_na_fetched_at,
            chart_eu_fetched_at=chart_eu_fetched_at,
            sunrise=sunrise_str,
            sunset=sunset_str,
        )

    def render(self, email_data: ForecastEmail) -> str:
        """Render email data to HTML string."""
        template = self.env.get_template("forecast.html")
        return template.render(email=email_data)
