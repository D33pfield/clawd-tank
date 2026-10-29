# host/tests/test_garmin_client.py
"""Tests for GarminClient relay transport."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from clawd_tank_daemon.garmin_client import GarminClient, load_config


def start_relay(status=204):
    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.headers["Authorization"], json.loads(body)))
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, received


async def settle(received, n, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        if len(received) >= n:
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_folds_events_into_snapshot():
    server, received = start_relay()
    url = f"http://127.0.0.1:{server.server_port}/clawd/state"
    client = GarminClient(url, "tok", debounce=0.01)
    await client.connect()
    await settle(received, 1)  # initial post
    await client.write_notification(json.dumps({"action": "set_status", "status": "working_2"}))
    await client.write_notification(json.dumps({"action": "add", "id": "a", "project": "p1", "message": "m1"}))
    await client.write_notification(json.dumps({"action": "add", "id": "b", "project": "p2", "message": "m2"}))
    await settle(received, 2)
    await asyncio.sleep(0.1)
    auth, snap = received[-1]
    assert auth == "Bearer tok"
    assert snap == {"status": "working_2", "cards": [{"p": "p2", "m": "m2"}, {"p": "p1", "m": "m1"}]}

    await client.write_notification(json.dumps({"action": "dismiss", "id": "b"}))
    await client.write_notification(json.dumps({"action": "set_status", "status": "sweeping"}))
    await asyncio.sleep(0.2)
    assert received[-1][1] == {"status": "working_2", "cards": [{"p": "p1", "m": "m1"}]}

    await client.write_notification(json.dumps({"action": "clear"}))
    await asyncio.sleep(0.2)
    assert received[-1][1]["cards"] == []
    await client.disconnect()
    assert received[-1][1] == {"status": "sleeping", "cards": []}
    server.shutdown()


@pytest.mark.asyncio
async def test_caps_cards_and_ignores_unrelated_actions():
    server, received = start_relay()
    client = GarminClient(f"http://127.0.0.1:{server.server_port}/x", "t", debounce=0.01)
    await client.connect()
    for i in range(6):
        await client.write_notification(json.dumps({"action": "add", "id": str(i), "project": "p", "message": str(i)}))
    assert await client.write_notification(json.dumps({"action": "set_time", "epoch": 1})) is True
    assert await client.write_notification("not json") is False
    await asyncio.sleep(0.2)
    assert [c["m"] for c in received[-1][1]["cards"]] == ["5", "4", "3", "2"]
    await client.disconnect()
    server.shutdown()


@pytest.mark.asyncio
async def test_post_failure_is_non_fatal():
    client = GarminClient("http://127.0.0.1:1/x", "t", debounce=0.01)
    await client.connect()
    assert await client.write_notification(json.dumps({"action": "clear"})) is True
    await asyncio.sleep(0.1)
    assert client.is_connected
    await client.disconnect()


@pytest.mark.asyncio
async def test_heartbeat_reposts():
    server, received = start_relay()
    client = GarminClient(f"http://127.0.0.1:{server.server_port}/x", "t", debounce=0.01, heartbeat=0.1)
    await client.connect()
    await settle(received, 3)
    assert len(received) >= 3
    await client.disconnect()
    server.shutdown()


def test_load_config(tmp_path):
    p = tmp_path / "garmin.json"
    assert load_config(p) is None
    p.write_text('{"url": "https://x/y", "token": "t"}')
    assert load_config(p)["url"] == "https://x/y"
    p.write_text('{"url": "https://x/y", "token": "t", "enabled": false}')
    assert load_config(p) is None
    p.write_text('{"url": "https://x/y"}')
    assert load_config(p) is None
    p.write_text("garbage")
    assert load_config(p) is None


def test_load_config_url_rules(tmp_path):
    p = tmp_path / "garmin.json"
    for url, ok in [
        ("https://x.example/clawd/state", True),
        ("http://127.0.0.1:8788/clawd/state", True),
        ("http://x.example/clawd/state", False),
        ("file:///etc/hostname", False),
        ("ftp://x.example/", False),
        ("https://", False),
        (123, False),
    ]:
        p.write_text(json.dumps({"url": url, "token": "t"}))
        assert (load_config(p) is not None) == ok, url


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_token_not_forwarded():
    hits = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(("GET", self.headers.get("Authorization")))
            self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
        do_POST = do_GET

        def log_message(self, *a):
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()

    class Redirector(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/stolen")
            self.send_header("Content-Length", "0"); self.end_headers()

        def log_message(self, *a):
            pass

    redirector = HTTPServer(("127.0.0.1", 0), Redirector)
    threading.Thread(target=redirector.serve_forever, daemon=True).start()
    client = GarminClient(f"http://127.0.0.1:{redirector.server_port}/x", "secret", debounce=0.01)
    assert await client._post({"status": "idle", "cards": []}) is False
    assert hits == []
    redirector.shutdown(); target.shutdown()


@pytest.mark.asyncio
async def test_snapshot_stays_under_relay_cap_with_long_and_non_ascii_text():
    server, received = start_relay()
    client = GarminClient(f"http://127.0.0.1:{server.server_port}/x", "t", debounce=0.01)
    await client.connect()
    for i in range(6):
        await client.write_notification(json.dumps(
            {"action": "add", "id": str(i), "project": "项目" * 40, "message": "错误" * 700}))
    await asyncio.sleep(0.3)
    body = json.dumps(received[-1][1], separators=(",", ":"), ensure_ascii=False).encode()
    assert len(body) < 2048
    assert all(len(c["m"]) <= 60 and len(c["p"]) <= 24 for c in received[-1][1]["cards"])
    await client.disconnect()
    server.shutdown()


@pytest.mark.asyncio
async def test_non_object_payload_is_rejected_not_raised():
    client = GarminClient("http://127.0.0.1:1/x", "t")
    assert await client.write_notification("[1]") is False
    assert await client.write_notification('"x"') is False
