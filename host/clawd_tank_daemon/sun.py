"""Today's sunrise and sunset, for the display's day/night sky.

The board has no idea where it is, and the host does not ask. Instead it uses the
coordinates the timezone database lists for the representative city of the host's
own IANA timezone (``zone.tab``, shipped with macOS and Linux), e.g.
``America/Chicago`` -> Chicago. That is only as precise as the zone is small, but
the sky fades over about two hours, so being 15-30 minutes off is not visible.

Everything here fails soft: if the zone or its coordinates cannot be found,
``todays_sun_times()`` returns None and the daemon simply sends no sun times, so the
board keeps its fixed default schedule.
"""

import logging
import math
import os
import re
from datetime import date, datetime, time as dtime
from functools import lru_cache
from typing import Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("clawd-tank.sun")

ZONE_TAB_PATHS = (
    "/usr/share/zoneinfo/zone.tab",
    "/usr/share/lib/zoneinfo/zone.tab",
    "/var/db/timezone/zoneinfo/zone.tab",
)

# Polar day/night: keep the "day" between 2.5 and 23 hours so the display's fades
# (which need room on either side of noon) always make sense.
MIN_HALF_DAY_MIN = 75
MAX_HALF_DAY_MIN = 690

_ZONE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)+$")
# ISO 6709 as used by zone.tab: +-DDMM+-DDDMM or +-DDMMSS+-DDDMMSS
_COORD = re.compile(r"^([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?$")


def local_zone_name(
    env: Mapping[str, str] = os.environ,
    localtime_path: str = "/etc/localtime",
    timezone_file: str = "/etc/timezone",
) -> Optional[str]:
    """IANA name of the host's timezone (``America/Chicago``), or None."""
    tz = env.get("TZ", "").lstrip(":")
    if _ZONE_NAME.match(tz):
        return tz

    # macOS and most Linux: /etc/localtime is a symlink into the zoneinfo tree.
    try:
        target = os.path.realpath(localtime_path)
        if "zoneinfo/" in target:
            name = target.split("zoneinfo/", 1)[1]
            if _ZONE_NAME.match(name):
                return name
    except OSError:
        pass

    # Debian-style systems keep the name in a text file.
    try:
        with open(timezone_file) as f:
            name = f.read().strip()
        if _ZONE_NAME.match(name):
            return name
    except OSError:
        pass

    return None


def _parse_coord(coord: str) -> Optional[Tuple[float, float]]:
    m = _COORD.match(coord)
    if not m:
        return None
    lat = int(m.group(2)) + int(m.group(3)) / 60 + int(m.group(4) or 0) / 3600
    lon = int(m.group(6)) + int(m.group(7)) / 60 + int(m.group(8) or 0) / 3600
    return (-lat if m.group(1) == "-" else lat, -lon if m.group(5) == "-" else lon)


def zone_coordinates(
    zone_name: str, zone_tab_paths: Sequence[str] = ZONE_TAB_PATHS
) -> Optional[Tuple[float, float]]:
    """(latitude, longitude) in degrees of the zone's representative city, or None."""
    for path in zone_tab_paths:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    fields = line.rstrip("\n").split("\t")
                    if len(fields) >= 3 and fields[2] == zone_name:
                        return _parse_coord(fields[1])
        except OSError:
            continue
    return None


def sun_times(
    lat: float, lon: float, day: date, utc_offset_minutes: int
) -> Tuple[int, int]:
    """(sunrise, sunset) in local minutes since midnight, using NOAA's solar equations.

    Accurate to a couple of minutes away from the poles. Polar day and night are
    clamped (see MIN_HALF_DAY_MIN / MAX_HALF_DAY_MIN) so the result is always
    0 <= sunrise < sunset <= 1440.
    """
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)  # fractional year, radians (evaluated at noon)

    eqtime = 229.18 * (  # equation of time, minutes
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    decl = (  # solar declination, radians
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )

    lat_r = math.radians(lat)
    denom = max(math.cos(lat_r) * math.cos(decl), 1e-9)
    # 90.833 deg = the sun's centre at the horizon, with refraction and its radius
    cos_ha = math.cos(math.radians(90.833)) / denom - math.tan(lat_r) * math.tan(decl)
    ha_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_ha))))  # hour angle of sunrise

    half_day = min(max(ha_deg * 4, MIN_HALF_DAY_MIN), MAX_HALF_DAY_MIN)  # minutes
    # Local clock minutes of solar noon. Wrapped into the day so that even an offset
    # that doesn't match the longitude (no real place has one) gives an ordered pair.
    solar_noon = (720 - 4 * lon - eqtime + utc_offset_minutes) % 1440

    rise = max(0, round(solar_noon - half_day))
    sset = min(1440, round(solar_noon + half_day))
    return rise, sset


def _local_utc_offset_minutes(day: date) -> int:
    """The host's UTC offset around noon on `day`, DST included."""
    offset = datetime.combine(day, dtime(12)).astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


@lru_cache(maxsize=32)
def _coordinates_for_zone(zone: str) -> Optional[Tuple[float, float]]:
    """Cached zone.tab lookup (the file is only read once per zone)."""
    coords = zone_coordinates(zone)
    if coords is None:
        logger.info("Sun times unavailable: no coordinates for timezone %s", zone)
        return None
    logger.info("Sun times use %s (lat %.2f, lon %.2f)", zone, coords[0], coords[1])
    return coords


def _host_coordinates() -> Optional[Tuple[float, float]]:
    """Coordinates for the host's current timezone.

    The zone name is re-read on every call (a symlink lookup) and only the zone ->
    coordinates step is cached, so a laptop that changes timezone while the daemon
    runs follows it instead of staying on the first city it saw.
    """
    zone = local_zone_name()
    if zone is None:
        logger.info("Sun times unavailable: could not determine the local timezone name")
        return None
    return _coordinates_for_zone(zone)


def todays_sun_times(today: Optional[date] = None) -> Optional[Tuple[int, int]]:
    """Today's (sunrise, sunset) in local minutes since midnight, or None if unknown."""
    coords = _host_coordinates()
    if coords is None:
        return None
    day = today or date.today()
    try:
        return sun_times(coords[0], coords[1], day, _local_utc_offset_minutes(day))
    except (ValueError, OverflowError) as e:  # pragma: no cover - defensive
        logger.warning("Sun time calculation failed: %s", e)
        return None
