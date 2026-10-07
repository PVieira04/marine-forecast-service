"""
Send log — records each forecast send in Cloudflare D1, via the Worker in worker/.

Logging must never block or fail a forecast: every error is caught and logged.
Disabled when `send_log_url` (config.yaml) or SEND_LOG_TOKEN (env) is missing.
"""

import dataclasses
import json
import logging
import os
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not JSON serialisable: {type(obj).__name__}")


async def log_send(
    url: Optional[str],
    *,
    slot: str,
    status: str,
    test_mode: bool,
    recipient: str,
    resend_id: Optional[str] = None,
    subject: Optional[str] = None,
    error: Optional[str] = None,
    location_name: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    position_source: Optional[str] = None,
    forecast_hours: Optional[int] = None,
    wind_alert: Optional[bool] = None,
    forecast=None,
    charts: Optional[dict] = None,
) -> None:
    """POST one send record. `charts` maps kind -> (jpeg_b64, fetched_at)."""
    token = os.environ.get("SEND_LOG_TOKEN", "")
    if not url or not token:
        logger.info("Send log disabled (no send_log_url or SEND_LOG_TOKEN)")
        return

    try:
        payload = {
            "sent_at": datetime.utcnow().isoformat() + "Z",
            "slot": slot,
            "status": status,
            "test_mode": test_mode,
            "recipient": recipient,
            "resend_id": resend_id,
            "subject": subject,
            "error": error,
            "location_name": location_name,
            "latitude": latitude,
            "longitude": longitude,
            "position_source": position_source,
            "forecast_hours": forecast_hours,
            "wind_alert": wind_alert,
            "forecast": dataclasses.asdict(forecast) if forecast else None,
            "charts": [
                {"kind": kind, "jpeg_b64": b64, "fetched_at": fetched_at}
                for kind, (b64, fetched_at) in (charts or {}).items()
                if b64
            ],
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                content=json.dumps(payload, default=_json_default),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
        logger.info("Send logged: %s", response.text)
    except Exception as e:
        logger.warning("Send log failed (forecast unaffected): %s", e)
