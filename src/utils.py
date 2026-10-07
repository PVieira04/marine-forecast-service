"""Utility functions for conversions and helpers."""

import math
from datetime import datetime
from typing import Optional


def ms_to_knots(speed_ms: float) -> float:
    """Convert meters per second to knots."""
    return speed_ms * 1.94384


def knots_to_ms(speed_kn: float) -> float:
    """Convert knots to meters per second."""
    return speed_kn / 1.94384


def degrees_to_compass(degrees: float) -> str:
    """Convert degrees to compass direction (N, NE, E, etc.)."""
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    index = round(degrees / 22.5) % 16
    return directions[index]


def format_coordinates(lat: float, lon: float) -> str:
    """Format coordinates as human-readable string."""
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}"


def calculate_distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in nautical miles (Haversine formula)."""
    R = 3440.065  # Earth radius in nautical miles

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = math.sin(delta_lat / 2) ** 2 + \
        math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def format_time_ago(dt: datetime) -> str:
    """Format datetime as human-readable 'X ago' string."""
    now = datetime.utcnow()
    delta = now - dt

    if delta.days > 0:
        return f"{delta.days} day{'s' if delta.days > 1 else ''} ago"
    elif delta.seconds >= 3600:
        hours = delta.seconds // 3600
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    elif delta.seconds >= 60:
        minutes = delta.seconds // 60
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    else:
        return "just now"


def calculate_confidence(values: list[float], threshold_pct: float = 15.0) -> str:
    """Generic percentage-spread confidence (kept for backwards compat)."""
    if not values:
        return "LOW"
    if len(values) == 1:
        return "MED"

    min_val = min(values)
    max_val = max(values)
    mean_val = sum(values) / len(values)

    if mean_val == 0:
        return "HIGH"

    spread_pct = ((max_val - min_val) / mean_val) * 100

    if spread_pct <= threshold_pct:
        return "HIGH"
    elif spread_pct <= threshold_pct * 2:
        return "MED"
    else:
        return "LOW"


def confidence_from_spread(
    values: list[float], high_max: float, med_max: float
) -> str:
    """Confidence from absolute spread of model values.

    Pct-based confidence punishes calm days (a 4kn disagreement among 6kn
    forecasts is 67% spread but operationally meaningless). Absolute
    thresholds match how a sailor actually thinks about model disagreement.

    Args:
        values: per-model values
        high_max: max absolute spread that still counts as HIGH
        med_max: max absolute spread that still counts as MED

    Returns: "HIGH", "MED", or "LOW".
    """
    if not values:
        return "LOW"
    if len(values) == 1:
        return "MED"
    spread = max(values) - min(values)
    if spread <= high_max:
        return "HIGH"
    if spread <= med_max:
        return "MED"
    return "LOW"


def format_wind_range(min_wind: float, max_wind: float) -> str:
    """Format wind speed range as 'X-Ykn'."""
    return f"{int(round(min_wind))}-{int(round(max_wind))}kn"


def format_wave_height(height: float, direction: Optional[float] = None) -> str:
    """Format wave height with optional direction."""
    if direction is not None:
        compass = degrees_to_compass(direction)
        return f"{height:.1f}m {compass}"
    return f"{height:.1f}m"


def format_height_range(
    min_h: float, max_h: float, direction: Optional[float] = None
) -> str:
    """Format wave or swell height range as 'X-Ym DIR' (or 'Ym DIR' if equal)."""
    if abs(max_h - min_h) < 0.05:
        body = f"{max_h:.1f}m"
    else:
        body = f"{min_h:.1f}-{max_h:.1f}m"
    if direction is not None:
        return f"{body} {degrees_to_compass(direction)}"
    return body


def format_swell(height: float, direction: float, period: float) -> str:
    """Format swell as 'Xm DIR Ys'."""
    compass = degrees_to_compass(direction)
    return f"{height:.1f}m {compass} {int(round(period))}s"


def format_swell_range(
    min_h: float, max_h: float, direction: float, period: float
) -> str:
    """Format swell with height range as 'X-Ym DIR Zs' (or 'Ym DIR Zs' if equal)."""
    compass = degrees_to_compass(direction)
    if abs(max_h - min_h) < 0.05:
        return f"{max_h:.1f}m {compass} {int(round(period))}s"
    return f"{min_h:.1f}-{max_h:.1f}m {compass} {int(round(period))}s"


def format_visibility(visibility_m: float) -> str:
    """Format visibility in human-readable form."""
    if visibility_m >= 10000:
        return ">10km"
    elif visibility_m >= 1000:
        return f"{int(visibility_m / 1000)}km"
    else:
        return f"{int(visibility_m)}m"
