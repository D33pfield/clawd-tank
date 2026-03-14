# Bundled Simulator Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bundle the native simulator inside the macOS Menu Bar app so users without hardware can see the animated Clawd display.

**Architecture:** The simulator binary (C/SDL2) is statically linked and placed inside the `.app` bundle. The menu bar app (Python/rumps) manages its lifecycle as a subprocess and communicates via the existing TCP protocol, extended with window management commands and bidirectional events.

**Tech Stack:** C11, SDL2 (static), CMake FetchContent, Python 3.11, asyncio, rumps, py2app, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-03-14-bundled-simulator-design.md`

---

## File Map

### New files
- `host/clawd_tank_daemon/sim_process.py` — SimProcessManager: subprocess lifecycle, binary discovery, window commands
- `host/tests/test_sim_process.py` — Tests for SimProcessManager
- `host/tests/test_preferences.py` — Tests for preferences read-modify-write
- `.github/workflows/release.yml` — GitHub Actions release workflow

### Modified files
- `simulator/CMakeLists.txt` — Add `STATIC_SDL2` option with FetchContent
- `simulator/sim_socket.c` — Window command queue, outbound event sending
- `simulator/sim_socket.h` — New public API: window command types, send_event, process_window_cmds
- `simulator/sim_display.c` — Show/hide window, hidden flag, render pause
- `simulator/sim_display.h` — New API: show/hide/is_hidden
- `simulator/sim_ble_parse.c` — Recognize window actions, return new code
- `simulator/sim_ble_parse.h` — Document return code 3 for window commands
- `simulator/sim_main.c` — `--hidden`, `--bordered` flags; window command processing in main loops; SDL_QUIT hides instead of quits
- `host/clawd_tank_daemon/sim_client.py` — `send_command()`, background reader task, event callback
- `host/clawd_tank_menubar/preferences.py` — Read-modify-write, merge with DEFAULTS
- `host/clawd_tank_menubar/app.py` — Submenu UI, transport enable/disable, SimProcessManager integration
- `host/clawd_tank_daemon/daemon.py` — Preference-driven transport creation
- `host/setup.py` — No changes needed (simulator binary injected post-build)
- `host/tests/test_sim_client.py` — Tests for send_command, background reader, event callback

---

## Chunk 1: Simulator — Window Management Infrastructure

### Task 1: Preferences Read-Modify-Write

**Files:**
- Modify: `host/clawd_tank_menubar/preferences.py`
- Create: `host/tests/test_preferences.py`

- [ ] **Step 1: Write failing tests for preferences**

```python
# host/tests/test_preferences.py
"""Tests for preferences read-modify-write and defaults merging."""

import json
import pytest
from pathlib import Path
from clawd_tank_menubar.preferences import load_preferences, save_preferences, DEFAULTS


@pytest.fixture
def prefs_file(tmp_path):
    return tmp_path / "preferences.json"


def test_load_returns_defaults_when_missing(prefs_file):
    result = load_preferences(prefs_file)
    assert result == DEFAULTS


def test_load_merges_missing_keys_with_defaults(prefs_file):
    """Old preferences file with only sim_enabled should get new default keys."""
    prefs_file.write_text(json.dumps({"sim_enabled": False}))
    result = load_preferences(prefs_file)
    assert result["sim_enabled"] is False
    assert result["ble_enabled"] == DEFAULTS["ble_enabled"]
    assert result["sim_window_visible"] == DEFAULTS["sim_window_visible"]
    assert result["sim_always_on_top"] == DEFAULTS["sim_always_on_top"]


def test_save_preserves_existing_keys(prefs_file):
    """Saving one key should not clobber others."""
    prefs_file.write_text(json.dumps({"sim_enabled": True, "ble_enabled": False}))
    save_preferences(path=prefs_file, updates={"sim_window_visible": False})
    result = json.loads(prefs_file.read_text())
    assert result["sim_enabled"] is True
    assert result["ble_enabled"] is False
    assert result["sim_window_visible"] is False


