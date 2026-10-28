#!/usr/bin/env python3
"""Interactive BLE test tool for Clawd Tank firmware.

Connect to the device and press keys to trigger state transitions.
"""

import asyncio
import json
import sys
import tty
import termios

try:
    import bleak
except ImportError:
    print("Error: bleak not installed. Run: pip install bleak")
    sys.exit(1)

SERVICE_UUID = "aecbefd9-98a2-4773-9fed-bb2166daa49a"
NOTIF_CHAR_UUID = "71ffb137-8b7a-47c9-9a7a-4b1b16662d9a"
CONFIG_CHAR_UUID = "e9f6e626-5fca-4201-b80c-4d2b51c40f51"
DEVICE_NAME = "Clawd Tank"

# Preset notifications for testing
PRESETS = [
    {"id": "slack_1", "project": "Slack", "message": "New message from Alice"},
    {"id": "github_1", "project": "GitHub", "message": "PR #42 approved by Bob"},
    {"id": "email_1", "project": "Email", "message": "Meeting at 3pm today"},
    {"id": "linear_1", "project": "Linear", "message": "Bug CT-123 assigned to you"},
    {"id": "github_2", "project": "GitHub", "message": "CI failed on main branch"},
    {"id": "slack_2", "project": "Slack", "message": "Thread reply from Charlie"},
    {"id": "sentry_1", "project": "Sentry", "message": "TypeError in api/handler.py"},
    {"id": "vercel_1", "project": "Vercel", "message": "Deployment ready: preview"},
]

HELP = """\
\033[1;36m╔══════════════════════════════════════════════════════════════╗
║                  Clawd Tank BLE Interactive Tester           ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  \033[1;33mNotifications\033[1;36m                                              ║
║    \033[1;37mn\033[0;36m  — Add next preset notification                        \033[1;36m║
║    \033[1;37mN\033[0;36m  — Add ALL 8 notifications (fill buffer)               \033[1;36m║
║    \033[1;37md\033[0;36m  — Dismiss most recent notification                    \033[1;36m║
║    \033[1;37mD\033[0;36m  — Dismiss oldest notification                         \033[1;36m║
║    \033[1;37mc\033[0;36m  — Clear all notifications                             \033[1;36m║
║                                                              ║
║  \033[1;33mConfig\033[0;36m                                                     \033[1;36m║
║    \033[1;37mr\033[0;36m  — Read current config                                 \033[1;36m║
║    \033[1;37m+\033[0;36m  — Brightness up (+25)                                 \033[1;36m║
║    \033[1;37m-\033[0;36m  — Brightness down (-25)                               \033[1;36m║
║    \033[1;37m0\033[0;36m  — Set brightness to minimum (10)                      \033[1;36m║
║    \033[1;37m9\033[0;36m  — Set brightness to maximum (255)                     \033[1;36m║
║    \033[1;37ms\033[0;36m  — Set sleep timeout to 10s (quick test)               \033[1;36m║
║    \033[1;37mS\033[0;36m  — Set sleep timeout to 5min (default)                 \033[1;36m║
║    \033[1;37mt\033[0;36m  — Toggle the day/night cycle (default: off)           \033[1;36m║
║                                                              ║
║  \033[1;33mScenarios\033[0;36m                                                  \033[1;36m║
║    \033[1;37m1\033[0;36m  — Burst: 3 notifications, 500ms apart                 \033[1;36m║
║    \033[1;37m2\033[0;36m  — Fill & drain: add 8, then dismiss one by one        \033[1;36m║
║    \033[1;37m3\033[0;36m  — Reconnect sim: disconnect, wait 2s, reconnect       \033[1;36m║
║    \033[1;37m4\033[0;36m  — Sleep cycle: set 5s timeout, wait for sleep         \033[1;36m║
║                                                              ║
║  \033[1;33mConnection\033[0;36m                                                 \033[1;36m║
║    \033[1;37mx\033[0;36m  — Disconnect from device                              \033[1;36m║
║    \033[1;37mq\033[0;36m  — Quit                                                \033[1;36m║
║    \033[1;37m?\033[0;36m  — Show this help                                      \033[1;36m║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝\033[0m"""


