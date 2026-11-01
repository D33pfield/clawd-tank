# host/tests/test_now_playing.py
"""Tests for the Windows now-playing reader (parsing, track ids, process lifecycle)."""

import asyncio
import json
import sys

import pytest
import clawd_tank_daemon.now_playing as now_playing
from clawd_tank_daemon.now_playing import (
    NowPlayingReader,
    TrackCounter,
    clean_text,
    load_config,
    parse_line,
)

# Lines as now_playing.ps1 prints them (CRLF from Windows).
PLAYING = b'{"s":"playing","a":"Beyonc\xc3\xa9","t":"\xe5\x9d\x82\xe6\x9c\xac\xe9\xbe\x8d\xe4\xb8\x80 Song"}\r\n'
PAUSED = '{"s":"paused","a":"Artist","t":"Title"}\r\n'
NONE = '{"s":"none","a":"","t":""}'


def test_parse_states():
    assert parse_line(PLAYING) == {"playing": True, "artist": "Beyoncé", "title": "坂本龍一 Song"}
    assert parse_line(PAUSED) == {"playing": False, "artist": "Artist", "title": "Title"}
    assert parse_line('{"s":"stopped","a":"A","t":"T"}') == {"playing": False, "artist": "A", "title": "T"}
    assert parse_line(NONE) == {"playing": False, "artist": "", "title": ""}
    assert parse_line('{"s":"none","a":"leftover","t":"x"}') == {"playing": False, "artist": "", "title": ""}


@pytest.mark.parametrize("line", [
    "", "not json", "[]", "null", '{"err":"WinRT media controls unavailable"}',
    '{"s":"dancing","a":"A","t":"T"}', '{"a":"A","t":"T"}', b"\xff\xfe garbage",
    '{"s":"playing","a":"' + "x" * 5000 + '","t":""}',
])
def test_parse_rejects_garbage(line):
    assert parse_line(line) is None


def test_parse_non_string_fields_become_empty():
    assert parse_line('{"s":"playing","a":5,"t":["x"]}') == {"playing": True, "artist": "", "title": ""}


def test_clean_text_strips_controls_collapses_and_caps():
    assert clean_text("  a\tb\n\n c \x1b[0m​ ") == "a b c [0m"
    assert clean_text("x" * 100) == "x" * 60
    assert clean_text("word " * 20) == ("word " * 12).strip()  # no trailing space after the cap
    assert clean_text(None) == ""
    assert clean_text(42) == ""


def test_track_id_changes_with_the_track_only():
    c = TrackCounter(seed=7)
    a = {"playing": True, "artist": "A", "title": "One"}
    assert c.music(a) == {"playing": True, "artist": "A", "title": "One", "track": 7}
    assert c.music({**a, "playing": False})["track"] == 7  # pause keeps the id
    assert c.music({"playing": False, "artist": "", "title": ""})["track"] == 7  # nothing playing
    assert c.music(a)["track"] == 7  # same track back
    assert c.music({**a, "title": "Two"})["track"] == 8
    assert c.music({**a, "artist": "B", "title": "Two"})["track"] == 9


def test_track_id_wraps_and_seeds_randomly():
    c = TrackCounter(seed=now_playing.TRACK_MOD - 1)
    c.music({"playing": True, "artist": "", "title": "One"})
    assert c.music({"playing": True, "artist": "", "title": "Two"})["track"] == 0
    seeds = {TrackCounter().music({"playing": False, "artist": "", "title": ""})["track"] for _ in range(5)}
    assert len(seeds) > 1


def test_load_config(tmp_path):
    p = tmp_path / "music.json"
    assert load_config(p) is None
    p.write_text('{"enabled": true}')
    assert load_config(p) == {"enabled": True}
    p.write_text('{"enabled": false}')
    assert load_config(p) is None
    p.write_text("garbage")
    assert load_config(p) is None
    assert load_config() is None  # conftest points the default at an empty tmp dir


def test_bad_interval_falls_back():
    assert NowPlayingReader(lambda m: None, interval="soon")._interval == 2.0
    assert NowPlayingReader(lambda m: None, interval=0.01)._interval == 1.0
    assert NowPlayingReader(lambda m: None, interval=1e9)._interval == 30.0


FAKE_READER = r"""
import sys
for line in sys.argv[1:]:
    print(line, flush=True)
sys.stdin.read()  # like the real script: exit when the daemon closes stdin
"""


@pytest.mark.asyncio
async def test_reader_reports_changes_and_stops_cleanly():
    got = []
    lines = [PLAYING.decode().strip(), "garbage", PAUSED.strip(), NONE]
    reader = NowPlayingReader(got.append, command=[sys.executable, "-c", FAKE_READER, *lines])
    reader.start()
    for _ in range(150):
        if len(got) >= 3:
            break
        await asyncio.sleep(0.02)
    proc = reader._proc
    assert [m["playing"] for m in got] == [True, False, False]
    assert got[0]["artist"] == "Beyoncé" and got[2]["title"] == ""
    assert got[0]["track"] + 1 == got[1]["track"] == got[2]["track"]
    await reader.stop()
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_reader_death_drops_music_and_backs_off(monkeypatch):
    monkeypatch.setattr(now_playing, "RESTART_MIN", 0.05)
    got = []
    reader = NowPlayingReader(got.append, command=[sys.executable, "-c", f"print({NONE!r})"])
    reader.start()
    for _ in range(150):
        if None in got:
            break
        await asyncio.sleep(0.02)
    await reader.stop()
    assert got[0] == {"playing": False, "artist": "", "title": "", "track": got[0]["track"]}
    assert got[1] is None


@pytest.mark.asyncio
async def test_reader_without_powershell_reports_none(monkeypatch):
    monkeypatch.setattr(now_playing, "find_powershell", lambda: None)
    got = []
    reader = NowPlayingReader(got.append)
    reader.start()
    for _ in range(100):
        if got:
            break
        await asyncio.sleep(0.02)
    await reader.stop()
    assert got == [None]


def test_daemon_starts_no_reader_without_config():
    from clawd_tank_daemon.daemon import ClawdDaemon
    assert ClawdDaemon(sim_only=True)._music is None


def test_daemon_needs_phone_transport_for_music(tmp_path):
    (tmp_path / "music.json").write_text(json.dumps({"enabled": True}))
    from clawd_tank_daemon.daemon import ClawdDaemon
    assert ClawdDaemon(sim_only=True)._music is None  # no phone.json, nothing to send it to
    phone = tmp_path / "phone.json"
    phone.write_text(json.dumps({"url": "http://127.0.0.1:1/p", "token": "t"}))
    phone.chmod(0o600)
    d = ClawdDaemon(sim_only=True)
    assert d._music is not None
    assert d._music._on_change == d._transports["phone"].set_music