def test_save_creates_file_if_missing(prefs_file):
    save_preferences(path=prefs_file, updates={"ble_enabled": False})
    result = json.loads(prefs_file.read_text())
    assert result["ble_enabled"] is False
    # Other keys should get defaults from merge
    loaded = load_preferences(prefs_file)
    assert loaded["sim_enabled"] == DEFAULTS["sim_enabled"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_preferences.py -v`
Expected: FAIL — `save_preferences` doesn't accept `updates` kwarg, `DEFAULTS` is incomplete

- [ ] **Step 3: Implement preferences changes**

```python
# host/clawd_tank_menubar/preferences.py
"""Persistent preferences for the Clawd Tank menubar app."""

import json
import logging
from pathlib import Path

logger = logging.getLogger("clawd-tank.menubar")

DEFAULTS = {
    "ble_enabled": True,
    "sim_enabled": True,
    "sim_window_visible": True,
    "sim_always_on_top": True,
}
PREFS_PATH = Path.home() / ".clawd-tank" / "preferences.json"


def load_preferences(path: Path = PREFS_PATH) -> dict:
    """Load preferences from disk, merged with defaults for missing keys."""
    result = dict(DEFAULTS)
    try:
        stored = json.loads(path.read_text())
        result.update(stored)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return result


def save_preferences(path: Path = PREFS_PATH, updates: dict = None) -> None:
    """Read-modify-write: load existing, merge updates, save back."""
    if updates is None:
        updates = {}
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    try:
        existing = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    existing.update(updates)
    path.write_text(json.dumps(existing, indent=2) + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_preferences.py -v`
Expected: PASS

- [ ] **Step 5: Update callers of save_preferences**

In `host/clawd_tank_menubar/app.py`, find the call to `save_preferences` in `_on_toggle_simulator` and update it to use the new `updates` kwarg:

Old pattern: `save_preferences(prefs={"sim_enabled": self._sim_toggle.state})`
New pattern: `save_preferences(updates={"sim_enabled": self._sim_toggle.state})`

Search for all calls to `save_preferences` in the codebase and update them.

- [ ] **Step 6: Update existing preference tests in test_menubar.py**

The existing tests at `host/tests/test_menubar.py:81-98` assert against the old DEFAULTS `{"sim_enabled": False}`. Update them to match the new DEFAULTS:

```python
def test_load_preferences_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        prefs = load_preferences(Path(tmpdir) / "prefs.json")
        assert prefs == {"ble_enabled": True, "sim_enabled": True, "sim_window_visible": True, "sim_always_on_top": True}

def test_save_and_load_preferences():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "subdir" / "prefs.json"
        save_preferences(path=path, updates={"sim_enabled": False})
        prefs = load_preferences(path)
        assert prefs["sim_enabled"] is False
        assert prefs["ble_enabled"] is True  # default

def test_load_preferences_malformed_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "prefs.json"
        path.write_text("not json{{{")
        prefs = load_preferences(path)
        assert prefs == {"ble_enabled": True, "sim_enabled": True, "sim_window_visible": True, "sim_always_on_top": True}
```

- [ ] **Step 7: Run full test suite**

Run: `cd host && .venv/bin/pytest -v`
Expected: All tests pass

- [ ] **Step 8: Commit**

```bash
git add host/clawd_tank_menubar/preferences.py host/tests/test_preferences.py host/clawd_tank_menubar/app.py host/tests/test_menubar.py
git commit -m "feat(preferences): read-modify-write with defaults merging"
```

---

### Task 2: Simulator — Window Show/Hide in sim_display

**Files:**
- Modify: `simulator/sim_display.c`
- Modify: `simulator/sim_display.h`

- [ ] **Step 1: Add new API to sim_display.h**

Add after the existing `sim_display_set_pinned` declaration (line 35):

```c
/** Show the SDL window (no-op if headless). */
void sim_display_show_window(void);

/** Hide the SDL window (no-op if headless). */
void sim_display_hide_window(void);

/** Returns true if the window is currently hidden. */
bool sim_display_is_hidden(void);

/** Reset the quit flag (used when hiding instead of quitting in listen mode). */
void sim_display_clear_quit(void);
```

Also change the `sim_display_init` signature to accept a `bordered` parameter:

```c
lv_display_t *sim_display_init(bool headless, int scale, bool bordered);
```

- [ ] **Step 2: Implement all new functions in sim_display.c**

Add a static flag after the `s_quit` declaration (line 15):

```c
static bool s_hidden = false;
```

Change `sim_display_init` signature (line 83) to accept `bordered`:

```c
lv_display_t *sim_display_init(bool headless, int scale, bool bordered)
```

Change the SDL_CreateWindow call (line 105) to use the bordered flag:

```c
        Uint32 flags = bordered ? 0 : SDL_WINDOW_BORDERLESS;
        s_window = SDL_CreateWindow(
            "Clawd Tank Simulator",
            SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
            win_w, win_h,
            flags);
```

After creating the window, only set hit test for borderless:

```c
        if (!bordered) {
            SDL_SetWindowHitTest(s_window, hit_test_cb, NULL);
        }
```

Modify `sim_display_tick()` (line 156) to skip rendering when hidden:

```c
void sim_display_tick(void)
{
    if (s_headless || s_hidden) return;
    // ... rest unchanged
}
```

Add the show/hide/clear_quit implementations between the always-on-top section and the shutdown section (after `sim_display_set_pinned`, before `sim_display_shutdown`):

```c
/* ---- Show / Hide ---- */

void sim_display_show_window(void)
{
    if (!s_window) return;
    SDL_ShowWindow(s_window);
    SDL_RaiseWindow(s_window);
    s_hidden = false;
}

void sim_display_hide_window(void)
{
    if (!s_window) return;
    SDL_HideWindow(s_window);
    s_hidden = true;
}

bool sim_display_is_hidden(void)
{
    return s_hidden;
}

void sim_display_clear_quit(void)
{
    s_quit = false;
}
```

- [ ] **Step 3: Build and test**

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: Will fail because `sim_main.c` still calls `sim_display_init` with 2 args. This is expected — Task 4 will update the caller. For now, temporarily update the call in `sim_main.c` line 384 to pass `false` as the third arg to verify compilation:

```c
    sim_display_init(opt_headless, opt_scale, false);
```

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: Compiles cleanly

- [ ] **Step 4: Commit**

```bash
git add simulator/sim_display.c simulator/sim_display.h simulator/sim_main.c
git commit -m "feat(simulator): add window show/hide, bordered param, and clear_quit API"
```

---

### Task 3: Simulator — Window Command Queue in sim_socket

**Files:**
- Modify: `simulator/sim_socket.h`
- Modify: `simulator/sim_socket.c`
- Modify: `simulator/sim_ble_parse.c`
- Modify: `simulator/sim_ble_parse.h`

- [ ] **Step 1: Define window command types in sim_socket.h**

Add after the existing declarations:

```c
/* Window command types (dispatched from TCP, executed on main thread) */
typedef enum {
    SIM_WIN_CMD_SHOW,
    SIM_WIN_CMD_HIDE,
    SIM_WIN_CMD_SET_PINNED,
} sim_win_cmd_type_t;

typedef struct {
    sim_win_cmd_type_t type;
    bool pinned;  /* only used for SET_PINNED */
} sim_win_cmd_t;

// Drain window commands from the socket thread.
// Call from the main loop. Returns true if any command was processed.
// The callback is invoked for each command on the main thread.
bool sim_socket_process_window_cmds(void (*handler)(const sim_win_cmd_t *cmd));

// Send a JSON event to the connected TCP client (main thread safe).
// Returns true on success.
bool sim_socket_send_event(const char *json_line);
```

- [ ] **Step 2: Recognize window actions in sim_ble_parse**

Update `sim_ble_parse.h` comment to document return code 3:

```c
// Returns 3 for window commands (show_window/hide_window/set_window — caller handles directly).
```

In `sim_ble_parse.c`, add before the final `else` block (before line 92):

```c
    } else if (strcmp(action->valuestring, "show_window") == 0 ||
               strcmp(action->valuestring, "hide_window") == 0 ||
               strcmp(action->valuestring, "set_window") == 0) {
        cJSON_Delete(json);
        return 3;
    }
```

- [ ] **Step 3: Implement window command queue in sim_socket.c**

Add a second ring buffer for window commands (similar pattern to the ble_evt_t queue). Add after the existing queue declarations (after line 25):

```c
/* ---- Window command queue ---- */
#define WIN_CMD_QUEUE_SIZE 8

static sim_win_cmd_t s_win_queue[WIN_CMD_QUEUE_SIZE];
static int s_win_head = 0;
static int s_win_tail = 0;
static int s_win_count = 0;
static pthread_mutex_t s_win_mutex = PTHREAD_MUTEX_INITIALIZER;

static bool win_queue_push(const sim_win_cmd_t *cmd) {
    pthread_mutex_lock(&s_win_mutex);
    if (s_win_count >= WIN_CMD_QUEUE_SIZE) {
        pthread_mutex_unlock(&s_win_mutex);
        return false;
    }
    s_win_queue[s_win_tail] = *cmd;
    s_win_tail = (s_win_tail + 1) % WIN_CMD_QUEUE_SIZE;
    s_win_count++;
    pthread_mutex_unlock(&s_win_mutex);
    return true;
}

static bool win_queue_pop(sim_win_cmd_t *out) {
    pthread_mutex_lock(&s_win_mutex);
    if (s_win_count == 0) {
        pthread_mutex_unlock(&s_win_mutex);
        return false;
    }
    *out = s_win_queue[s_win_head];
    s_win_head = (s_win_head + 1) % WIN_CMD_QUEUE_SIZE;
    s_win_count--;
    pthread_mutex_unlock(&s_win_mutex);
    return true;
}
```

- [ ] **Step 4: Handle window commands in handle_client**

Add a new `handle_window_action` function and wire it into `handle_client`. Add before `handle_client`:

```c
static void handle_window_action(const char *buf, uint16_t len) {
    cJSON *json = cJSON_ParseWithLength(buf, len);
    if (!json) return;

    cJSON *action = cJSON_GetObjectItem(json, "action");
    if (!action || !cJSON_IsString(action)) { cJSON_Delete(json); return; }

    sim_win_cmd_t cmd = {0};
    if (strcmp(action->valuestring, "show_window") == 0) {
        cmd.type = SIM_WIN_CMD_SHOW;
    } else if (strcmp(action->valuestring, "hide_window") == 0) {
        cmd.type = SIM_WIN_CMD_HIDE;
    } else if (strcmp(action->valuestring, "set_window") == 0) {
        cJSON *pinned = cJSON_GetObjectItem(json, "pinned");
        cmd.type = SIM_WIN_CMD_SET_PINNED;
        cmd.pinned = pinned && cJSON_IsTrue(pinned);
    } else {
        cJSON_Delete(json);
        return;
    }
    cJSON_Delete(json);

    if (!win_queue_push(&cmd)) {
        printf("[tcp] Window command queue full, dropping\n");
    }
}
```

In `handle_client`, add `rc == 3` handling after the `rc == 2` case (after line 135):

```c
                } else if (rc == 3) {
                    /* Window command — enqueue for main thread */
                    handle_window_action(line_start, (uint16_t)line_len);
                }
