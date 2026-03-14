# host/clawd_tank_menubar/launchd.py
"""Manage Launch at Login via launchd user agent plist."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

PLIST_LABEL = "com.clawd-tank.menubar"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def is_enabled() -> bool:
    """Check if the launch agent plist exists."""
    return PLIST_PATH.exists()


def enable() -> None:
    """Write the launchd plist and load the agent."""
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    executable = sys.executable
    module_path = "clawd_tank_menubar.app"

    plist = {
        "Label": PLIST_LABEL,
        "ProgramArguments": [executable, "-m", module_path],
        "RunAtLoad": True,
        "KeepAlive": False,
    }

    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)

    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(PLIST_PATH)],
        capture_output=True,
    )


def is_stale() -> bool:
    """Check if the plist points to a different executable than the current one."""
    if not PLIST_PATH.exists():
        return False
    try:
        with open(PLIST_PATH, "rb") as f:
            plist = plistlib.load(f)
        program_args = plist.get("ProgramArguments", [])
        if program_args and program_args[0] != sys.executable:
            return True
    except (OSError, plistlib.InvalidFileException):
        pass
    return False


def disable() -> None:
    """Unload and remove the launchd plist."""
    if not PLIST_PATH.exists():
        return

    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(PLIST_PATH)],
        capture_output=True,
    )

    PLIST_PATH.unlink(missing_ok=True)
