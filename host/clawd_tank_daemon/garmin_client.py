# host/clawd_tank_daemon/garmin_client.py
"""HTTPS relay transport for the Garmin watch app.

The watch can only pull, so this client folds the daemon's notification
payloads into one small snapshot and POSTs it to a relay the watch polls.
Config lives in ~/.clawd-tank/garmin.json: {"url": ..., "token": ..., "enabled": true}.
"""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("clawd-tank.garmin")

CONFIG_PATH = Path.home() / ".clawd-tank" / "garmin.json"
DEBOUNCE = 0.3  # seconds; coalesces bursts of events into one POST
HEARTBEAT = 60  # seconds; lets the watch tell "daemon gone" from "all quiet"
POST_TIMEOUT = 10
SHUTDOWN_TIMEOUT = 3  # seconds for the final "sleeping" post, so a dead relay can't stall daemon shutdown
MAX_CARDS = 4
# Same limits the relay applies; a snapshot over its 2 KB cap is rejected outright.
MAX_PROJECT = 24
MAX_MESSAGE = 60
MAX_STATUS = 16


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: the bearer token must only ever go to the configured URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _url_ok(url) -> bool:
    """https only; plain http is allowed for loopback (tests, local relays)."""
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")


def load_config(path: Path | None = None) -> dict | None:
    """Return the config dict if present, valid and enabled, else None."""
    path = path if path is not None else CONFIG_PATH  # read at call time so tests can redirect it
    try:
        cfg = json.loads(path.read_text())
        mode = path.stat().st_mode
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict) or not cfg.get("enabled", True):
        return None
    if not cfg.get("token") or not isinstance(cfg.get("token"), str):
        logger.warning("%s needs a 'token'", path)
        return None
    if not _url_ok(cfg.get("url")):
        logger.warning("%s: 'url' must be an https:// URL (http:// is only allowed for localhost)", path)
        return None
    if mode & 0o077:
        logger.warning("%s is readable by other users; run: chmod 600 %s", path, path)
    return cfg


class GarminClient:
    """Stateless-HTTP transport: always 'connected', best-effort delivery."""

    def __init__(
        self,
        url: str,
        token: str,
        on_disconnect_cb=None,
        on_connect_cb=None,
        debounce: float = DEBOUNCE,
        heartbeat: float = HEARTBEAT,
    ):
        self._url = url
        self._token = token
        self._on_disconnect_cb = on_disconnect_cb
        self._on_connect_cb = on_connect_cb
        self._debounce = debounce
        self._heartbeat = heartbeat
        self._status = "sleeping"
        self._notifications: dict[str, dict] = {}
        self._dirty = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._inflight: asyncio.Future | None = None  # the POST running in a worker thread

    @property
    def is_connected(self) -> bool:
        return self._task is not None and not self._task.done()

    async def connect(self) -> None:
        if self.is_connected:
            return
        self._task = asyncio.create_task(self._poster())
        self._dirty.set()
        if self._on_connect_cb:
            self._on_connect_cb()

    async def disconnect(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # A POST already running in its thread can't be cancelled; wait for it so it can't land after "sleeping".
        if self._inflight is not None and not self._inflight.done():
            await asyncio.wait({self._inflight}, timeout=SHUTDOWN_TIMEOUT)
        await self._post(self._snapshot(offline=True), timeout=SHUTDOWN_TIMEOUT)

    async def ensure_connected(self) -> None:
        if not self.is_connected:
            await self.connect()

    async def write_notification(self, payload: str) -> bool:
        """Fold a daemon payload into the snapshot. Delivery is best-effort."""
        try:
            msg = json.loads(payload)
        except (ValueError, TypeError):
            return False
        if not isinstance(msg, dict):
            return False
        action = msg.get("action")
        if action == "add":
            nid = msg.get("id", "")
            self._notifications.pop(nid, None)  # re-adding moves the card to newest
            self._notifications[nid] = {
                "p": str(msg.get("project", ""))[:MAX_PROJECT],
                "m": str(msg.get("message", ""))[:MAX_MESSAGE],
            }
        elif action == "dismiss":
            self._notifications.pop(msg.get("id", ""), None)
        elif action == "clear":
            self._notifications.clear()
        elif action == "set_status":
            if msg.get("status") != "sweeping":  # oneshot; keep the prior status
                self._status = str(msg.get("status", self._status))[:MAX_STATUS]
        else:
            return True  # set_time, set_sessions, etc. have no watch representation
        self._dirty.set()
        return True

    async def read_config(self) -> dict:
        return {}

    async def write_config(self, payload: str) -> bool:
        return False

    def _snapshot(self, offline: bool = False) -> dict:
        cards = list(self._notifications.values())[-MAX_CARDS:][::-1]  # newest first
        if offline:
            return {"status": "sleeping", "cards": []}
        return {"status": self._status, "cards": cards}

    async def _poster(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._dirty.wait(), timeout=self._heartbeat)
                await asyncio.sleep(self._debounce)
            except asyncio.TimeoutError:
                pass
            self._dirty.clear()
            await self._post(self._snapshot())

    async def _post(self, snapshot: dict, timeout: float = POST_TIMEOUT) -> bool:
        fut = asyncio.ensure_future(asyncio.to_thread(self._post_blocking, snapshot, timeout))
        self._inflight = fut
        try:
            await asyncio.shield(fut)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:  # network errors must never disturb other transports
            logger.warning("Relay POST failed: %s", e)
            return False

    def _post_blocking(self, snapshot: dict, timeout: float) -> None:
        # ensure_ascii=False keeps non-ASCII text at its UTF-8 size instead of 6 bytes per \\uXXXX escape
        body = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "clawd-tank-daemon",  # Cloudflare 403s the default Python-urllib UA
            },
        )
        with _opener.open(req, timeout=timeout):
            pass