```

- [ ] **Step 5: Implement sim_socket_process_window_cmds and sim_socket_send_event**

Add before `sim_socket_shutdown`:

```c
bool sim_socket_process_window_cmds(void (*handler)(const sim_win_cmd_t *cmd)) {
    bool any = false;
    sim_win_cmd_t cmd;
    while (win_queue_pop(&cmd)) {
        handler(&cmd);
        any = true;
    }
    return any;
}

bool sim_socket_send_event(const char *json_line) {
    pthread_mutex_lock(&s_client_mutex);
    if (s_client_fd < 0) {
        pthread_mutex_unlock(&s_client_mutex);
        return false;
    }
    int fd = s_client_fd;
    pthread_mutex_unlock(&s_client_mutex);

    size_t len = strlen(json_line);
    ssize_t sent = send(fd, json_line, len, 0);
    if (sent < 0) return false;
    send(fd, "\n", 1, 0);
    return true;
}
```

- [ ] **Step 6: Build and test**

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: Compiles cleanly

- [ ] **Step 7: Commit**

```bash
git add simulator/sim_socket.c simulator/sim_socket.h simulator/sim_ble_parse.c simulator/sim_ble_parse.h
git commit -m "feat(simulator): window command queue and outbound event API"
```

---

### Task 4: Simulator — Main Loop Integration

**Files:**
- Modify: `simulator/sim_main.c`

Note: `sim_display.h` and `sim_display.c` changes (bordered param, clear_quit, show/hide) were already done in Task 2.

- [ ] **Step 1: Add --hidden and --bordered flags**

Add new CLI options after `opt_pinned` (line 26):

```c
static bool     opt_hidden = false;
static bool     opt_bordered = false;
```

Add to `print_usage()`:

```c
        "  --bordered              Use window border (override default borderless)\n"
        "  --hidden                Start with window hidden\n"
```

Add to `parse_args()`:

```c
        } else if (strcmp(argv[i], "--hidden") == 0) {
            opt_hidden = true;
        } else if (strcmp(argv[i], "--bordered") == 0) {
            opt_bordered = true;
        }
```

Update `sim_display_init` call in `main()` (line 384) to pass bordered flag:

```c
    sim_display_init(opt_headless, opt_scale, opt_bordered);
```

After `parse_args()` in `main()`, add headless-takes-precedence logic:

```c
    if (opt_headless && opt_hidden) {
        opt_hidden = false;  /* --headless takes precedence */
    }
```

- [ ] **Step 2: Add window command handler and apply --hidden**

Add a static function before `run_interactive`:

```c
static void handle_window_cmd(const sim_win_cmd_t *cmd)
{
    switch (cmd->type) {
    case SIM_WIN_CMD_SHOW:
        sim_display_show_window();
        printf("[win] Window shown\n");
        break;
    case SIM_WIN_CMD_HIDE:
        sim_display_hide_window();
        printf("[win] Window hidden\n");
        break;
    case SIM_WIN_CMD_SET_PINNED:
        sim_display_set_pinned(cmd->pinned);
        printf("[win] Pinned=%s\n", cmd->pinned ? "true" : "false");
        break;
    }
}
```

In `main()`, after `sim_display_set_pinned` (after line 404), add:

```c
    /* 4c. Apply hidden mode */
    if (opt_hidden) {
        sim_display_hide_window();
    }
```

- [ ] **Step 3: Change SDL_QUIT and keyboard quit to hide when in listen mode**

In `handle_sdl_events()`, change the SDL_QUIT handler (line 301-303):

```c
        if (e.type == SDL_QUIT) {
            if (opt_listen_port > 0) {
                sim_display_hide_window();
                sim_socket_send_event("{\"event\":\"window_hidden\"}");
            } else {
                sim_display_set_quit();
            }
            return;
        }
```

Similarly, change the `q`/`Escape` key handlers (line 307-309):

```c
            case SDLK_q:
            case SDLK_ESCAPE:
                if (opt_listen_port > 0) {
                    sim_display_hide_window();
                    sim_socket_send_event("{\"event\":\"window_hidden\"}");
                } else {
                    sim_display_set_quit();
                }
                return;
