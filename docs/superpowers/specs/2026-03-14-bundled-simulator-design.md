# Bundled Simulator Design

Bundle the native simulator inside the macOS Menu Bar app so users without hardware can see the animated Clawd display on their desktop.

## Context

The simulator (`clawd-tank-sim`) compiles the same firmware source files against SDL2 and runs as a standalone native binary. The menu bar app (`Clawd Tank.app`) is a Python/rumps application packaged via py2app. Currently the simulator must be built and launched separately — users run `clawd-tank-sim --listen` and toggle "Enable Simulator" in the menu bar to connect over TCP.

This design eliminates that manual step: the simulator binary ships inside the `.app` bundle and the menu bar app manages its lifecycle directly.

## Distribution

- GitHub release with a zipped `.app` — no DMG, no code signing, no notarization
- Users download, unzip, drag to Applications, right-click → Open on first launch (Gatekeeper bypass)
- macOS 13+ (Ventura), arm64 only

## Simulator Build Changes

### Static SDL2 Linking

Add a `STATIC_SDL2` CMake option to `simulator/CMakeLists.txt`:

- `STATIC_SDL2=ON`: downloads a pinned SDL2 release tarball (>= 2.0.16 for `SDL_SetWindowAlwaysOnTop` support), builds it as a static library, and links it into the simulator binary. Produces a self-contained arm64 executable with no dynamic dependencies beyond system frameworks (Cocoa, IOKit, CoreAudio, CoreVideo, Metal, Carbon, ForceFeedback, AudioToolbox, libSystem).
- `STATIC_SDL2=OFF` (default): uses system/Homebrew SDL2 via `find_package`, preserving the current local development workflow.

SDL2 is zlib-licensed — static linking is permitted.

### New TCP Commands

The simulator's TCP listener (`--listen`) gains three new JSON actions for window management:

| Action | Payload | Effect |
|---|---|---|
| `show_window` | `{"action":"show_window"}` | Shows the SDL window (`SDL_ShowWindow`) |
| `hide_window` | `{"action":"hide_window"}` | Hides the SDL window (`SDL_HideWindow`), process stays alive |
| `set_window` | `{"action":"set_window","pinned":true\|false}` | Toggles always-on-top |

**Command routing in `sim_socket.c`:** Window commands cannot go through `sim_ble_parse_json()` because they don't produce a `ble_evt_t`. Instead, `sim_socket.c` recognizes these actions before calling the BLE parser and dispatches them to a new window command queue (similar to the existing `ble_evt_t` queue). The main thread drains this queue in the event loop and calls SDL functions (`SDL_ShowWindow`, `SDL_HideWindow`, `SDL_SetWindowAlwaysOnTop`) on the main thread where SDL operations are safe.

### Bidirectional TCP Communication

The current TCP protocol is primarily unidirectional (menu app → simulator), with the only response being `read_config`. This design adds simulator → menu app events.

**Simulator side:** A new outbound message function in `sim_socket.c` writes JSON lines to the client socket. Access to the client fd is mutex-guarded (the socket thread already uses a mutex for the inbound queue). The main thread calls this function when it needs to notify the menu app of state changes.

**SimClient side:** `SimClient` gains a background reader task that continuously reads lines from the TCP connection. When it receives an event (e.g., `{"event":"window_hidden"}`), it invokes a callback on the `SimProcessManager`. The existing `read_config()` request-response pattern is preserved by using a response future that the background reader fulfills when it sees a config response.

**Events sent from simulator to menu app:**

| Event | Payload | Trigger |
|---|---|---|
| `window_hidden` | `{"event":"window_hidden"}` | User closes the SDL window (Cmd+W / red X) |

### Window Close Behavior

When the user closes the SDL window (SDL_QUIT / Cmd+W), instead of exiting the process:

1. The window is hidden (`SDL_HideWindow`)
2. The render loop pauses (skips `SDL_RenderPresent` and texture updates while hidden to save CPU; LVGL ticks continue so animation state stays current)
3. The process continues running, accepting TCP commands
4. A `{"event":"window_hidden"}` JSON message is sent to the menu app via the outbound TCP channel
5. On `show_window`, the render loop resumes from the current animation state

