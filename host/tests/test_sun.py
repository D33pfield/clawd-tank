"""Tests for clawd_tank_daemon.sun (sunrise/sunset from the host's timezone)."""

from datetime import date

import pytest

from clawd_tank_daemon import sun

ZONE_TAB = (
    "# tzdb timezone descriptions\n"
    "US\t+415100-0873900\tAmerica/Chicago\tCentral (most areas)\n"
    "US\t+404251-0740023\tAmerica/New_York\tEastern (most areas)\n"
    "AU\t-3352+15113\tAustralia/Sydney\tNew South Wales\n"
    "SJ\t+7800+01600\tArctic/Longyearbyen\tSvalbard\n"
)

CHICAGO = (41.85, -87.65)
SYDNEY = (-33.8667, 151.2167)
SVALBARD = (78.0, 16.0)


def hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@pytest.fixture(autouse=True)
def _fresh_cache():
    sun._host_coordinates.cache_clear()
    yield
    sun._host_coordinates.cache_clear()


@pytest.fixture
def zone_tab(tmp_path):
    path = tmp_path / "zone.tab"
    path.write_text(ZONE_TAB, encoding="utf-8")
    return str(path)


# --- zone.tab lookup ---------------------------------------------------------


def test_zone_coordinates_parses_minutes_and_seconds_formats(zone_tab):
    lat, lon = sun.zone_coordinates("America/Chicago", [zone_tab])
    assert (lat, lon) == pytest.approx((41.85, -87.65), abs=0.01)

    lat, lon = sun.zone_coordinates("America/New_York", [zone_tab])  # seconds format
    assert (lat, lon) == pytest.approx((40.714, -74.006), abs=0.01)

    lat, lon = sun.zone_coordinates("Australia/Sydney", [zone_tab])  # southern/eastern
    assert (lat, lon) == pytest.approx((-33.867, 151.217), abs=0.01)


def test_zone_coordinates_unknown_zone_or_missing_file(zone_tab, tmp_path):
    assert sun.zone_coordinates("Mars/Olympus_Mons", [zone_tab]) is None
    assert sun.zone_coordinates("America/Chicago", [str(tmp_path / "nope.tab")]) is None


def test_zone_coordinates_tries_later_paths(zone_tab, tmp_path):
    paths = [str(tmp_path / "nope.tab"), zone_tab]
    assert sun.zone_coordinates("America/Chicago", paths) is not None


# --- finding the host's zone name -------------------------------------------


def test_local_zone_name_from_tz_env(tmp_path):
    none = str(tmp_path / "missing")
    assert sun.local_zone_name({"TZ": ":America/Chicago"}, none, none) == "America/Chicago"
    assert sun.local_zone_name({"TZ": "Europe/London"}, none, none) == "Europe/London"
    # A POSIX string like UTC+5 isn't an IANA name and can't be mapped to a place.
    assert sun.local_zone_name({"TZ": "UTC+5"}, none, none) is None


def test_local_zone_name_from_localtime_symlink(tmp_path):
    zone_file = tmp_path / "usr" / "share" / "zoneinfo" / "America" / "Chicago"
    zone_file.parent.mkdir(parents=True)
    zone_file.write_bytes(b"TZif")
    link = tmp_path / "localtime"
    link.symlink_to(zone_file)
    assert sun.local_zone_name({}, str(link), str(tmp_path / "missing")) == "America/Chicago"


def test_local_zone_name_from_timezone_file(tmp_path):
    tzfile = tmp_path / "timezone"
    tzfile.write_text("Europe/Berlin\n")
    assert sun.local_zone_name({}, str(tmp_path / "missing"), str(tzfile)) == "Europe/Berlin"


def test_local_zone_name_unknown(tmp_path):
    none = str(tmp_path / "missing")
    assert sun.local_zone_name({}, none, none) is None


# --- the sun maths -----------------------------------------------------------


def test_chicago_solstices_match_published_times():
    # Published: 21 Jun sunrise 05:15 / sunset 20:30 CDT; 21 Dec 07:16 / 16:23 CST.
    rise, sset = sun.sun_times(*CHICAGO, date(2026, 6, 21), -300)
    assert abs(rise - (5 * 60 + 15)) <= 6, hm(rise)
    assert abs(sset - (20 * 60 + 30)) <= 6, hm(sset)

    rise, sset = sun.sun_times(*CHICAGO, date(2026, 12, 21), -360)
    assert abs(rise - (7 * 60 + 16)) <= 6, hm(rise)
    assert abs(sset - (16 * 60 + 23)) <= 6, hm(sset)


def test_equator_day_is_about_twelve_hours_all_year():
    for month in range(1, 13):
        rise, sset = sun.sun_times(0.0, 0.0, date(2026, month, 15), 0)
        assert abs((sset - rise) - 727) <= 6  # 12 h 07 min with refraction


def test_seasons_are_reversed_between_hemispheres():
    def length(coords, day, offset):
        rise, sset = sun.sun_times(*coords, day, offset)
        return sset - rise

    june, december = date(2026, 6, 21), date(2026, 12, 21)
    assert length(CHICAGO, june, -300) > length(CHICAGO, december, -360) + 240
    assert length(SYDNEY, december, 660) > length(SYDNEY, june, 600) + 180


def test_daylight_follows_the_timezone_offset():
    # Same place and day: the clock reading shifts by exactly the offset change.
    a = sun.sun_times(*CHICAGO, date(2026, 6, 21), -360)
    b = sun.sun_times(*CHICAGO, date(2026, 6, 21), -300)
    assert (b[0] - a[0], b[1] - a[1]) == (60, 60)


def test_polar_days_and_nights_are_clamped():
    rise, sset = sun.sun_times(*SVALBARD, date(2026, 6, 21), 120)  # midnight sun
    assert 0 <= rise < sset <= 1440
    assert sset - rise >= 1300

    rise, sset = sun.sun_times(*SVALBARD, date(2026, 12, 21), 60)  # polar night
    assert sset - rise == 2 * sun.MIN_HALF_DAY_MIN


def test_always_a_valid_ordered_pair_everywhere():
    for lat in range(-85, 90, 5):
        for lon in range(-180, 181, 45):
            for month in range(1, 13):
                for offset in range(-720, 841, 180):
                    rise, sset = sun.sun_times(lat, lon, date(2026, month, 21), offset)
                    assert isinstance(rise, int) and isinstance(sset, int)
                    assert 0 <= rise < sset <= 1440, (lat, lon, month, offset, rise, sset)


# --- today's values for this host -------------------------------------------


def test_todays_sun_times_is_none_without_a_zone(monkeypatch):
    monkeypatch.setattr(sun, "local_zone_name", lambda: None)
    assert sun.todays_sun_times() is None


def test_todays_sun_times_is_none_without_coordinates(monkeypatch):
    monkeypatch.setattr(sun, "local_zone_name", lambda: "Mars/Olympus_Mons")
    monkeypatch.setattr(sun, "zone_coordinates", lambda name: None)
    assert sun.todays_sun_times() is None


def test_todays_sun_times_uses_the_host_zone(monkeypatch):
    monkeypatch.setattr(sun, "local_zone_name", lambda: "America/Chicago")
    monkeypatch.setattr(sun, "zone_coordinates", lambda name: CHICAGO)
    monkeypatch.setattr(sun, "_local_utc_offset_minutes", lambda day: -300)
    result = sun.todays_sun_times(date(2026, 6, 21))
    assert result == sun.sun_times(*CHICAGO, date(2026, 6, 21), -300)