```

- [ ] **Step 4: Rewrite run_interactive to handle hidden state**

Replace `run_interactive()` with:

```c
static void run_interactive(void)
{
    printf("[sim] Interactive mode (scale=%dx). Keys: c=connect d=disconnect n=notify 1-8=dismiss x=clear s=screenshot z=sleep q/esc=quit\n",
           opt_scale);

    while (!sim_display_should_quit()) {
        if (!sim_display_is_hidden()) {
            handle_sdl_events();
        } else {
            /* When hidden, still pump SDL events minimally */
            SDL_Event e;
            while (SDL_PollEvent(&e)) { /* discard */ }
        }

        /* Process scripted events if any (using wall time) */
        if (opt_events || opt_scenario) {
            sim_events_process(SDL_GetTicks());
        }

        /* Process TCP socket events */
        if (opt_listen_port > 0) {
            sim_socket_process();
            sim_socket_process_window_cmds(handle_window_cmd);
        }

        /* Advance simulated timers (drives RGB LED animation) */
        sim_timers_tick(TICK_MS);

        ui_manager_tick();

        if (!sim_display_is_hidden()) {
            sim_display_tick();  /* present to SDL window */
        }

        SDL_Delay(TICK_MS);
    }
}
```

Note: quit is still handled by `sim_display_should_quit()` — this returns true only for standalone mode (no listen). In listen mode, the SDL_QUIT and keyboard handlers in Step 3 hide instead of setting quit, so the loop continues.

- [ ] **Step 5: Process window commands in run_headless**

In `run_headless()`, add window command processing after `sim_socket_process()` (after line 158), guarded by listen port check:

```c
            /* Process window commands from TCP */
            if (opt_listen_port > 0) {
                sim_socket_process_window_cmds(handle_window_cmd);
            }
```

- [ ] **Step 6: Build and manual test**

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: Compiles cleanly

Manual test (use two terminal windows):
```bash
# Terminal 1: start simulator with listen and hidden
./simulator/build/clawd-tank-sim --listen --hidden

# Terminal 2: send show command (keep connection open with -q)
echo '{"action":"show_window"}' | nc -q 1 localhost 19872
# Window should appear.

# Close the simulator window (Cmd+W or click X).
# It should hide, not exit. Terminal 1 should show "[win] Window hidden" and stay alive.

# Show again:
echo '{"action":"show_window"}' | nc -q 1 localhost 19872
```

- [ ] **Step 7: Commit**

```bash
git add simulator/sim_main.c
git commit -m "feat(simulator): window lifecycle management via TCP commands"
```

---

### Task 5: Static SDL2 Linking

**Files:**
- Modify: `simulator/CMakeLists.txt`

- [ ] **Step 1: Add STATIC_SDL2 option with FetchContent**

Replace the SDL2 section of `CMakeLists.txt` (lines 6-7) with:

```cmake
# SDL2 — static or system
option(STATIC_SDL2 "Download and statically link SDL2" OFF)

if(STATIC_SDL2)
    include(FetchContent)
    set(SDL_SHARED OFF CACHE BOOL "" FORCE)
    set(SDL_STATIC ON CACHE BOOL "" FORCE)
    set(SDL_TEST OFF CACHE BOOL "" FORCE)
    set(SDL2_DISABLE_SDL2MAIN ON CACHE BOOL "" FORCE)
    FetchContent_Declare(
        SDL2
        URL https://github.com/libsdl-org/SDL/releases/download/release-2.30.10/SDL2-2.30.10.tar.gz
        URL_HASH SHA256=f59d23afc048498e498e75a53386be02e43a4e1fc0e4fc88cb10e02060cc9695
    )
    FetchContent_MakeAvailable(SDL2)
    # SDL2-static target provides the static library
    set(SDL2_TARGET SDL2-static)
else()
    find_package(SDL2 REQUIRED)
    set(SDL2_TARGET SDL2::SDL2)
endif()
```

Update the `target_link_libraries` (line 56-58):

```cmake
target_link_libraries(clawd-tank-sim PRIVATE
    lvgl
    ${SDL2_TARGET}
)
```

Update the Apple section (line 62-64) to add all required frameworks for static linking:

```cmake
if(APPLE)
    target_link_libraries(clawd-tank-sim PRIVATE
        "-framework Cocoa"
        "-framework IOKit"
        "-framework CoreAudio"
        "-framework CoreVideo"
        "-framework Metal"
        "-framework Carbon"
        "-framework ForceFeedback"
        "-framework AudioToolbox"
        "-framework GameController"
        "-framework CoreHaptics"
    )
endif()
```

Note: the extra frameworks are only needed for static linking but including them for dynamic linking is harmless — unused frameworks are not linked.

- [ ] **Step 2: Test static build**

Run:
```bash
cd simulator && cmake -B build-static -DSTATIC_SDL2=ON && cmake --build build-static
```
Expected: Downloads SDL2, builds static lib, links into `clawd-tank-sim`

Verify no SDL2 dynamic dependency:
```bash
otool -L simulator/build-static/clawd-tank-sim | grep -i sdl
```
Expected: No SDL2 dylib in output

- [ ] **Step 3: Test dynamic build still works**

Run:
```bash
cd simulator && cmake -B build && cmake --build build
```
Expected: Builds successfully using system SDL2

- [ ] **Step 4: Commit**

```bash
git add simulator/CMakeLists.txt
git commit -m "feat(simulator): add STATIC_SDL2 option for self-contained binary"
```

---

## Chunk 2: Host — SimClient, Process Manager, Menu UI

### Task 6: SimClient — send_command and Background Reader

**Files:**
- Modify: `host/clawd_tank_daemon/sim_client.py`
- Modify: `host/tests/test_sim_client.py`

- [ ] **Step 1: Write failing tests**

Add to `host/tests/test_sim_client.py`:

```python
@pytest.mark.asyncio
async def test_send_command():
    """send_command sends arbitrary JSON payloads."""
    received = []

    async def handler(reader, writer):
        while True:
            line = await reader.readline()
            if not line:
                break
            received.append(line.decode().strip())
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()

        result = await client.send_command({"action": "show_window"})
        assert result is True

        await client.disconnect()
        await asyncio.sleep(0.05)

    assert len(received) == 1
    assert json.loads(received[0])["action"] == "show_window"


@pytest.mark.asyncio
async def test_background_reader_receives_events():
    """Background reader should invoke on_event callback for unsolicited messages."""
    events = []

    async def handler(reader, writer):
        # Send an unsolicited event after a brief delay
        await asyncio.sleep(0.1)
        writer.write(b'{"event":"window_hidden"}\n')
        await writer.drain()
        # Keep connection open briefly
        await asyncio.sleep(0.5)
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port, on_event_cb=lambda e: events.append(e))
        await client.connect()
        await asyncio.sleep(0.3)

        assert len(events) == 1
        assert events[0]["event"] == "window_hidden"

        await client.disconnect()