### Launch Flag Changes

- The simulator window is already always borderless (unconditional `SDL_WINDOW_BORDERLESS` in `sim_display.c`). Add a `--bordered` flag to override this for development use.
- New `--hidden` flag: initializes SDL and creates the window but starts it hidden (`SDL_HideWindow` immediately after creation). The menu app uses this combined with the `show_window` TCP command for controlled initial launch.
- `--hidden` and `--headless` are mutually exclusive. `--headless` skips SDL initialization entirely (no window, no renderer). `--hidden` initializes SDL fully but hides the window. If both are specified, `--headless` takes precedence.

## Menu Bar App Changes

### Transport Architecture

Both BLE and Simulator become independently enable/disable-able transports with preferences:

```json
{
  "ble_enabled": true,
  "sim_enabled": true,
  "sim_window_visible": true,
  "sim_always_on_top": true
}
```

**Preferences read-modify-write:** `save_preferences()` currently overwrites the entire file with the provided dict. This must change to a read-modify-write pattern: load existing preferences from disk, merge the changed key(s), write back. This prevents toggling one preference from clobbering others. `load_preferences()` must also merge with `DEFAULTS` so that missing keys (from older preference files) get their default values rather than being absent.

On startup, the daemon reads preferences and only creates transports that are enabled. Toggling "Enabled" in a submenu:

- **Enable**: creates the transport client, adds it to the daemon, starts connecting. For simulator: also spawns the process.
- **Disable**: removes the transport from the daemon. For simulator: kills the subprocess.

### Simulator Process Manager

New class `SimProcessManager` in `clawd_tank_daemon/`:

- **Spawning**: launches `clawd-tank-sim --listen --hidden` as a subprocess
- **Binary discovery**: looks for the binary using `NSBundle.mainBundle().bundlePath` + `/Contents/MacOS/clawd-tank-sim` when running inside a `.app` bundle, falls back to `os.path.join(os.path.dirname(sys.executable), 'clawd-tank-sim')`, then `shutil.which('clawd-tank-sim')` for development
- **Window control**: sends show/hide/pinned TCP commands via `SimClient.send_command()` (new method for non-notification JSON payloads)
- **Event handling**: receives `window_hidden` events from `SimClient`'s background reader and updates the menu bar toggle state
- **Process monitoring**: if the process crashes, logs it and updates status. No auto-restart.
- **Port conflict handling**: before spawning, attempts a TCP connect to the target port. If something is already listening, logs a warning and connects `SimClient` to the existing instance without spawning a new process. This handles the case where a standalone `clawd-tank-sim --listen` is already running.
- **Clean shutdown**: SIGTERM → wait → SIGKILL on disable or app quit

### SimClient Changes

`SimClient` gains:

- **`send_command(payload: dict)`**: sends an arbitrary JSON command (used for `show_window`, `hide_window`, `set_window`). Distinct from `write_notification()` which wraps payloads in notification format.
- **Background reader task**: continuously reads lines from the TCP connection. Dispatches events to a callback. Routes `read_config` responses to a future for the existing request-response pattern.
- **`on_event` callback**: set by `SimProcessManager` to receive simulator events like `window_hidden`.

### Menu Bar UI

Transport-centric layout with submenus:

**Top level:**
```
BLE  ● Connected   ▸
Simulator  ● Running   ▸
─────────────────────────
Brightness              ▸
Session Timeout         ▸
─────────────────────────
Install Claude Code Hooks
Launch at Login
─────────────────────────
Quit Clawd Tank
```

**BLE submenu (enabled):**
```
Status: Connected
✓ Enabled
Reconnect
```

**BLE submenu (disabled):**
```
Status: Disabled
  Enabled
```

**Simulator submenu (enabled):**
```
Status: Running
✓ Enabled
✓ Show Window
✓ Always on Top
```

**Simulator submenu (disabled):**
```
Status: Disabled
  Enabled
```

