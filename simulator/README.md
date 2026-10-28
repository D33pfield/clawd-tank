# Clawd Tank Simulator

Native macOS simulator for the Clawd LVGL UI. Runs the same firmware rendering code (`scene.c`, `notification_ui.c`, `ui_manager.c`, `notification.c`) without hardware, using shim headers to replace ESP-IDF APIs.

Supports two modes:
- **Interactive** — SDL2 window with keyboard controls
- **Headless** — No window, framebuffer-only with PNG screenshot capture

Both modes support a **TCP listener** (`--listen`) that accepts the same JSON protocol as BLE, enabling the daemon to drive the simulator over TCP.

## Prerequisites

- macOS with Xcode command line tools
- CMake 3.16+
- SDL2 (`brew install sdl2`)

## Build

```bash
cd simulator
cmake -B build
cmake --build build
```

## Usage

### Interactive mode (default)

```bash
./build/clawd-tank-sim

# With TCP listener for daemon connection
./build/clawd-tank-sim --listen
```

Opens a 640x344 window (320x172 scaled 2x). Keyboard shortcuts:

| Key | Action |
|-----|--------|
| `c` | Connect (BLE) |
| `d` | Disconnect |
| `n` | Add sample notification (cycles through 6 presets) |
| `1`-`8` | Dismiss notification by index |
| `x` | Clear all notifications |
| `b` | BOOT button (wakes a sleeping display, else clears notifications) |
| `s` | Save screenshot to current directory |
| `z` | Force sleep state |
| `q` / `Esc` | Quit |

Change window scale with `--scale`:

```bash
./build/clawd-tank-sim --scale 4
```

### Headless mode

```bash
./build/clawd-tank-sim --headless \
  --events 'connect; wait 500; notify "GitHub" "PR merged"; wait 2000; disconnect' \
  --screenshot-dir ./shots/ \
  --screenshot-on-event
```

### CLI options

| Option | Description |
|--------|-------------|
| `--headless` | Run without SDL2 window |
| `--scale <N>` | Window scale factor (default: 2) |
| `--pinned` | Keep window always on top |
| `--events '<commands>'` | Inline event string (semicolon-separated) |
| `--scenario <file.json>` | Load events from JSON scenario file |
| `--screenshot-dir <dir>` | Output directory for PNG screenshots |
| `--screenshot-interval <ms>` | Capture a screenshot every N ms |
| `--screenshot-on-event` | Capture a screenshot after each event |
| `--run-ms <ms>` | Total simulation duration (headless only; defaults to last event + 500ms) |
| `--listen [port]` | Start TCP listener for daemon connection (default: 19872) |
| `--help` | Show help |

## Inline events

Semicolon-separated commands with `wait` for timing:

| Command | Description |
|---------|-------------|
| `connect` | Simulate BLE connect |
| `disconnect` | Simulate BLE disconnect |
| `notify "project" "message"` | Add a notification |
| `dismiss <index>` | Dismiss notification by order of addition (0-based) |
| `clear` | Clear all notifications |
| `wait <ms>` | Advance simulated time |

Example — full lifecycle:

```bash
./build/clawd-tank-sim --headless \
  --events 'connect; wait 200; notify "GitHub" "PR merged"; wait 500; notify "Slack" "Deploy done"; wait 3000; dismiss 0; wait 1000; clear; wait 500; disconnect' \
  --screenshot-dir ./shots/ \
  --screenshot-interval 250 --screenshot-on-event
```

## JSON scenarios

For complex sequences, use a JSON file:

```json
[
    {"time_ms": 0,    "event": "connect"},
    {"time_ms": 500,  "event": "notify", "project": "GitHub", "message": "PR #42 merged"},
    {"time_ms": 1500, "event": "notify", "project": "Slack",  "message": "Deploy complete"},
    {"time_ms": 3000, "event": "dismiss", "index": 0},
    {"time_ms": 5000, "event": "clear"},
    {"time_ms": 6000, "event": "disconnect"}
]
```

Run it:

```bash
./build/clawd-tank-sim --headless \
  --scenario scenarios/demo.json \
  --screenshot-dir ./shots/ \
  --screenshot-on-event
```

A demo scenario is included at `scenarios/demo.json`.

## Screenshot naming

- Periodic: `frame_NNNNNN.png` (N = simulated time in ms)
- Event-triggered: `event_NNNNNN_<type>.png` (type = connect, disconnect, notify, dismiss, clear)

## Architecture

```
simulator/
  shims/              ESP-IDF header stubs (esp_log, FreeRTOS, ble_service)
  cjson/              cJSON library for scenario parsing
  scenarios/          JSON scenario files
  sim_display.c/h     LVGL display backend (framebuffer + SDL2)
  sim_events.c/h      Event injection (inline + JSON)
  sim_screenshot.c/h  PNG capture via stb_image_write
  sim_ble_parse.c/h   Shared JSON parser for TCP bridge
  sim_socket.c/h      TCP listener with thread-safe event queue
  sim_main.c          CLI, main loops (headless + interactive)
  lv_conf.h           LVGL config for native build
  CMakeLists.txt      Build system
```

The simulator compiles the actual firmware source files from `firmware/main/` unmodified. Shim headers in `simulator/shims/` shadow ESP-IDF includes so they resolve at compile time without the ESP-IDF toolchain.

## TCP Listener

The `--listen` flag starts a TCP server that accepts the same newline-delimited JSON protocol used over BLE. This enables the full Claude Code → daemon → display pipeline without hardware.

```bash
# Start simulator with TCP listener
./build/clawd-tank-sim --listen

# Custom port
./build/clawd-tank-sim --listen 12345

# Headless + listen (runs indefinitely, daemon-driven)
./build/clawd-tank-sim --headless --listen
```

The daemon connects via CLI flags (`--sim`, `--sim-only`) or via the "Enable Simulator" toggle in the macOS menu bar app. Default port: 19872.

The TCP listener accepts one client at a time. When connected, the daemon sends JSON commands (`add`, `dismiss`, `clear`, `set_time`, `set_status`, `read_config`, `write_config`) and the simulator processes them identically to BLE events. The listener uses a background pthread with a mutex-guarded ring buffer queue to maintain LVGL thread safety.

### `set_status` command

The daemon sends `set_status` to control Clawd's working animation based on active Claude Code sessions:

```json
{"action": "set_status", "status": "thinking"}
```

Valid status values: `sleeping`, `idle`, `thinking`, `working_1`, `working_2`, `working_3`, `confused`, `sweeping`.
