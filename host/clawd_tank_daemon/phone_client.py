# host/clawd_tank_daemon/phone_client.py
"""HTTPS relay transport for the Android bridge app.

Same stateless POST machinery as GarminClient, but the snapshot carries protocol v2
session state (per-session animations and IDs) so the phone can drive the board with
set_sessions. Config lives in ~/.clawd-tank/phone.json: {"url": ..., "token": ..., "enabled": true}.
"""

import json
import logging
from pathlib import Path

from .garmin_client import (
    GarminClient,
    MAX_CARDS,
    MAX_MESSAGE,
    MAX_PROJECT,
    MAX_STATUS,
    load_config as _load_config,
)

logger = logging.getLogger("clawd-tank.phone")

CONFIG_PATH = Path.home() / ".clawd-tank" / "phone.json"
MAX_SESSIONS = 4
MAX_CARD_ID = 40


def load_config(path: Path | None = None) -> dict | None:
    return _load_config(path if path is not None else CONFIG_PATH)


class PhoneClient(GarminClient):
    """Folds daemon payloads into a v2 snapshot:

    {"v": 2, "status": null | "sleeping", "anims": [...], "ids": [...], "subagents": n,
     "overflow": n, "cards": [{"id": ..., "p": ..., "m": ..., "e": 1?}]}

    `status` is only set while the board should show a non-session state (sleeping); a
    set_sessions payload clears it, mirroring the firmware.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._status: str | None = "sleeping"
        self._anims: list[str] = []
        self._ids: list[int] = []
        self._subagents = 0
        self._overflow = 0

    async def read_version(self) -> int:
        return 2

    async def write_notification(self, payload: str) -> bool:
        try:
            msg = json.loads(payload)
        except (ValueError, TypeError):
            return False
        if not isinstance(msg, dict):
            return False
        action = msg.get("action")
        if action == "add":
            nid = str(msg.get("id", ""))[:MAX_CARD_ID]
            self._notifications.pop(nid, None)
            card = {
                "id": nid,
                "p": str(msg.get("project", ""))[:MAX_PROJECT],
                "m": str(msg.get("message", ""))[:MAX_MESSAGE],
            }
            if msg.get("alert") == "error":
                card["e"] = 1
            self._notifications[nid] = card
        elif action == "dismiss":
            self._notifications.pop(str(msg.get("id", ""))[:MAX_CARD_ID], None)
        elif action == "clear":
            self._notifications.clear()
        elif action == "set_sessions":
            anims = msg.get("anims", [])
            ids = msg.get("ids", [])
            if not isinstance(anims, list) or not isinstance(ids, list):
                return False
            n = min(len(anims), len(ids), MAX_SESSIONS)
            self._anims = [str(a)[:MAX_STATUS] for a in anims[:n]]
            self._ids = [int(i) for i in ids[:n]]
            self._subagents = int(msg.get("subagents", 0))
            self._overflow = int(msg.get("overflow", 0))
            self._status = None
        elif action == "set_status":
            status = msg.get("status")
            if status == "sweeping":
                return True  # oneshot
            self._status = str(status)[:MAX_STATUS]
            self._anims, self._ids, self._subagents, self._overflow = [], [], 0, 0
        else:
            return True  # set_time etc. have no phone representation
        self._dirty.set()
        return True

    def _snapshot(self, offline: bool = False) -> dict:
        if offline:
            return {"v": 2, "status": "sleeping", "anims": [], "ids": [], "subagents": 0, "cards": []}
        cards = list(self._notifications.values())[-MAX_CARDS:][::-1]  # newest first
        snap = {
            "v": 2,
            "status": self._status,
            "anims": self._anims,
            "ids": self._ids,
            "subagents": self._subagents,
            "cards": cards,
        }
        if self._overflow:
            snap["overflow"] = self._overflow
        return snap
