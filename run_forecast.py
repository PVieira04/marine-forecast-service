"""
Marine Weather Agent — GitHub Actions entry point.

Replaces the FastAPI server + APScheduler + persistent AIS WebSocket
with a single script that runs once per invocation.

Usage:
    python run_forecast.py --morning        # 18hr forecast + AIS lookup + email
    python run_forecast.py --evening        # 36hr forecast + AIS lookup + email
    python run_forecast.py --position-only  # AIS lookup only, no forecast/email
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import websockets
import yaml

from src.logging_config import setup_logging
from src.weather import OpenMeteoClient
from src.charts import DWDChartFetcher
from src.composer import ForecastComposer
from src.sender import EmailSender
from src.send_log import log_send

logger = logging.getLogger(__name__)

# Paths relative to repo root
REPO_ROOT = Path(__file__).parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
POSITION_CACHE_PATH = REPO_ROOT / "position_cache.json"

# AIS WebSocket
AIS_WEBSOCKET_URL = "wss://stream.aisstream.io/v0/stream"
# seconds; forecast runs keep this short, update-position.yml sets a longer wait
AIS_LOOKUP_TIMEOUT = int(os.environ.get("AIS_LOOKUP_TIMEOUT", "45"))
# Class A (types 1-3) plus Class B — most yachts transmit Class B.
AIS_POSITION_MESSAGE_TYPES = [
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
]


def load_config() -> dict:
    """Load and validate config.yaml."""
    if not CONFIG_PATH.exists():
        logger.error("Config file not found: %s", CONFIG_PATH)
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    # Validate required fields
    required = [
        ("vessel", "mmsi"),
        ("recipient_email",),
        ("default_position", "latitude"),
        ("default_position", "longitude"),
    ]
    for keys in required:
        obj = config
        for key in keys:
            if not isinstance(obj, dict) or key not in obj:
                logger.error("Missing required config: %s", ".".join(keys))
                sys.exit(1)
            obj = obj[key]

    return config


def load_position_cache() -> dict:
    """Load position_cache.json, returning defaults if missing/invalid."""
    if not POSITION_CACHE_PATH.exists():
        return {
            "mmsi": None,
            "latitude": None,
            "longitude": None,
            "location_name": None,
            "updated_at": None,
        }

    try:
        with open(POSITION_CACHE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read position cache: %s", e)
        return {
            "mmsi": None,
            "latitude": None,
            "longitude": None,
            "location_name": None,
            "updated_at": None,
        }


def save_position_cache(cache: dict):
    """Write position_cache.json."""
    with open(POSITION_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2, default=str)
        f.write("\n")


async def attempt_ais_lookup(mmsi: str, api_key: str) -> Optional[dict]:
    """
    Brief WebSocket connection to aisstream.io to get vessel position.

    Connects, subscribes to the MMSI, waits up to AIS_LOOKUP_TIMEOUT seconds
    for a Class A or Class B position report, then disconnects.

    Returns:
        Dict with latitude, longitude, timestamp or None if no position found.
    """
    if not api_key:
        logger.warning("No AISSTREAM_API_KEY set, skipping AIS lookup")
        return None

    subscription = {
        "APIKey": api_key,
        "BoundingBoxes": [[[-90, -180], [90, 180]]],
        "FiltersShipMMSI": [mmsi],
        "FilterMessageTypes": AIS_POSITION_MESSAGE_TYPES,
    }

    try:
        async with asyncio.timeout(AIS_LOOKUP_TIMEOUT):
            async with websockets.connect(
                AIS_WEBSOCKET_URL, ping_interval=20, ping_timeout=20
            ) as ws:
                logger.info("Connected to aisstream.io, looking for MMSI %s...", mmsi)
                await ws.send(json.dumps(subscription))

                async for message in ws:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue

                    if "error" in data:
                        logger.warning("aisstream.io error: %s", data["error"])
                        return None

                    message_type = data.get("MessageType")
                    if message_type not in AIS_POSITION_MESSAGE_TYPES:
                        continue

                    meta = data.get("MetaData", {})
                    report = data.get("Message", {}).get(message_type, {})

                    if str(meta.get("MMSI")) != mmsi:
                        continue

                    lat = report.get("Latitude")
                    lon = report.get("Longitude")

                    # Validate position
                    if lat is None or lon is None:
                        continue
                    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                        logger.warning("AIS position out of range: %s, %s", lat, lon)
                        continue
                    if lat == 0.0 and lon == 0.0:
                        continue

                    logger.info("AIS position received (%s): %s, %s", message_type, lat, lon)
                    return {
                        "latitude": lat,
                        "longitude": lon,
                        "timestamp": datetime.utcnow().isoformat(),
                    }

    except TimeoutError:
        logger.info("AIS lookup timed out after %ds (vessel may not be transmitting)", AIS_LOOKUP_TIMEOUT)
    except Exception as e:
        logger.warning("AIS lookup failed: %s", e)

    return None


def resolve_position(
    config: dict, position_cache: dict, ais_result: Optional[dict]
) -> tuple[float, float, str, str, Optional[datetime]]:
    """
    Determine the best available position.

    Priority: fresh AIS > cached position (with staleness check) > default.

    Returns:
        (latitude, longitude, location_name, position_source, position_updated_at)
    """
    stale_hours = config.get("ais_stale_threshold_hours", 24)

    # 1. Fresh AIS result
    if ais_result:
        lat = ais_result["latitude"]
        lon = ais_result["longitude"]
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        location_name = f"{abs(lat):.4f}\u00b0{lat_dir}, {abs(lon):.4f}\u00b0{lon_dir}"
        updated_at = datetime.fromisoformat(ais_result["timestamp"])
        return lat, lon, location_name, "ais_live", updated_at

    # 2. Cached position
    if position_cache.get("latitude") is not None and position_cache.get("longitude") is not None:
        lat = position_cache["latitude"]
        lon = position_cache["longitude"]
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        location_name = position_cache.get("location_name") or f"{abs(lat):.4f}\u00b0{lat_dir}, {abs(lon):.4f}\u00b0{lon_dir}"

        updated_at = None
        position_source = "ais_live"

        if position_cache.get("updated_at"):
            try:
                updated_at = datetime.fromisoformat(position_cache["updated_at"])
                age_hours = (datetime.utcnow() - updated_at).total_seconds() / 3600
                if age_hours > stale_hours:
                    position_source = "ais_stale"
                    location_name += f" (position from {int(age_hours)}h ago)"
            except (ValueError, TypeError):
                position_source = "ais_stale"

        return lat, lon, location_name, position_source, updated_at

    # 3. Default position from config
    default = config["default_position"]
    return (
        default["latitude"],
        default["longitude"],
        default.get("location_name", "Default Position"),
        "default",
        None,
    )


async def notify_failure(sender: EmailSender, recipient: str, alert_email: Optional[str], error: str) -> None:
    """Short "delayed" notice to the recipient; technical details to the maintainer."""
    try:
        await sender.send_delay_notice(recipient)
        if alert_email:
            await sender.send_failure_notification(alert_email, error)
    except Exception as notify_error:
        logger.error("Could not send failure notification: %s", notify_error)


async def run_forecast(is_evening: bool, config: dict, position_cache: dict, ais_result: Optional[dict]) -> bool:
    """Run the full forecast pipeline: weather + charts + compose + send. Returns True on success."""
    lat, lon, location_name, position_source, position_updated_at = resolve_position(
        config, position_cache, ais_result
    )
    logger.info(
        "Position: %s (%s) at %.4f, %.4f",
        location_name, position_source, lat, lon,
    )

    forecast_cfg = config.get("forecast", {})
    forecast_hours = forecast_cfg.get("evening_hours", 36) if is_evening else forecast_cfg.get("morning_hours", 18)
    wind_threshold_kn = forecast_cfg.get("wind_threshold_kn", 20)
    weather_models = forecast_cfg.get("weather_models", ["ecmwf_ifs025", "icon_eu", "gfs_global"])
    marine_models = forecast_cfg.get("marine_models", ["ecmwf_wam025", "gwam"])

    weather_client = OpenMeteoClient()
    chart_fetcher = DWDChartFetcher()
    composer = ForecastComposer(template_dir=str(REPO_ROOT / "templates"))
    sender = EmailSender()
    test_mode = os.environ.get("TEST_MODE", "").lower() == "true"
    recipient_override = os.environ.get("RECIPIENT_OVERRIDE", "").strip()
    if recipient_override:
        recipient = recipient_override
        bcc = None
        alert_email = recipient_override
        logger.info("RECIPIENT_OVERRIDE set — redirecting to %s (bcc suppressed)", recipient)
    else:
        recipient = config["recipient_email"]
        bcc = config.get("bcc_email") or None
        alert_email = config.get("alert_email") or bcc
    send_log_url = config.get("send_log_url")
    log_common = dict(
        slot="evening" if is_evening else "morning",
        # Redirected sends never reached the recipient, so the watchdog must not count them.
        test_mode=test_mode or bool(recipient_override),
        location_name=location_name,
        latitude=lat,
        longitude=lon,
        position_source=position_source,
        forecast_hours=forecast_hours,
    )

    try:
        # Fetch weather data
        logger.info("Fetching %dhr forecast for %.4f, %.4f...", forecast_hours, lat, lon)
        forecast = await weather_client.get_forecast(
            lat, lon, weather_models, marine_models, forecast_hours,
        )

        # Fetch charts
        chart_cfg = config.get("charts", {})
        chart_na_b64 = None
        chart_eu_b64 = None
        chart_na_fetched_at = None
        chart_eu_fetched_at = None

        if chart_cfg.get("include_na", True):
            chart_na_b64 = await chart_fetcher.get_chart_base64("na")
            chart_na_fetched_at = datetime.utcnow() if chart_na_b64 else None

        if chart_cfg.get("include_eu", True):
            chart_eu_b64 = await chart_fetcher.get_chart_base64("eu")
            chart_eu_fetched_at = datetime.utcnow() if chart_eu_b64 else None

        def chart_status(enabled: bool, b64: Optional[str]) -> str:
            if not enabled:
                return "disabled in config"
            return f"{len(b64)} b64 chars" if b64 else "FAILED (see error above)"

        logger.info(
            "Charts - NA: %s, EU: %s",
            chart_status(chart_cfg.get("include_na", True), chart_na_b64),
            chart_status(chart_cfg.get("include_eu", True), chart_eu_b64),
        )

        # Compose email
        email_data = composer.compose(
            forecast=forecast,
            location_name=location_name,
            forecast_hours=forecast_hours,
            wind_threshold_kn=wind_threshold_kn,
            chart_na_b64=chart_na_b64,
            chart_eu_b64=chart_eu_b64,
            is_evening=is_evening,
            position_source=position_source,
            position_updated_at=position_updated_at,
            chart_na_fetched_at=chart_na_fetched_at,
            chart_eu_fetched_at=chart_eu_fetched_at,
            test_mode=test_mode,
        )

        # Destination forecast (if configured)
        destination = config.get("destination")
        if destination and destination.get("name") and destination.get("latitude") and destination.get("longitude"):
            try:
                dest_forecast = await weather_client.get_forecast(
                    float(destination["latitude"]),
                    float(destination["longitude"]),
                    weather_models,
                    marine_models,
                    forecast_hours,
                )
                # Aggregate destination forecast into time blocks
                dest_blocks = []
                now = datetime.utcnow()
                current_time = now.replace(minute=0, second=0, microsecond=0)
                block_hours = 6
                current_hour = current_time.hour
                block_start_hour = (current_hour // block_hours) * block_hours
                if block_start_hour < current_hour:
                    block_start_hour += block_hours
                current_time = current_time.replace(hour=block_start_hour % 24)
                if block_start_hour >= 24:
                    current_time += timedelta(days=1)

                num_blocks = forecast_hours // block_hours
                for _ in range(num_blocks):
                    block = composer._aggregate_time_block(
                        dest_forecast.weather,
                        dest_forecast.marine,
                        current_time,
                        block_hours,
                        wind_threshold_kn,
                    )
                    dest_blocks.append(block)
                    current_time += timedelta(hours=block_hours)

                email_data.destination_name = destination["name"]
                email_data.destination_blocks = dest_blocks
                logger.info("Destination forecast added for %s", destination["name"])
            except Exception as e:
                logger.warning("Failed to fetch destination forecast: %s", e)

        # Render HTML
        html_content = composer.render(email_data)
        logger.info("Email composed: %d chars HTML", len(html_content))

        # Send email
        logger.info("Sending forecast to %s%s...", recipient, f" (bcc: {bcc})" if bcc else "")
        resend_id = await sender.send_with_retry(
            to=recipient,
            subject=email_data.subject,
            html_content=html_content,
            chart_na_b64=chart_na_b64,
            chart_eu_b64=chart_eu_b64,
            bcc=bcc,
        )

        await log_send(
            send_log_url,
            **log_common,
            status="sent" if resend_id else "send_failed",
            recipient=recipient,
            resend_id=resend_id,
            subject=email_data.subject,
            wind_alert=any(b.wind_alert for b in email_data.time_blocks or []),
            forecast=forecast,
            charts={
                "na": (chart_na_b64, chart_na_fetched_at),
                "eu": (chart_eu_b64, chart_eu_fetched_at),
            },
        )

        if resend_id:
            logger.info("Forecast sent successfully!")
            return True
        else:
            logger.error("Failed to send forecast email after retries")
            await notify_failure(sender, recipient, alert_email, "Failed to send forecast email after multiple retries.")
            return False

    except Exception as e:
        logger.error("Error generating forecast: %s", e, exc_info=True)
        await log_send(
            send_log_url,
            **log_common,
            status="error",
            recipient=recipient,
            error=str(e),
        )
        await notify_failure(sender, recipient, alert_email, f"Error generating forecast: {e}")
        return False

    finally:
        await weather_client.close()
        await chart_fetcher.close()


async def main():
    parser = argparse.ArgumentParser(description="Marine Weather Agent")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--morning", action="store_true", help="Send morning forecast (18hr)")
    group.add_argument("--evening", action="store_true", help="Send evening forecast (36hr)")
    group.add_argument("--position-only", action="store_true", help="AIS position update only")
    args = parser.parse_args()

    setup_logging()

    logger.info("=== Marine Weather Agent ===")
    config = load_config()
    position_cache = load_position_cache()
    mmsi = config["vessel"]["mmsi"]

    # Attempt AIS lookup
    api_key = os.environ.get("AISSTREAM_API_KEY", "")
    ais_result = await attempt_ais_lookup(mmsi, api_key)

    # Update position cache if we got a new position
    if ais_result:
        position_cache["mmsi"] = mmsi
        position_cache["latitude"] = ais_result["latitude"]
        position_cache["longitude"] = ais_result["longitude"]
        position_cache["updated_at"] = ais_result["timestamp"]
        logger.info("Position cache updated: %.4f, %.4f", ais_result["latitude"], ais_result["longitude"])
    else:
        logger.info("No new AIS position; using cached/default")

    # Always save cache (updates file even if unchanged, for git commit check)
    save_position_cache(position_cache)

    # If position-only mode, we're done
    if args.position_only:
        logger.info("Position-only mode — done.")
        return

    # Run forecast
    is_evening = args.evening
    success = await run_forecast(is_evening, config, position_cache, ais_result)

    logger.info("=== Done ===")
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