@pytest.mark.asyncio
async def test_read_config_with_background_reader():
    """read_config should still work when background reader is active."""
    async def handler(reader, writer):
        line = await reader.readline()
        req = json.loads(line.decode().strip())
        if req.get("action") == "read_config":
            writer.write(b'{"brightness":128,"sleep_timeout":300}\n')
            await writer.drain()
        await asyncio.sleep(0.5)
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()

        config = await client.read_config()
        assert config["brightness"] == 128

        await client.disconnect()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_sim_client.py::test_send_command tests/test_sim_client.py::test_background_reader_receives_events tests/test_sim_client.py::test_read_config_with_background_reader -v`
Expected: FAIL — `send_command` doesn't exist, `on_event_cb` not a valid parameter

- [ ] **Step 3: Implement SimClient changes**

Update `host/clawd_tank_daemon/sim_client.py`:

Add `on_event_cb` parameter to `__init__`:

```python
def __init__(
    self,
    host: str = "127.0.0.1",
    port: int = SIM_DEFAULT_PORT,
    on_disconnect_cb=None,
    on_connect_cb=None,
    on_event_cb=None,
    retry_interval: float = SIM_RETRY_INTERVAL,
):
    self._host = host
    self._port = port
    self._reader: asyncio.StreamReader | None = None
    self._writer: asyncio.StreamWriter | None = None
    self._on_disconnect_cb = on_disconnect_cb
    self._on_connect_cb = on_connect_cb
    self._on_event_cb = on_event_cb
    self._retry_interval = retry_interval
    self._lock = asyncio.Lock()
    self._reader_task: asyncio.Task | None = None
    self._config_response: asyncio.Future | None = None
```

Add `send_command` method:

```python
async def send_command(self, payload: dict) -> bool:
    """Send an arbitrary JSON command. Returns True on success."""
    return await self.write_notification(json.dumps(payload))
```

Add background reader that starts on connect:

```python
async def connect(self) -> None:
    """Connect to the simulator. Retries until successful."""
    if self._writer is not None:
        await self.disconnect()
    while True:
        try:
            logger.info("Connecting to simulator at %s:%d...", self._host, self._port)
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port
            )
            logger.info("Connected to simulator")
            self._reader_task = asyncio.create_task(self._background_reader())
            if self._on_connect_cb:
                self._on_connect_cb()
            return
        except (ConnectionRefusedError, OSError) as e:
            logger.debug("Simulator not available: %s, retrying...", e)
            await asyncio.sleep(self._retry_interval)

