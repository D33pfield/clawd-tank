# host/clawd_tank_daemon/now_playing.py
"""What is playing on the Windows host, for the phone transport (WSL only, opt-in).

One long-running powershell.exe runs now_playing.ps1, which polls the Windows System Media
Transport Controls in process and prints a JSON line only when the state changes (plus a slow
heartbeat). This module reads those lines and hands a small `music` dict to a callback:

    {"playing": bool, "artist": str, "title": str, "track": int}

`track` goes up by one whenever a new non-empty artist/title shows up, starting from a random
value so a daemon restart can't repeat the id the phone saw last. The callback gets None when
the reader is not running (powershell missing, crashed), so consumers drop the field.

Enable with ~/.clawd-tank/music.json: {"enabled": true} (optional "interval": poll seconds, 1-30).
"""

import asyncio
import json
import logging
import os
import random
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("clawd-tank.music")

CONFIG_PATH = Path.home() / ".clawd-tank" / "music.json"
SCRIPT_PATH = Path(__file__).with_name("now_playing.ps1")
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
MAX_TEXT = 60
MAX_LINE = 4096  # a reader line longer than this is garbage
TRACK_MOD = 1 << 31  # fits a Java/Kotlin Int on the phone
RESTART_MIN = 30  # seconds before restarting a reader that died
RESTART_MAX = 600
STABLE_AFTER = 300  # a reader that ran this long resets the restart back-off
STOP_TIMEOUT = 3


def load_config(path: Path | None = None) -> dict | None:
    """The music config if present and enabled, else None. Holds no secrets."""
    path = path if path is not None else CONFIG_PATH
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict) or not cfg.get("enabled", True):
        return None
    return cfg


def find_powershell() -> str | None:
    if os.path.exists(POWERSHELL):
        return POWERSHELL
    return shutil.which("powershell.exe")


def clean_text(value) -> str:
    """Control and format characters out, whitespace runs collapsed, trimmed, capped."""
    if not isinstance(value, str):
        return ""
    kept = "".join(" " if ch.isspace() else ch for ch in value
                   if ch.isspace() or not unicodedata.category(ch).startswith("C"))
    return " ".join(kept.split())[:MAX_TEXT].strip()


def parse_line(line) -> dict | None:
    """One reader line -> {"playing", "artist", "title"}, or None for anything else (errors, garbage)."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    if not isinstance(line, str) or len(line) > MAX_LINE:
        return None
    try:
        msg = json.loads(line.strip())
    except ValueError:
        return None
    if not isinstance(msg, dict) or msg.get("s") not in ("playing", "paused", "stopped", "none"):
        return None
    if msg["s"] == "none":
        return {"playing": False, "artist": "", "title": ""}
    return {"playing": msg["s"] == "playing", "artist": clean_text(msg.get("a")), "title": clean_text(msg.get("t"))}


class TrackCounter:
    """Turns parsed states into `music` dicts with a track id that changes with the track."""

    def __init__(self, seed: int | None = None):
        self._track = (seed if seed is not None else random.randrange(1, TRACK_MOD)) % TRACK_MOD
        self._last: tuple[str, str] | None = None

    def music(self, state: dict) -> dict:
        key = (state["artist"], state["title"])
        if any(key) and key != self._last:
            if self._last is not None:
                self._track = (self._track + 1) % TRACK_MOD
            self._last = key
        return {"playing": state["playing"], "artist": state["artist"], "title": state["title"],
                "track": self._track}


class NowPlayingReader:
    """Keeps one powershell.exe reader alive and reports changes to `on_change`."""

    def __init__(self, on_change: Callable[[Optional[dict]], None], interval: float = 2,
                 command: list[str] | None = None):
        self._on_change = on_change
        try:
            interval = float(interval)
        except (TypeError, ValueError):
            interval = 2.0
        self._interval = min(30.0, max(1.0, interval)) if interval == interval else 2.0
        self._command = command  # tests pass a fake reader
        self._counter = TrackCounter()
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None

    def _build_command(self) -> list[str] | None:
        if self._command is not None:
            return self._command
        exe = find_powershell()
        if not exe:
            return None
        try:
            script = subprocess.run(["wslpath", "-w", str(SCRIPT_PATH)], capture_output=True,
                                    text=True, timeout=5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", script, "-Interval", str(self._interval)]

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._end_process()

    async def _end_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()  # the script exits when its stdin closes
            await asyncio.wait_for(proc.wait(), STOP_TIMEOUT)
        except (asyncio.TimeoutError, OSError):
            try:
                proc.kill()  # killing the WSL side also ends the Windows process
                await asyncio.wait_for(proc.wait(), STOP_TIMEOUT)
            except (ProcessLookupError, asyncio.TimeoutError, OSError):
                pass

    async def _run(self) -> None:
        backoff = RESTART_MIN
        warned = False
        while True:
            cmd = await asyncio.to_thread(self._build_command)
            started = time.monotonic()
            if cmd is None:
                if not warned:
                    logger.warning("Music: powershell.exe not reachable; is this WSL with interop on?")
                    warned = True
            else:
                try:
                    await self._read(cmd)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if not warned:
                        logger.warning("Music reader failed: %s", e)
                        warned = True
                finally:
                    await self._end_process()
            self._on_change(None)
            if time.monotonic() - started >= STABLE_AFTER:
                backoff, warned = RESTART_MIN, False
            await asyncio.sleep(backoff)
            backoff = min(RESTART_MAX, backoff * 2)

    async def _read(self, cmd: list[str]) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=MAX_LINE * 4,
        )
        logger.info("Music reader started")
        assert self._proc.stdout is not None
        while True:
            try:
                line = await self._proc.stdout.readline()
            except ValueError:  # a line over the stream limit
                continue
            if not line:
                break
            state = parse_line(line)
            if state is not None:
                self._on_change(self._counter.music(state))
            elif b'"err"' in line:
                logger.warning("Music reader: %s", line.decode("utf-8", "replace").strip()[:200])
        code = await self._proc.wait()
        logger.info("Music reader exited (%s)", code)
