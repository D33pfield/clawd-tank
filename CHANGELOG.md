# Changelog

## [1.2.1] - 2026-03-14

### Added

- **Subagent tracking** — `SubagentStart`/`SubagentStop` hooks track active Claude Code subagents per session. Sessions with active subagents are never evicted and count as "working", preventing Clawd from sleeping during long-running agent tasks.
- **Session state persistence** — Session state saved atomically to `~/.clawd-tank/sessions.json` on structural changes (state transitions, subagent add/remove). Daemon loads saved state on startup with immediate stale eviction, so restarting the app preserves the correct animation.
- **Simulator logging** — Simulator stdout/stderr routed through Python logger to unified `clawd-tank.log` with `[clawd-tank.sim-process]` tag.
- **Build script** — `host/build.sh` automates static simulator build, py2app, binary bundling, and optional install (`--install`).
- **Version logging** — App version logged on startup for easier debugging.

### Changed

- **Version numbering on master** — Commit count now measured against `origin/master` (unpushed commits) instead of local `master` (always 0).
- **CI workflow** — `build-macos-app.yml` now builds the static simulator and bundles it into the `.app`, matching `release.yml`.

### Fixed

- **Quit handler race condition** — Sim transport is now removed from daemon before killing the process, avoiding double-disconnect. Sim process is SIGKILL'd immediately instead of waiting 3s for SIGTERM.
- **Session file double-close** — Fixed fd double-close in `save_sessions` error path that could leave orphaned temp files.
- **Test pollution** — Added `conftest.py` with autouse fixture to redirect session persistence to temp dirs, preventing tests from writing to real `~/.clawd-tank/sessions.json`.

## [1.1.0] - 2026-03-14

### Added

- **Session-aware working animations** — Clawd now shows real-time animation states driven by Claude Code session hooks. The tank acts as a workload meter reflecting what Claude is doing across all active sessions.
- **6 new sprite animations** — thinking (tapping chin with thought bubble), typing (frantic keyboard work), juggling (tossing data packets), building (hammering on anvil), confused (looking around with question marks), sweeping (push broom, oneshot for context compaction).
- **Intensity tiers** — Animation scales with concurrent session count: 1 session working = typing, 2 sessions = juggling, 3+ sessions = building.
- **Session state tracking** — Daemon maintains per-session state (`registered → thinking → working → idle → confused`) and computes a single display state via priority resolution.
- **3 new Claude Code hooks** — `SessionStart`, `PreToolUse`, `PreCompact` registered alongside existing hooks.
- **`set_status` BLE/TCP action** — New protocol command for daemon to control device animation state directly.
- **Fallback animation mechanism** — Oneshot animations (alert, happy, sweeping) now return to the current working animation instead of always idle.
- **Simulator `--pinned` flag** — Keeps the window always on top of other windows.
- **Simulator auto-focus** — Window comes to the front on launch.
- **Hook migration detection** — Install button detects outdated hooks and allows reinstallation.

### Changed

- **Sleep model** — Replaced firmware timer-based sleep (5-minute idle) with daemon-driven session-based sleep. No sessions = sleeping. Configurable staleness timeout (default 10 minutes) evicts dead sessions.
- **"Sleep Timeout" → "Session Timeout"** — Menu bar label renamed to reflect new semantics.
- **Default simulator scale** — Changed from 3x to 2x (640×344 window).
- **Clock display** — Now visible in all full-width states (idle, thinking, working), not just idle.
- **`daemon_message_to_ble_payload()`** — Returns `Optional[str]` instead of `str`; session-internal events return `None`.

### Fixed

- **Simulator shutdown freeze** — Fixed hang on exit when a TCP client was connected by closing the client socket during shutdown.
- **Hook reinstallation blocked** — `are_hooks_installed()` now checks all required hooks are present, not just that any hook uses the script.

## [1.0.0] - 2026-03-12

Initial release.

- ESP32-C6 firmware with 5 animated sprites (idle, alert, happy, sleeping, disconnected)
- BLE GATT server for notification management (add/dismiss/clear/set_time)
- NVS-backed config store (brightness, sleep timeout)
- LVGL 9.5 notification card UI with auto-rotation and hero expansion
- macOS menu bar app with daemon control, device config, and hook installer
- Python async daemon with multi-transport (BLE + TCP simulator)
- Native macOS simulator (SDL2) with TCP listener, screenshots, and headless mode
- RLE sprite compression pipeline (svg2frames.py + png2rgb565.py)
- 23 C tests, 68 Python tests