**Simulator status states:**

| State | Status text | Indicator |
|---|---|---|
| Process starting | Launching... | ● yellow |
| Process running, TCP connecting | Connecting... | ● yellow |
| Process running, TCP connected | Running | ● green |
| Process crashed / not running | Stopped | ● red |
| Disabled | Disabled | ○ gray |

**BLE status states:**

| State | Status text | Indicator |
|---|---|---|
| Scanning / connecting | Connecting... | ● yellow |
| Connected | Connected | ● green |
| Disabled | Disabled | ○ gray |

**Menu bar icon**: reflects aggregate state — connected crab if any transport is connected, disconnected crab if none are, notification crab if there are active notifications. Per-transport detail lives in the submenus.

### Defaults

- BLE: enabled
- Simulator: enabled, window visible, always on top
- Both transports active out of the box for the best first-run experience

### Window Behavior

- Simulator window is always borderless (no title bar) — this is existing behavior
- Always on top by default, toggleable from the simulator submenu
- Closing the SDL window (red X / Cmd+W) hides the window — process stays alive, same as toggling "Show Window" off. Menu toggle updates via the `window_hidden` TCP event.
- Show/hide controlled via the simulator submenu, which sends `show_window`/`hide_window` TCP commands
- When hidden, the render loop pauses to save CPU. Animation state stays current via LVGL ticks.

### Both Transports Disabled

No special handling. The submenu status shows "Disabled" for each, and the menu bar icon shows the disconnected crab. User can re-enable at any time.

## App Bundle Structure

```
Clawd Tank.app/
  Contents/
    MacOS/
      Clawd Tank          (py2app stub launcher)
      python              (Python interpreter)
      clawd-tank-sim      (simulator binary — NEW)
    Frameworks/
      libpython3.11.dylib, libcrypto, libssl, etc.
    Resources/
      lib/python3.11/
        clawd_tank_daemon/
        clawd_tank_menubar/
      icons/
      AppIcon.icns
    Info.plist
```

The simulator binary is placed in `Contents/MacOS/` per macOS conventions for executables.

## GitHub Actions Workflow

Triggered on tag push (e.g., `v1.2.0`):

1. **Build simulator** — checkout repo, `cmake -B build -DSTATIC_SDL2=ON`, `cmake --build build`. Produces self-contained `clawd-tank-sim` arm64 binary.
2. **Build menu app** — `cd host && python setup.py py2app`. Produces `Clawd Tank.app`.
3. **Inject simulator** — `cp simulator/build/clawd-tank-sim "host/dist/Clawd Tank.app/Contents/MacOS/"`.
4. **Package** — `cd host/dist && zip -r clawd-tank-macos-arm64.zip "Clawd Tank.app"`.
5. **Release** — attach zip to the GitHub release.

macOS runner: `macos-14` (arm64 Apple Silicon).

## Migration

**Existing users upgrading:**

- **Preferences**: `load_preferences()` changes to merge loaded preferences with `DEFAULTS`, so missing keys get default values. `save_preferences()` changes to read-modify-write. Existing `sim_enabled: false` is preserved on disk. New keys (`ble_enabled`, `sim_window_visible`, `sim_always_on_top`) get their defaults via the merge.
- **Note**: the default for `sim_enabled` changes from `false` to `true`, but existing users who previously toggled the simulator already have `{"sim_enabled": false}` on disk, so their behavior is unchanged. New users get both transports enabled.
- **Hooks**: unchanged — same script, same settings.
- **Launchd plist**: if the user switches from a dev install to the bundled `.app`, the plist's `ProgramArguments` will point to the old Python interpreter path. On startup, detect if the plist exists but points to a different executable than the current one, and prompt the user to re-enable "Launch at Login".
- **Menu structure**: changes from flat to submenus. No user data affected.

## Out of Scope

- Code signing / notarization (future work)
- DMG with drag-to-Applications visual
- Auto-update mechanism
- Intel (x86_64) support
- Windows / Linux builds
- Simulator auto-restart on crash