async def _background_reader(self) -> None:
    """Read lines from the TCP connection and dispatch events or config responses."""
    try:
        while self._reader and not self._reader.at_eof():
            line = await self._reader.readline()
            if not line:
                break
            try:
                data = json.loads(line.decode("utf-8").strip())
            except json.JSONDecodeError:
                continue

            # Config responses go to the waiting future.
            # We identify them by the presence of a pending future —
            # read_config is request-response, so a response arrives
            # only when we're waiting for one.
            if self._config_response and not self._config_response.done():
                self._config_response.set_result(data)
                continue

            # Everything else is an event
            if self._on_event_cb and "event" in data:
                self._on_event_cb(data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    except asyncio.CancelledError:
        return

    # If we get here, connection was lost
    self._handle_disconnect()
```

Update `read_config` to use a future:

```python
async def read_config(self) -> dict:
    """Request and read config from simulator. Returns empty dict on error."""
    async with self._lock:
        if not self.is_connected:
            return {}
        try:
            self._config_response = asyncio.get_event_loop().create_future()
            self._writer.write(b'{"action":"read_config"}\n')
            await self._writer.drain()
            result = await asyncio.wait_for(self._config_response, timeout=2.0)
            return result
        except (asyncio.TimeoutError, asyncio.CancelledError, OSError) as e:
            logger.error("Config read failed: %s", e)
            self._handle_disconnect()
            return {}
        finally:
            self._config_response = None
```

Update `disconnect` to cancel the reader task:

```python
async def disconnect(self) -> None:
    """Close the TCP connection."""
    if self._reader_task:
        self._reader_task.cancel()
        try:
            await self._reader_task
        except asyncio.CancelledError:
            pass
        self._reader_task = None
    if self._writer:
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass
    self._writer = None
    self._reader = None
```

- [ ] **Step 4: Run new tests**

Run: `cd host && .venv/bin/pytest tests/test_sim_client.py -v`
Expected: All tests pass (new and existing)

- [ ] **Step 5: Run full test suite**

Run: `cd host && .venv/bin/pytest -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add host/clawd_tank_daemon/sim_client.py host/tests/test_sim_client.py
git commit -m "feat(sim_client): add send_command, background reader, and event callback"
```

---

### Task 7: Simulator Process Manager

**Files:**
- Create: `host/clawd_tank_daemon/sim_process.py`
- Create: `host/tests/test_sim_process.py`

- [ ] **Step 1: Write failing tests**

```python
# host/tests/test_sim_process.py
"""Tests for SimProcessManager."""

import asyncio
import json
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from clawd_tank_daemon.sim_process import SimProcessManager


@pytest.mark.asyncio
async def test_find_binary_in_app_bundle():
    """Should find binary next to sys.executable."""
    mgr = SimProcessManager()
    with patch.object(os.path, "isfile", return_value=True):
        with patch("sys.executable", "/App.app/Contents/MacOS/python"):
            path = mgr._find_binary()
            assert path == "/App.app/Contents/MacOS/clawd-tank-sim"


@pytest.mark.asyncio
async def test_find_binary_fallback_to_which():
    """Should fall back to shutil.which if not next to executable."""
    mgr = SimProcessManager()
    with patch.object(os.path, "isfile", return_value=False):
        with patch("shutil.which", return_value="/usr/local/bin/clawd-tank-sim"):
            path = mgr._find_binary()
            assert path == "/usr/local/bin/clawd-tank-sim"


@pytest.mark.asyncio
async def test_find_binary_returns_none():
    """Should return None if binary not found anywhere."""
    mgr = SimProcessManager()
    with patch.object(os.path, "isfile", return_value=False):
        with patch("shutil.which", return_value=None):
            path = mgr._find_binary()
            assert path is None


@pytest.mark.asyncio
async def test_port_probe_detects_existing():
    """Should detect an existing listener on the port."""
    # Start a real server on a random port
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        mgr = SimProcessManager(port=port)
        assert await mgr._is_port_in_use() is True

    # After server stops
    mgr2 = SimProcessManager(port=port)
    assert await mgr2._is_port_in_use() is False


@pytest.mark.asyncio
async def test_on_window_event_callback():
    """window_hidden event should be forwarded to the callback."""
    events = []
    mgr = SimProcessManager(on_window_event=lambda e: events.append(e))
    mgr._handle_sim_event({"event": "window_hidden"})
    assert len(events) == 1
    assert events[0]["event"] == "window_hidden"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_sim_process.py -v`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Implement SimProcessManager**

```python
# host/clawd_tank_daemon/sim_process.py
"""Simulator process lifecycle manager."""

import asyncio
import logging
import os
import shutil
import signal
import sys
from typing import Callable, Optional

from .sim_client import SimClient, SIM_DEFAULT_PORT

logger = logging.getLogger("clawd-tank.sim-process")


class SimProcessManager:
    """Manages the simulator subprocess and its window state."""

    def __init__(
        self,
        port: int = SIM_DEFAULT_PORT,
        on_window_event: Optional[Callable] = None,
    ):
        self._port = port
        self._process: Optional[asyncio.subprocess.Process] = None
        self._client: Optional[SimClient] = None
        self._on_window_event = on_window_event

    def _find_binary(self) -> Optional[str]:
        """Locate the clawd-tank-sim binary."""
        # 1. Next to sys.executable (inside .app bundle)
        exe_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(exe_dir, "clawd-tank-sim")
        if os.path.isfile(candidate):
            return candidate

        # 2. NSBundle path (py2app)
        try:
            from Foundation import NSBundle
            bundle = NSBundle.mainBundle()
            if bundle:
                bundle_candidate = os.path.join(
                    bundle.bundlePath(), "Contents", "MacOS", "clawd-tank-sim"
                )
                if os.path.isfile(bundle_candidate):
                    return bundle_candidate
        except ImportError:
            pass

        # 3. PATH lookup (development)
        return shutil.which("clawd-tank-sim")

    async def _is_port_in_use(self) -> bool:
        """Check if something is already listening on our port."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self._port),
                timeout=1.0,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            return False

    def _handle_sim_event(self, event: dict) -> None:
        """Called by SimClient's background reader for simulator events."""
        if self._on_window_event:
            self._on_window_event(event)

    async def start(self) -> Optional[SimClient]:
        """Start the simulator process and return a connected SimClient.

        If the port is already in use, skip spawning and connect to existing.
        Returns None if the binary is not found.
        """
        if await self._is_port_in_use():
            logger.warning(
                "Port %d already in use, connecting to existing simulator", self._port
            )
        else:
            binary = self._find_binary()
            if not binary:
                logger.error("clawd-tank-sim binary not found")
                return None

            logger.info("Starting simulator: %s --listen %d --hidden", binary, self._port)
            self._process = await asyncio.create_subprocess_exec(
                binary, "--listen", str(self._port), "--hidden",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            # Start a background task to log stderr output
            asyncio.create_task(self._log_stderr())
            # Brief wait for the process to start listening
            await asyncio.sleep(0.3)

        self._client = SimClient(
            port=self._port,
            on_event_cb=self._handle_sim_event,
        )
        return self._client

    async def stop(self) -> None:
        """Stop the simulator process and disconnect."""
        if self._client:
            await self._client.disconnect()
            self._client = None

        if self._process and self._process.returncode is None:
            logger.info("Stopping simulator process (PID %d)", self._process.pid)
            self._process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("Simulator did not exit, sending SIGKILL")
                self._process.kill()
                await self._process.wait()
            self._process = None

    async def show_window(self) -> bool:
        """Send show_window command."""
        if self._client and self._client.is_connected:
            return await self._client.send_command({"action": "show_window"})
        return False

    async def hide_window(self) -> bool:
        """Send hide_window command."""
        if self._client and self._client.is_connected:
            return await self._client.send_command({"action": "hide_window"})
        return False

    async def set_pinned(self, pinned: bool) -> bool:
        """Send set_window pinned command."""
        if self._client and self._client.is_connected:
            return await self._client.send_command(
                {"action": "set_window", "pinned": pinned}
            )
        return False

    async def _log_stderr(self) -> None:
        """Read stderr from the simulator process and log it."""
        if not self._process or not self._process.stderr:
            return
        try:
            async for line in self._process.stderr:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.warning("[sim-stderr] %s", text)
        except (ValueError, asyncio.CancelledError):
            pass

    @property
    def is_running(self) -> bool:
        """Check if the simulator process is alive."""
        if self._process is None:
            return False
        return self._process.returncode is None
```

- [ ] **Step 4: Run tests**

Run: `cd host && .venv/bin/pytest tests/test_sim_process.py -v`
Expected: All pass

- [ ] **Step 5: Run full test suite**

Run: `cd host && .venv/bin/pytest -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add host/clawd_tank_daemon/sim_process.py host/tests/test_sim_process.py
git commit -m "feat: add SimProcessManager for simulator lifecycle"
```

---

### Task 8: Daemon — Preference-Driven Transport Creation

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py`

- [ ] **Step 1: Update daemon __init__ for menu bar mode**

In `daemon.py`, modify `__init__` (line 87-112): when `headless=False`, skip automatic transport creation. The menu bar app will add transports explicitly via `add_transport()`:

```python
def __init__(
    self,
    observer: Optional["DaemonObserver"] = None,
    headless: bool = True,
    sim_port: int = 0,
    sim_only: bool = False,
):
    self._transports: dict[str, TransportClient] = {}
    self._transport_queues: dict[str, asyncio.Queue] = {}

    if headless:
        # Headless (CLI) mode — create transports from args
        if not sim_only:
            ble = ClawdBleClient(
                on_disconnect_cb=lambda: self._on_transport_disconnect("ble"),
                on_connect_cb=lambda: self._on_transport_connect("ble"),
            )
            self._transports["ble"] = ble
            self._transport_queues["ble"] = asyncio.Queue()

        if sim_port > 0:
            sim = SimClient(
                port=sim_port,
                on_disconnect_cb=lambda: self._on_transport_disconnect("sim"),
                on_connect_cb=lambda: self._on_transport_connect("sim"),
            )
            self._transports["sim"] = sim
            self._transport_queues["sim"] = asyncio.Queue()
    # Menu bar mode (headless=False): transports added later via add_transport()

    self._sender_tasks: dict[str, asyncio.Task] = {}
    # ... rest unchanged
```

- [ ] **Step 2: Run tests**

Run: `cd host && .venv/bin/pytest tests/test_daemon.py tests/test_menubar.py -v`
Expected: All pass (existing tests use default `headless=True`, so behavior unchanged)

- [ ] **Step 3: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py
git commit -m "refactor(daemon): skip auto-transport creation in menu bar mode"
```

---

### Task 9: Menu Bar UI — BLE Submenu

**Files:**
- Modify: `host/clawd_tank_menubar/app.py`

- [ ] **Step 1: Replace flat BLE status with submenu**

In `app.py __init__`, replace the flat `_ble_status_item` with a submenu:

```python
# BLE submenu
self._ble_menu = rumps.MenuItem("BLE")
self._ble_status = rumps.MenuItem("Status: Connecting...", callback=None)
self._ble_status.set_callback(None)
self._ble_enabled_toggle = rumps.MenuItem("Enabled", callback=self._on_toggle_ble_enabled)
self._ble_reconnect = rumps.MenuItem("Reconnect", callback=self._on_reconnect)

prefs = load_preferences()
self._ble_enabled_toggle.state = prefs.get("ble_enabled", True)

self._ble_menu.update([self._ble_status, None, self._ble_enabled_toggle, self._ble_reconnect])
```

Add the toggle handler:

```python
def _on_toggle_ble_enabled(self, sender):
    sender.state = not sender.state
    save_preferences(updates={"ble_enabled": sender.state})
    if sender.state and self._loop:
        from clawd_tank_daemon.ble_client import ClawdBleClient
        client = ClawdBleClient()
        self._transport_status["ble"] = False
        asyncio.run_coroutine_threadsafe(
            self._daemon.add_transport("ble", client), self._loop
        )
    elif not sender.state and self._loop:
        asyncio.run_coroutine_threadsafe(
            self._daemon.remove_transport("ble"), self._loop
        )
        self._transport_status.pop("ble", None)
    self._schedule_menu_update()
```

- [ ] **Step 2: Update _update_menu_state for BLE submenu**

Replace the BLE status update in `_update_menu_state`:

```python
# BLE status
if not self._ble_enabled_toggle.state:
    self._ble_menu.title = "BLE  ○ Disabled"
    self._ble_status.title = "Status: Disabled"
    self._ble_reconnect.set_callback(None)
else:
    ble_connected = self._transport_status.get("ble", False)
    if ble_connected:
        self._ble_menu.title = "BLE  ● Connected"
        self._ble_status.title = "Status: Connected"
    else:
        self._ble_menu.title = "BLE  ● Connecting..."
        self._ble_status.title = "Status: Connecting..."
    self._ble_reconnect.set_callback(self._on_reconnect)
```

- [ ] **Step 3: Update _start_daemon_thread for preference-driven BLE**

In `_start_daemon_thread`, add BLE transport based on preferences (replace existing BLE setup which was implicit in daemon init):

```python
# After daemon creation and loop ready:
prefs = load_preferences()
if prefs.get("ble_enabled", True):
    from clawd_tank_daemon.ble_client import ClawdBleClient
    client = ClawdBleClient()
    self._transport_status["ble"] = False
    asyncio.run_coroutine_threadsafe(
        self._daemon.add_transport("ble", client), self._loop
    )
```

- [ ] **Step 4: Update menu assembly**

Replace the flat menu items in `self.menu = [...]` with the new submenu structure:

```python
self.menu = [
    self._ble_menu,
    self._sim_menu,  # (added in next task)
    None,
    self._brightness_item,
    None,
    self._session_timeout_menu,
    None,
    self._hooks_item,
    self._login_item,
    None,
    self._quit_item,
]
```

Remove the old `_ble_status_item`, `_sim_status_item`, `_sim_toggle`, `_reconnect_item`.

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_menubar/app.py
git commit -m "feat(menubar): BLE transport submenu with enable/disable"
```

---

### Task 10: Menu Bar UI — Simulator Submenu

**Files:**
- Modify: `host/clawd_tank_menubar/app.py`

- [ ] **Step 1: Add simulator submenu with all controls**

In `__init__`, create the simulator submenu:

```python
# Simulator submenu
self._sim_menu = rumps.MenuItem("Simulator")
self._sim_status = rumps.MenuItem("Status: Disabled", callback=None)
self._sim_status.set_callback(None)
self._sim_enabled_toggle = rumps.MenuItem("Enabled", callback=self._on_toggle_sim_enabled)
self._sim_window_toggle = rumps.MenuItem("Show Window", callback=self._on_toggle_sim_window)
self._sim_pinned_toggle = rumps.MenuItem("Always on Top", callback=self._on_toggle_sim_pinned)

self._sim_enabled_toggle.state = prefs.get("sim_enabled", True)
self._sim_window_toggle.state = prefs.get("sim_window_visible", True)
self._sim_pinned_toggle.state = prefs.get("sim_always_on_top", True)

self._sim_menu.update([
    self._sim_status, None,
    self._sim_enabled_toggle,
    self._sim_window_toggle,
    self._sim_pinned_toggle,
])

self._sim_process: Optional[SimProcessManager] = None
```

Add imports at top of file:

```python
from clawd_tank_daemon.sim_process import SimProcessManager
```

- [ ] **Step 2: Implement toggle handlers**

```python
def _on_toggle_sim_enabled(self, sender):
    sender.state = not sender.state
    save_preferences(updates={"sim_enabled": sender.state})
    if sender.state and self._loop:
        self._start_simulator()
    elif not sender.state and self._loop:
        self._stop_simulator()
    self._schedule_menu_update()

def _on_toggle_sim_window(self, sender):
    sender.state = not sender.state
    save_preferences(updates={"sim_window_visible": sender.state})
    if self._sim_process and self._loop:
        if sender.state:
            asyncio.run_coroutine_threadsafe(
                self._sim_process.show_window(), self._loop
            )
        else:
            asyncio.run_coroutine_threadsafe(
                self._sim_process.hide_window(), self._loop
            )

def _on_toggle_sim_pinned(self, sender):
    sender.state = not sender.state
    save_preferences(updates={"sim_always_on_top": sender.state})
    if self._sim_process and self._loop:
        asyncio.run_coroutine_threadsafe(
            self._sim_process.set_pinned(sender.state), self._loop
        )

def _on_sim_window_event(self, event):
    """Called when the simulator sends a window event (e.g., user closed window)."""
    if event.get("event") == "window_hidden":
        self._sim_window_toggle.state = False
        save_preferences(updates={"sim_window_visible": False})
        self._schedule_menu_update()
```

- [ ] **Step 3: Implement _start_simulator and _stop_simulator**

```python
def _start_simulator(self):
    """Start the simulator process and connect transport."""
    self._sim_process = SimProcessManager(
        on_window_event=self._on_sim_window_event
    )
    self._transport_status["sim"] = False

    async def _do_start():
        client = await self._sim_process.start()
        if client:
            await self._daemon.add_transport("sim", client)
            # Apply initial window state
            prefs = load_preferences()
            if prefs.get("sim_window_visible", True):
                await self._sim_process.show_window()
            await self._sim_process.set_pinned(prefs.get("sim_always_on_top", True))

    asyncio.run_coroutine_threadsafe(_do_start(), self._loop)

def _stop_simulator(self):
    """Stop the simulator process and remove transport."""
    async def _do_stop():
        await self._daemon.remove_transport("sim")
        if self._sim_process:
            await self._sim_process.stop()
            self._sim_process = None
        self._transport_status.pop("sim", None)

    asyncio.run_coroutine_threadsafe(_do_stop(), self._loop)
```

- [ ] **Step 4: Update _start_daemon_thread for simulator**

Add after the BLE setup in `_start_daemon_thread`:

```python
if prefs.get("sim_enabled", True):
    self._start_simulator()
```

- [ ] **Step 5: Update _update_menu_state for simulator submenu**

```python
# Simulator status
if not self._sim_enabled_toggle.state:
    self._sim_menu.title = "Simulator  ○ Disabled"
    self._sim_status.title = "Status: Disabled"
    self._sim_window_toggle.set_callback(None)
    self._sim_pinned_toggle.set_callback(None)
else:
    sim_connected = self._transport_status.get("sim", False)
    if sim_connected:
        self._sim_menu.title = "Simulator  ● Running"
        self._sim_status.title = "Status: Running"
    else:
        self._sim_menu.title = "Simulator  ● Connecting..."
        self._sim_status.title = "Status: Connecting..."
    self._sim_window_toggle.set_callback(self._on_toggle_sim_window)
    self._sim_pinned_toggle.set_callback(self._on_toggle_sim_pinned)
```

- [ ] **Step 6: Update _on_quit to stop simulator**

In `_on_quit`, add simulator cleanup before quitting:

```python
def _on_quit(self, _):
    if self._sim_process and self._loop:
        asyncio.run_coroutine_threadsafe(self._sim_process.stop(), self._loop)
    rumps.quit_application()
```

- [ ] **Step 7: Update test_menubar.py**

The existing daemon/observer tests should still pass since they test via `ClawdDaemon(observer=obs)` which defaults to `headless=True`. Run and fix any issues.

Run: `cd host && .venv/bin/pytest tests/test_menubar.py -v`

- [ ] **Step 8: Run full test suite and manual test**

Run: `cd host && .venv/bin/pytest -v`
Expected: All pass

Manual test:
```bash
cd simulator && cmake -B build && cmake --build build && cd ..
cd host && .venv/bin/python -m clawd_tank_menubar.app
```

Verify: BLE and Simulator submenus work, toggles persist across restarts, window show/hide/pinned work, closing window updates menu.

- [ ] **Step 9: Commit**

```bash
git add host/clawd_tank_menubar/app.py host/tests/test_menubar.py
git commit -m "feat(menubar): simulator submenu with lifecycle management"
```

---

## Chunk 3: CI & Packaging

### Task 11: GitHub Actions Release Workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Create the workflow**

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  build:
    runs-on: macos-14  # arm64 Apple Silicon
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive

      # Build simulator with static SDL2
      - name: Build simulator
        run: |
          cd simulator
          cmake -B build -DSTATIC_SDL2=ON
          cmake --build build
          # Verify no SDL2 dynamic dependency
          ! otool -L build/clawd-tank-sim | grep -i sdl

      # Set up Python for menu bar app
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install host dependencies
        run: |
          cd host
          pip install -r requirements.txt
          pip install py2app

      # Build menu bar app
      - name: Build menu bar app
        run: |
          cd host
          python setup.py py2app

      # Inject simulator binary
      - name: Bundle simulator into app
        run: |
          cp simulator/build/clawd-tank-sim "host/dist/Clawd Tank.app/Contents/MacOS/"

      # Package
      - name: Create release zip
        run: |
          cd host/dist
          zip -r clawd-tank-macos-arm64.zip "Clawd Tank.app"

      # Upload to release
      - name: Upload release asset
        uses: softprops/action-gh-release@v2
        with:
          files: host/dist/clawd-tank-macos-arm64.zip
```

- [ ] **Step 2: Commit**

```bash
mkdir -p .github/workflows
git add .github/workflows/release.yml
git commit -m "ci: add GitHub Actions release workflow for macOS arm64"
```

---

### Task 12: Launchd Plist Staleness Detection

**Files:**
- Modify: `host/clawd_tank_menubar/launchd.py`

- [ ] **Step 1: Add plist staleness check**

Add to `launchd.py`:

```python
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
```

- [ ] **Step 2: Wire into app.py startup**

In `app.py __init__`, after the login item setup, check for stale plist and mark the menu item:

```python
# After self._login_item.state = launchd.is_enabled()
if launchd.is_enabled() and launchd.is_stale():
    self._login_item.title = "Launch at Login (needs update)"
    logger.warning("Launchd plist points to a different executable — user should re-enable Launch at Login")
```

When the user toggles "Launch at Login" off and on, the plist is re-written with the current executable path, fixing the issue. The title resets to normal on the next `_update_menu_state` call.

- [ ] **Step 3: Commit**

```bash
git add host/clawd_tank_menubar/launchd.py host/clawd_tank_menubar/app.py
git commit -m "fix(launchd): detect and re-register stale plist on startup"
```

---

### Task 13: Final Integration Test and Cleanup

- [ ] **Step 1: Run full test suite**

Run: `cd host && .venv/bin/pytest -v`
Expected: All tests pass

- [ ] **Step 2: Test static simulator build**

Run:
```bash
cd simulator && cmake -B build-static -DSTATIC_SDL2=ON && cmake --build build-static
otool -L build-static/clawd-tank-sim | grep -i sdl
```
Expected: No SDL2 dynamic dependency

- [ ] **Step 3: Test full flow manually**

```bash
# Start menu bar app (it should auto-start simulator)
cd host && .venv/bin/python -m clawd_tank_menubar.app
```

Verify the complete flow:
1. App starts, simulator launches automatically
2. Simulator window appears (borderless, always on top)
3. BLE shows "Connecting..." (if no hardware)
4. Simulator submenu shows "Running"
5. Toggle "Show Window" off — window hides
6. Toggle "Show Window" on — window reappears
7. Close window via Cmd+W — window hides, menu updates
8. Toggle "Always on Top" — window layer changes
9. Disable simulator — process stops, status shows "Disabled"
10. Re-enable simulator — process restarts
11. Disable BLE — scanning stops
12. Quit app — simulator process killed cleanly

- [ ] **Step 4: Commit any final fixes**

Stage only the specific files that were changed during this task:

```bash
git add <specific changed files>
git commit -m "feat: bundled simulator — final integration"
```