class InteractiveTester:
    def __init__(self):
        self.client: bleak.BleakClient | None = None
        self.device = None
        self.next_preset = 0
        self.active_ids: list[str] = []  # track added notification IDs in order
        self.brightness = 128  # assumed starting value
        self.day_night = False  # updated from the device's config on connect / 'r'
        self.running = True

    def log(self, msg: str, color: str = "0"):
        print(f"\033[{color}m  {msg}\033[0m")

    def log_ok(self, msg: str):
        self.log(msg, "1;32")

    def log_warn(self, msg: str):
        self.log(msg, "1;33")

    def log_err(self, msg: str):
        self.log(msg, "1;31")

    def log_info(self, msg: str):
        self.log(msg, "0;37")

    async def scan_and_connect(self) -> bool:
        print("\n\033[1;36mScanning for Clawd Tank...\033[0m")
        devices = await bleak.BleakScanner.discover(timeout=5.0, return_adv=True)
        for d, adv in devices.values():
            if d.name and DEVICE_NAME.lower() in d.name.lower():
                self.device = d
                self.log_ok(f"Found: {d.name} [{d.address}] RSSI={adv.rssi}")
                break

        if not self.device:
            self.log_err("Device not found!")
            return False

        print(f"\033[1;36mConnecting to {self.device.name}...\033[0m")
        self.client = bleak.BleakClient(self.device.address)
        await self.client.connect()
        self.log_ok(f"Connected! MTU={self.client.mtu_size}")
        return True

    async def send_notif(self, cmd: dict):
        payload = json.dumps(cmd)
        self.log_info(f">> {payload}")
        await self.client.write_gatt_char(NOTIF_CHAR_UUID, payload.encode("utf-8"))

    async def send_config(self, cmd: dict):
        payload = json.dumps(cmd)
        self.log_info(f">> config: {payload}")
        await self.client.write_gatt_char(CONFIG_CHAR_UUID, payload.encode("utf-8"))

    async def read_config(self):
        data = await self.client.read_gatt_char(CONFIG_CHAR_UUID)
        config = json.loads(data.decode("utf-8"))
        self.log_ok(f"Config: {json.dumps(config, indent=2)}")
        self.brightness = config.get("brightness", self.brightness)
        self.day_night = config.get("day_night", self.day_night)

    # -- Actions --

    async def add_next_notification(self):
        preset = PRESETS[self.next_preset % len(PRESETS)]
        self.next_preset += 1
        await self.send_notif({"action": "add", **preset})
        self.active_ids.append(preset["id"])
        self.log_ok(f"Added [{preset['project']}] {preset['message']}")
        self.log_info(f"Active: {len(self.active_ids)} notification(s)")

    async def add_all_notifications(self):
        for preset in PRESETS:
            await self.send_notif({"action": "add", **preset})
            self.active_ids.append(preset["id"])
            await asyncio.sleep(0.15)
        self.next_preset = 0
        self.log_ok(f"Added all {len(PRESETS)} notifications")

    async def dismiss_newest(self):
        if not self.active_ids:
            self.log_warn("No active notifications to dismiss")
            return
        nid = self.active_ids.pop()
        await self.send_notif({"action": "dismiss", "id": nid})
        self.log_ok(f"Dismissed: {nid}")
        self.log_info(f"Active: {len(self.active_ids)} notification(s)")

    async def dismiss_oldest(self):
        if not self.active_ids:
            self.log_warn("No active notifications to dismiss")
            return
        nid = self.active_ids.pop(0)
        await self.send_notif({"action": "dismiss", "id": nid})
        self.log_ok(f"Dismissed: {nid}")
        self.log_info(f"Active: {len(self.active_ids)} notification(s)")

    async def clear_all(self):
        await self.send_notif({"action": "clear"})
        self.active_ids.clear()
        self.log_ok("Cleared all notifications")

    async def brightness_up(self):
        self.brightness = min(255, self.brightness + 25)
        await self.send_config({"brightness": self.brightness})
        self.log_ok(f"Brightness: {self.brightness}")

    async def brightness_down(self):
        self.brightness = max(10, self.brightness - 25)
        await self.send_config({"brightness": self.brightness})
        self.log_ok(f"Brightness: {self.brightness}")

    async def brightness_min(self):
        self.brightness = 10
        await self.send_config({"brightness": self.brightness})
        self.log_ok(f"Brightness: {self.brightness}")

    async def brightness_max(self):
        self.brightness = 255
        await self.send_config({"brightness": self.brightness})
        self.log_ok(f"Brightness: {self.brightness}")

    async def sleep_quick(self):
        await self.send_config({"sleep_timeout": 10})
        self.log_ok("Sleep timeout: 10s (will sleep soon if idle)")

    async def sleep_default(self):
        await self.send_config({"sleep_timeout": 300})
        self.log_ok("Sleep timeout: 5min (default)")

    async def toggle_day_night(self):
        self.day_night = not self.day_night
        await self.send_config({"day_night": self.day_night})
        self.log_ok(f"Day/night cycle: {'on' if self.day_night else 'off'}")

    # -- Scenarios --

    async def scenario_burst(self):
        self.log_ok("Scenario: burst 3 notifications")
        for i in range(3):
            await self.add_next_notification()
            await asyncio.sleep(0.5)
        self.log_ok("Burst complete")

    async def scenario_fill_drain(self):
        self.log_ok("Scenario: fill buffer then drain one by one")
        await self.add_all_notifications()
        await asyncio.sleep(2)
        while self.active_ids:
            await self.dismiss_newest()
            await asyncio.sleep(0.8)
        self.log_ok("Fill & drain complete")

    async def scenario_reconnect(self):
        self.log_ok("Scenario: disconnect, wait 2s, reconnect")
        self.log_warn("Disconnecting...")
        await self.client.disconnect()
        await asyncio.sleep(2)
        self.log_warn("Reconnecting...")
        await self.client.connect()
        self.active_ids.clear()
        self.next_preset = 0
        self.log_ok(f"Reconnected! MTU={self.client.mtu_size}")

    async def scenario_sleep(self):
        self.log_ok("Scenario: sleep cycle (5s timeout)")
        await self.clear_all()
        await self.send_config({"sleep_timeout": 5})
        self.log_info("Waiting 7s for Clawd to fall asleep...")
        await asyncio.sleep(7)
        self.log_info("Sending notification to wake up...")
        await self.add_next_notification()
        await asyncio.sleep(2)
        await self.clear_all()
        await self.send_config({"sleep_timeout": 300})
        self.log_ok("Sleep cycle complete, timeout restored to 5min")

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            self.active_ids.clear()
            self.log_ok("Disconnected")

    # -- Key dispatch --

    async def handle_key(self, key: str) -> bool:
        """Handle a keypress. Returns False to quit."""
        handlers = {
            "n": self.add_next_notification,
            "N": self.add_all_notifications,
            "d": self.dismiss_newest,
            "D": self.dismiss_oldest,
            "c": self.clear_all,
            "r": self.read_config,
            "+": self.brightness_up,
            "=": self.brightness_up,  # unshifted + on most keyboards
            "-": self.brightness_down,
            "0": self.brightness_min,
            "9": self.brightness_max,
            "s": self.sleep_quick,
            "S": self.sleep_default,
            "t": self.toggle_day_night,
            "1": self.scenario_burst,
            "2": self.scenario_fill_drain,
            "3": self.scenario_reconnect,
            "4": self.scenario_sleep,
            "x": self.disconnect,
        }

        if key == "q":
            return False
        if key == "?":
            print(HELP)
            return True

        handler = handlers.get(key)
        if handler:
            try:
                await handler()
            except Exception as e:
                self.log_err(f"Error: {e}")
        else:
            self.log_warn(f"Unknown key '{key}' — press ? for help")

        return True


def read_key() -> str:
    """Read a single keypress from stdin (raw mode)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


async def key_reader(tester: InteractiveTester):
    """Read keys in a thread and dispatch to the tester."""
    loop = asyncio.get_event_loop()
    while tester.running:
        key = await loop.run_in_executor(None, read_key)
        if key == "\x03":  # Ctrl+C
            tester.running = False
            break
        cont = await tester.handle_key(key)
        if not cont:
            tester.running = False
            break


async def main():
    tester = InteractiveTester()

    if not await tester.scan_and_connect():
        sys.exit(1)

    print(HELP)
    try:
        await tester.read_config()  # so toggles start from the device's real state
    except Exception as e:  # older firmware, or a flaky read: not fatal
        tester.log_warn(f"Could not read config: {e}")
    print("\n\033[1;36mReady! Press keys to send commands.\033[0m\n")

    try:
        await key_reader(tester)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        print("\n\033[1;36mCleaning up...\033[0m")
        await tester.disconnect()
        print("\033[1;36mBye!\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
