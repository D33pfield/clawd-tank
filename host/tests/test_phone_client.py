# host/tests/test_phone_client.py
"""Tests for PhoneClient v2 relay transport."""

import asyncio
import json

import pytest
from clawd_tank_daemon.phone_client import PhoneClient, load_config
from .test_garmin_client import settle, start_relay


def sessions(anims, ids, subagents=0, overflow=0):
    msg = {"action": "set_sessions", "anims": anims, "ids": ids, "subagents": subagents}
    if overflow:
        msg["overflow"] = overflow
    return json.dumps(msg)


@pytest.mark.asyncio
async def test_folds_v2_state_and_cards():
    server, received = start_relay()
    client = PhoneClient(f"http://127.0.0.1:{server.server_port}/p", "tok", debounce=0.01)
    await client.connect()
    await settle(received, 1)
    assert received[0][1]["status"] == "sleeping"
    assert await client.read_version() == 2

    await client.write_notification(sessions(["typing", "idle"], [1, 2], subagents=3, overflow=2))
    await client.write_notification(json.dumps(
        {"action": "add", "id": "a", "project": "p", "message": "m", "alert": "error"}))
    await asyncio.sleep(0.2)
    auth, snap = received[-1]
    assert auth == "Bearer tok"
    assert snap == {
        "v": 2, "status": None, "anims": ["typing", "idle"], "ids": [1, 2], "subagents": 3,
        "overflow": 2, "cards": [{"id": "a", "p": "p", "m": "m", "e": 1}],
    }

    await client.write_notification(json.dumps({"action": "set_status", "status": "sleeping"}))
    await asyncio.sleep(0.2)
    snap = received[-1][1]
    assert snap["status"] == "sleeping" and snap["anims"] == [] and snap["ids"] == []
    await client.disconnect()


@pytest.mark.asyncio
async def test_caps_sessions_and_ignores_garbage():
    client = PhoneClient("http://127.0.0.1:1/p", "tok")
    assert await client.write_notification("nope") is False
    assert await client.write_notification("[]") is False
    assert await client.write_notification(json.dumps({"action": "set_sessions", "anims": 1, "ids": []})) is False
    await client.write_notification(sessions(["idle"] * 6, [1, 2, 3, 4, 5, 6]))
    snap = client._snapshot()
    assert len(snap["anims"]) == 4 and len(snap["ids"]) == 4
    assert await client.write_notification(json.dumps({"action": "set_time", "epoch": 1})) is True


def test_snapshot_under_relay_cap_worst_case():
    # Relay cap is 4096 bytes. Control characters are the worst case: ensure_ascii=False
    # still escapes them as 6-byte \\u00XX sequences.
    client = PhoneClient("http://127.0.0.1:1/p", "tok")
    client._anims = ["confused"] * 4
    client._ids = [10**9] * 4
    for i in range(4):
        client._notifications[str(i)] = {"id": "x" * 40, "p": "\x1b" * 24, "m": "\x1b" * 60, "e": 1}
    body = json.dumps(client._snapshot(), separators=(",", ":"), ensure_ascii=False).encode()
    assert len(body) < 4096


def test_load_config_uses_phone_path(tmp_path):
    p = tmp_path / "phone.json"
    p.write_text(json.dumps({"url": "https://example.com/p", "token": "t"}))
    p.chmod(0o600)
    assert load_config(p)["url"] == "https://example.com/p"
