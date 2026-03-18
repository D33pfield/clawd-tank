# StopFailure Hook Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a DIZZY animation, error notification card, and triple red LED flash when a Claude Code session hits an API error via the new `StopFailure` hook.

**Architecture:** Follows the existing `Stop`/`Notification` → `"add"` event pattern. `StopFailure` maps to `{"event": "add", "hook": "StopFailure"}` with a new `"error"` session state and `"dizzy"` animation. Firmware gains `CLAWD_ANIM_DIZZY`, an `alert` field on `ble_evt_t`, and `rgb_led_flash_error()`.

**Tech Stack:** Python (host daemon), C (ESP-IDF firmware + SDL2 simulator), SVG → PNG → RLE sprite pipeline.

**Spec:** `docs/superpowers/specs/2026-03-17-stopfailure-hook-design.md`

---

## Chunk 1: Host — Protocol & Session State

### Task 1: Protocol — StopFailure hook conversion

**Files:**
- Modify: `host/clawd_tank_daemon/protocol.py:33-45` (add StopFailure handler after Stop)
- Modify: `host/clawd_tank_daemon/protocol.py:106-112` (add alert field to add BLE payload)
- Modify: `host/clawd_tank_daemon/protocol.py:134-147` (add dizzy to v1 fallback)
- Test: `host/tests/test_protocol.py`

- [ ] **Step 1: Write failing tests for StopFailure hook conversion**

Add to `host/tests/test_protocol.py`:

```python
def test_stop_failure_produces_add_event():
    hook = {
        "hook_event_name": "StopFailure",
        "session_id": "abc-123",
        "cwd": "/Users/me/Projects/my-project",
        "error": "Rate limit reached",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "add"
    assert msg["hook"] == "StopFailure"
    assert msg["session_id"] == "abc-123"
    assert msg["project"] == "my-project"
    assert msg["message"] == "Rate limit reached"


def test_stop_failure_fallback_message():
    hook = {
        "hook_event_name": "StopFailure",
        "session_id": "abc-123",
        "cwd": "/tmp/proj",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["message"] == "API error"


def test_stop_failure_ble_payload_includes_alert():
    msg = {
        "event": "add",
        "hook": "StopFailure",
        "session_id": "s1",
        "project": "proj",
        "message": "Rate limited",
    }
    ble = daemon_message_to_ble_payload(msg)
    parsed = json.loads(ble)
    assert parsed["action"] == "add"
    assert parsed["alert"] == "error"


def test_normal_add_ble_payload_no_alert():
    msg = {
        "event": "add",
        "hook": "Stop",
        "session_id": "s1",
        "project": "proj",
        "message": "Waiting",
    }
    ble = daemon_message_to_ble_payload(msg)
    parsed = json.loads(ble)
    assert "alert" not in parsed


def test_display_state_to_v1_dizzy_maps_to_confused():
    state = {"anims": ["dizzy"], "ids": [1], "subagents": 0}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "confused"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v -k "stop_failure or dizzy"`
Expected: FAIL — `StopFailure` not handled, `alert` field missing, `dizzy` not mapped.

- [ ] **Step 3: Implement StopFailure in hook_payload_to_daemon_message**

In `host/clawd_tank_daemon/protocol.py`, add after the `Stop` handler (after line 45):

```python
    if event_name == "StopFailure":
        cwd = hook.get("cwd", "")
        project = Path(cwd).name if cwd else "unknown"
        if not project:
            project = "unknown"
        message = hook.get("error", "") or hook.get("stop_reason", "") or "API error"
        return {
            "event": "add",
            "hook": "StopFailure",
            "session_id": session_id,
            "project": project,
            "message": message,
        }
```

- [ ] **Step 4: Add alert field to daemon_message_to_ble_payload**

In `host/clawd_tank_daemon/protocol.py`, modify the `add` branch (around line 106-112):

```python
    if event == "add":
        payload = {
            "action": "add",
            "id": msg.get("session_id", ""),
            "project": msg.get("project", ""),
            "message": msg.get("message", ""),
        }
        if msg.get("hook") == "StopFailure":
            payload["alert"] = "error"
        return json.dumps(payload)
```

- [ ] **Step 5: Add dizzy to v1 fallback**

In `host/clawd_tank_daemon/protocol.py`, modify `display_state_to_v1_payload` (around line 143):

```python
    elif any(a in ("confused", "dizzy") for a in state.get("anims", [])):
        status = "confused"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add host/clawd_tank_daemon/protocol.py host/tests/test_protocol.py
git commit -m "feat: add StopFailure hook to protocol layer with alert field"
```

---

### Task 2: Session state — error state and dizzy display mapping

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:258-264` (add StopFailure → error in _update_session_state)
- Modify: `host/clawd_tank_daemon/daemon.py:215-226` (add error → dizzy in _compute_display_state)
- Test: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests for error state**

Add to `host/tests/test_session_state.py`:

```python
def test_error_state_returns_dizzy():
    d = make_daemon()
    _add_session(d, "s1", {"state": "error", "last_event": time.time()})
    assert d._compute_display_state() == {"anims": ["dizzy"], "ids": [1], "subagents": 0}


@pytest.mark.asyncio
async def test_stop_failure_add_sets_error():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "StopFailure", "session_id": "s1",
        "project": "proj", "message": "Rate limited",
    })
    assert d._session_states["s1"]["state"] == "error"


@pytest.mark.asyncio
async def test_error_state_clears_on_prompt_submit():
    d = make_daemon()
    d._session_states["s1"] = {"state": "error", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "thinking"


@pytest.mark.asyncio
async def test_error_state_clears_on_tool_use():
    d = make_daemon()
    d._session_states["s1"] = {"state": "error", "last_event": time.time()}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "working"


@pytest.mark.asyncio
async def test_error_state_removed_on_session_end():
    d = make_daemon()
    d._session_states["s1"] = {"state": "error", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert "s1" not in d._session_states


def test_error_state_evicted_on_staleness():
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {"state": "error", "last_event": time.time() - 9999}
    d._evict_stale_sessions()
    assert "s1" not in d._session_states


@pytest.mark.asyncio
async def test_stop_then_stop_failure_overwrites_to_error():
    """Stop then StopFailure on same session — card overwrites, state becomes error."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"
    await d._handle_message({
        "event": "add", "hook": "StopFailure", "session_id": "s1",
        "project": "proj", "message": "Rate limited",
    })
    assert d._session_states["s1"]["state"] == "error"


@pytest.mark.asyncio
async def test_error_and_working_mixed():
    d = make_daemon()
    _add_session(d, "s1", {"state": "error", "last_event": time.time()})
    _add_session(d, "s2", {"state": "working", "last_event": time.time()})
    state = d._compute_display_state()
    assert state["anims"] == ["dizzy", "typing"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "error or dizzy or stop_failure"`
Expected: FAIL — `"error"` state not handled.

- [ ] **Step 3: Add error state to _update_session_state**

In `host/clawd_tank_daemon/daemon.py`, modify the `add` event branch in `_update_session_state` (around line 258-264). Add after the `elif hook == "Notification":` block:

```python
            elif hook == "StopFailure":
                self._session_states[session_id]["state"] = "error"
```

- [ ] **Step 4: Add dizzy to _compute_display_state**

In `host/clawd_tank_daemon/daemon.py`, modify `_compute_display_state` (around line 215-226). Add **between the `confused` elif and the final `else`** clause:

```python
            elif state["state"] == "error":
                anims.append("dizzy")
```

The resulting chain should be: `working/subagents` → `thinking` → `confused` → `error` → `else: idle`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`
Expected: All PASS.

- [ ] **Step 6: Run full test suite**

Run: `cd host && .venv/bin/pytest -v`
Expected: All PASS — no regressions.

- [ ] **Step 7: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat: add error session state with dizzy display mapping"
```

---

### Task 3: Hook registration and embedded script

**Files:**
- Modify: `host/clawd_tank_menubar/hooks.py:125-156` (add StopFailure to HOOKS_CONFIG)
- Modify: `host/clawd_tank_menubar/hooks.py:45-70` (add StopFailure to embedded NOTIFY_SCRIPT)
- Modify: `host/clawd-tank-notify:34-45` (add StopFailure to standalone script)

- [ ] **Step 1: Add StopFailure to HOOKS_CONFIG**

In `host/clawd_tank_menubar/hooks.py`, add after the `"Stop"` entry (after line 131):

```python
    "StopFailure": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
```

- [ ] **Step 2: Add StopFailure to embedded NOTIFY_SCRIPT**

In `host/clawd_tank_menubar/hooks.py`, in the embedded `hook_to_message` function, add after the `Stop` handler (after line 57):

```python
        if event_name == "StopFailure":
            cwd = hook.get("cwd", "")
            project = Path(cwd).name if cwd else "unknown"
            message = hook.get("error", "") or hook.get("stop_reason", "") or "API error"
            return {
                "event": "add",
                "hook": "StopFailure",
                "session_id": session_id,
                "project": project or "unknown",
                "message": message,
            }
```

- [ ] **Step 3: Verify standalone clawd-tank-notify needs no changes**

The standalone `host/clawd-tank-notify` imports `hook_payload_to_daemon_message` from `clawd_tank_daemon.protocol` — changes made in Task 1 automatically propagate. No modifications needed to this file.

- [ ] **Step 4: Verify hooks test passes**

Run: `cd host && .venv/bin/pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_menubar/hooks.py host/clawd-tank-notify
git commit -m "feat: register StopFailure hook and update hook scripts"
```

---

## Chunk 2: Firmware — BLE Parsing & LED

### Task 4: Add alert field to ble_evt_t and parse it

**Files:**
- Modify: `firmware/main/ble_service.h:34-46` (add alert field to ble_evt_t)
- Modify: `simulator/shims/ble_service.h:31-43` (mirror alert field)
- Modify: `firmware/main/ble_service.c:95-110` (parse alert in add action)
- Modify: `simulator/sim_ble_parse.c:51-63` (parse alert in add action)

- [ ] **Step 1: Add alert field to firmware ble_evt_t**

In `firmware/main/ble_service.h`, add after `session_overflow` (line 45):

```c
    uint8_t alert;  /* 0=none, 1=error */
```

- [ ] **Step 2: Mirror alert field in simulator shim**

In `simulator/shims/ble_service.h`, add after `session_overflow` (line 42):

```c
    uint8_t alert;  /* 0=none, 1=error */
```

- [ ] **Step 3: Parse alert field in firmware ble_service.c**

In `firmware/main/ble_service.c`, after the message parsing in the `add` action block (after line 110):

```c
        cJSON *alert = cJSON_GetObjectItem(json, "alert");
        evt.alert = (alert && cJSON_IsString(alert) && strcmp(alert->valuestring, "error") == 0) ? 1 : 0;
```

- [ ] **Step 4: Parse alert field in simulator sim_ble_parse.c**

In `simulator/sim_ble_parse.c`, after the message parsing in the `add` action block (after line 63):

```c
        cJSON *alert = cJSON_GetObjectItem(json, "alert");
        out->alert = (alert && cJSON_IsString(alert) && strcmp(alert->valuestring, "error") == 0) ? 1 : 0;
```

- [ ] **Step 5: Add dizzy to parse_anim_name in both files**

In `firmware/main/ble_service.c`, add to `parse_anim_name` (after line 63):

```c
    if (strcmp(str, "dizzy") == 0)    return CLAWD_ANIM_DIZZY;
```

In `simulator/sim_ble_parse.c`, add to `parse_anim_name` (after line 17):

```c
    if (strcmp(str, "dizzy") == 0)    return CLAWD_ANIM_DIZZY;
```

- [ ] **Step 6: Build simulator to verify compilation**

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: Build succeeds (will fail until CLAWD_ANIM_DIZZY is defined in Task 6, so this step may be deferred).

- [ ] **Step 7: Commit**

```bash
git add firmware/main/ble_service.h firmware/main/ble_service.c simulator/shims/ble_service.h simulator/sim_ble_parse.c
git commit -m "feat: add alert field to ble_evt_t and parse dizzy animation name"
```

---

### Task 5: Triple red LED flash

**Files:**
- Modify: `firmware/main/rgb_led.h` (add rgb_led_flash_error declaration)
- Modify: `firmware/main/rgb_led.c` (implement rgb_led_flash_error)
- Modify: `firmware/main/ui_manager.c:192-208` (call rgb_led_flash_error for alert notifications)

- [ ] **Step 1: Add rgb_led_flash_error declaration**

In `firmware/main/rgb_led.h`, add after `rgb_led_flash` (after line 16):

```c
/** Flash the LED red three times. Non-blocking (uses a timer). */
void rgb_led_flash_error(void);
```

- [ ] **Step 2: Implement rgb_led_flash_error**

In `firmware/main/rgb_led.c`, add a mode flag and update the timer callback. Add these definitions after `STEPS_PER_COLOR` (line 32):

```c
/* Flash modes */
#define FLASH_MODE_PALETTE  0
#define FLASH_MODE_ERROR    1

static atomic_int s_flash_mode;

/* Error flash: 3 red pulses.
 * Each pulse: 5 steps on (150ms) + 3 steps off (90ms) = 8 steps per pulse.
 * 3 pulses = 24 steps + 7 steps fade = 31 total steps.
 * At 30ms/step, total ≈ 930ms. */
#define ERROR_PULSE_ON_STEPS   5
#define ERROR_PULSE_OFF_STEPS  3
#define ERROR_PULSE_STEPS      (ERROR_PULSE_ON_STEPS + ERROR_PULSE_OFF_STEPS)
#define ERROR_PULSE_COUNT      3
#define ERROR_FADE_STEPS       7
#define ERROR_TOTAL_STEPS      (ERROR_PULSE_COUNT * ERROR_PULSE_STEPS + ERROR_FADE_STEPS)
```

Replace the existing `timer_cb` with a version that checks `s_flash_mode`:

```c
static void timer_cb(void *arg)
{
    (void)arg;
    s_steps_left--;

    if (s_steps_left <= 0) {
        apply_color(0, 0, 0);
        esp_timer_stop(s_timer);
        return;
    }

    if (s_flash_mode == FLASH_MODE_ERROR) {
        /* Error mode: 3 red pulses then fade */
        int step = ERROR_TOTAL_STEPS - s_steps_left;
        int in_fade = step >= ERROR_PULSE_COUNT * ERROR_PULSE_STEPS;
        if (in_fade) {
            /* Fade out from red */
            int fade_step = step - ERROR_PULSE_COUNT * ERROR_PULSE_STEPS;
            float fade = 1.0f - (float)fade_step / (float)ERROR_FADE_STEPS;
            apply_color((uint8_t)(255 * fade), 0, 0);
        } else {
            int in_pulse = step % ERROR_PULSE_STEPS;
            if (in_pulse < ERROR_PULSE_ON_STEPS) {
                apply_color(255, 0, 0);
            } else {
                apply_color(0, 0, 0);
            }
        }
        return;
    }

    /* Palette cycle mode (existing behavior) */
    int color_idx = s_steps_left / STEPS_PER_COLOR;
    int step_in_color = s_steps_left % STEPS_PER_COLOR;

    int from = color_idx % PALETTE_SIZE;
    int to = (color_idx + 1) % PALETTE_SIZE;

    float t = (float)step_in_color / (float)STEPS_PER_COLOR;
    uint8_t r = (uint8_t)(s_palette[from][0] * (1.0f - t) + s_palette[to][0] * t);
    uint8_t g = (uint8_t)(s_palette[from][1] * (1.0f - t) + s_palette[to][1] * t);
    uint8_t b = (uint8_t)(s_palette[from][2] * (1.0f - t) + s_palette[to][2] * t);

    int total = PALETTE_SIZE * STEPS_PER_COLOR;
    if (s_steps_left < total / 4) {
        float fade = (float)s_steps_left / (float)(total / 4);
        r = (uint8_t)(r * fade);
        g = (uint8_t)(g * fade);
        b = (uint8_t)(b * fade);
    }

    apply_color(r, g, b);
}
```

Add the new function at the end of the file (after `rgb_led_flash`):

```c
void rgb_led_flash_error(void)
{
    if (!s_strip || !s_timer) return;

    esp_timer_stop(s_timer);

    s_flash_mode = FLASH_MODE_ERROR;
    s_steps_left = ERROR_TOTAL_STEPS;

    apply_color(255, 0, 0);  /* Start with red */

    esp_timer_start_periodic(s_timer, STEP_MS * 1000);
}
```

Also update `rgb_led_flash` to set the mode:

```c
void rgb_led_flash(uint8_t r, uint8_t g, uint8_t b, int duration_ms)
{
    (void)r; (void)g; (void)b; (void)duration_ms;
    if (!s_strip || !s_timer) return;

    esp_timer_stop(s_timer);

    s_flash_mode = FLASH_MODE_PALETTE;
    s_steps_left = PALETTE_SIZE * STEPS_PER_COLOR;

    apply_color(s_palette[0][0], s_palette[0][1], s_palette[0][2]);

    esp_timer_start_periodic(s_timer, STEP_MS * 1000);
}
```

- [ ] **Step 3: Wire alert to LED in ui_manager.c**

In `firmware/main/ui_manager.c`, modify the `BLE_EVT_NOTIF_ADD` handler (around line 199):

Replace:
```c
        /* Flash RGB LED behind acrylic */
        rgb_led_flash(255, 140, 30, 800);
```

With:
```c
        /* Flash RGB LED behind acrylic */
        if (evt->alert) {
            rgb_led_flash_error();
        } else {
            rgb_led_flash(255, 140, 30, 800);
        }
```

- [ ] **Step 4: Build simulator to verify compilation**

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: Build succeeds (may need Task 6 for CLAWD_ANIM_DIZZY first).

- [ ] **Step 5: Commit**

```bash
git add firmware/main/rgb_led.h firmware/main/rgb_led.c firmware/main/ui_manager.c
git commit -m "feat: add rgb_led_flash_error for triple red pulse on API errors"
```

---

## Chunk 3: Firmware — DIZZY Animation

### Task 6: Create DIZZY sprite and wire into scene

**Files:**
- Modify: `firmware/main/scene.h:6-21` (add CLAWD_ANIM_DIZZY to enum)
- Create: `firmware/main/assets/sprite_dizzy.h` (via sprite pipeline)
- Modify: `firmware/main/scene.c:19-33` (add sprite_dizzy.h include)
- Modify: `firmware/main/scene.c:46-59` (add DIZZY_FRAME_MS timing)
- Modify: `firmware/main/scene.c:163-172` (add DIZZY to anim_defs table)
- Modify: `firmware/main/scene.c:1287-1291` (add "dizzy" to anim_id_to_name)

This task requires the sprite pipeline. The DIZZY animation must be created as an SVG, rendered to frames, and converted to an RLE header.

- [ ] **Step 1: Add CLAWD_ANIM_DIZZY to enum**

In `firmware/main/scene.h`, add before `CLAWD_ANIM_SWEEPING` (after line 16):

```c
    CLAWD_ANIM_DIZZY,
```

Note: Adding before SWEEPING keeps error-state animations grouped with character animations. The enum value of SWEEPING and later entries will shift by 1 — this is safe because `parse_anim_name` maps by string, not by integer value.

- [ ] **Step 2: Create the DIZZY animation SVG**

Use the `create-clawd-animation` skill to create the animated SVG:
- Clawd standing with X eyes and a band-aid on forehead
- 2-3 pixel-art stars orbiting in an elliptical path above his head
- Looping animation, ~8fps
- Reference: `clawd0.png` for the base crab design

- [ ] **Step 3: Render SVG to frames and convert to RLE header**

```bash
python tools/svg2frames.py assets/svg-animations/dizzy.svg /tmp/dizzy-frames/ --fps 8 --duration auto --scale 4
python tools/png2rgb565.py /tmp/dizzy-frames/ firmware/main/assets/sprite_dizzy.h --name dizzy
python tools/crop_sprites.py
```

- [ ] **Step 4: Add sprite include and frame timing**

In `firmware/main/scene.c`, add include (after line 29):

```c
#include "assets/sprite_dizzy.h"
```

Add frame timing constant (after CONFUSED_FRAME_MS, around line 56):

```c
#define DIZZY_FRAME_MS     (1000 / 8)   /* 125ms @ 8fps */
```

- [ ] **Step 5: Add DIZZY to anim_defs table**

In `firmware/main/scene.c`, add after the CONFUSED entry (after line 172):

```c
    [CLAWD_ANIM_DIZZY] = {
        .rle_data = dizzy_rle_data,
        .frame_offsets = dizzy_frame_offsets,
        .frame_count = DIZZY_FRAME_COUNT,
        .frame_ms = DIZZY_FRAME_MS,
        .looping = true,
        .width = DIZZY_WIDTH,
        .height = DIZZY_HEIGHT,
        .y_offset = -4,   /* adjust after seeing the sprite */
    },
```

- [ ] **Step 6: Add "dizzy" to anim_id_to_name**

In `firmware/main/scene.c`, update the `anim_id_to_name` names array (around line 1287-1291). Insert `"dizzy"` after `"confused"` to match the new enum order. The full array should be:

```c
    static const char *names[] = {
        "idle", "alert", "happy", "sleeping", "disconnected",
        "thinking", "typing", "juggling", "building", "confused",
        "dizzy", "sweeping", "walking", "going_away", "mini_clawd"
    };
```

- [ ] **Step 7: Build simulator and test**

```bash
cd simulator && cmake -B build && cmake --build build
./simulator/build/clawd-tank-sim --headless --events 'connect; wait 500; sessions dizzy:1; wait 3000' --screenshot-dir /tmp/dizzy-shots/ --screenshot-on-event
```

Visually verify the DIZZY animation in the screenshot.

- [ ] **Step 8: Build firmware**

```bash
cd firmware && idf.py build
```

Expected: Build succeeds.

- [ ] **Step 9: Commit**

```bash
git add firmware/main/scene.h firmware/main/scene.c firmware/main/assets/sprite_dizzy.h
git commit -m "feat: add CLAWD_ANIM_DIZZY sprite with X eyes, band-aid, and orbiting stars"
```

---

## Chunk 4: Integration & Testing

### Task 7: C unit test for alert field parsing (optional)

**Files:**
- Create: `firmware/test/test_ble_parse.c`
- Modify: `firmware/test/Makefile` (add test_ble_parse target)

The existing C test infrastructure doesn't include cJSON. This task adds a minimal test binary that compiles `sim_ble_parse.c` against the simulator's bundled cJSON.

- [ ] **Step 1: Create test_ble_parse.c**

```c
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include "../main/notification.h"
#include "ble_service.h"
#include "../simulator/sim_ble_parse.h"

static void test_add_with_alert_error(void) {
    ble_evt_t evt;
    const char *json = "{\"action\":\"add\",\"id\":\"s1\",\"project\":\"proj\",\"message\":\"Rate limited\",\"alert\":\"error\"}";
    int rc = sim_ble_parse_json(json, strlen(json), &evt);
    assert(rc == 0);
    assert(evt.type == BLE_EVT_NOTIF_ADD);
    assert(evt.alert == 1);
    assert(strcmp(evt.message, "Rate limited") == 0);
    printf("  PASS: test_add_with_alert_error\n");
}

static void test_add_without_alert(void) {
    ble_evt_t evt;
    const char *json = "{\"action\":\"add\",\"id\":\"s1\",\"project\":\"proj\",\"message\":\"Waiting\"}";
    int rc = sim_ble_parse_json(json, strlen(json), &evt);
    assert(rc == 0);
    assert(evt.type == BLE_EVT_NOTIF_ADD);
    assert(evt.alert == 0);
    printf("  PASS: test_add_without_alert\n");
}

int main(void) {
    printf("test_ble_parse:\n");
    test_add_with_alert_error();
    test_add_without_alert();
    printf("All tests passed.\n");
    return 0;
}
```

Note: This test requires include paths for the simulator headers. The exact include paths and compilation flags may need adjustment — match the simulator's CMakeLists.txt for cJSON and shim includes. If setting up the compilation is too complex, skip this task and rely on the simulator integration test in Task 8 Step 2.

- [ ] **Step 2: Add to Makefile (if feasible)**

- [ ] **Step 3: Commit**

```bash
git add firmware/test/test_ble_parse.c firmware/test/Makefile
git commit -m "test: add C unit test for alert field in BLE parse"
```

---

### Task 8: End-to-end integration test

**Files:**
- Test: manual via simulator + TCP

- [ ] **Step 1: Build static simulator**

```bash
cd simulator && cmake -B build-static -DSTATIC_SDL2=ON && cmake --build build-static
```

- [ ] **Step 2: Test error notification via TCP**

Run simulator with TCP listener:
```bash
./simulator/build-static/clawd-tank-sim --listen --bordered &
```

Send error notification and dizzy animation:
```bash
echo '{"action":"set_sessions","anims":["typing"],"ids":[1],"subagents":0}' | nc localhost 19872
sleep 1
echo '{"action":"add","id":"s1","project":"test-proj","message":"Rate limit reached","alert":"error"}' | nc localhost 19872
sleep 1
echo '{"action":"set_sessions","anims":["dizzy"],"ids":[1],"subagents":0}' | nc localhost 19872
```

Verify:
- Notification card shows "Rate limit reached"
- Crab switches to DIZZY animation
- (LED flash is firmware-only, not visible in simulator)

- [ ] **Step 3: Test transition out of error state**

Send UserPromptSubmit equivalent:
```bash
echo '{"action":"dismiss","id":"s1"}' | nc localhost 19872
echo '{"action":"set_sessions","anims":["thinking"],"ids":[1],"subagents":0}' | nc localhost 19872
```

Verify: Card dismisses, crab returns to thinking animation.

- [ ] **Step 4: Run full Python test suite**

```bash
cd host && .venv/bin/pytest -v
```

Expected: All PASS.

- [ ] **Step 5: Run C unit tests**

```bash
cd firmware/test && make test
```

Expected: All PASS.

- [ ] **Step 6: Build and install app**

```bash
cd host && ./build.sh --install
```

Kill and relaunch the app. Verify hooks are auto-reinstalled (check `~/.claude/settings.json` includes `StopFailure`).

- [ ] **Step 7: Final commit if any remaining changes**

Stage only relevant files — do not use `git add -A`.

---

## Task Dependencies

```
Task 1 (protocol) ─┐
Task 2 (session)  ─┼─→ Task 8 (integration)
Task 3 (hooks)    ─┤
Task 4 (BLE parse)─┤
Task 5 (LED)      ─┤
Task 6 (sprite)   ─┤
Task 7 (C test)   ─┘  (optional, depends on Task 4)
```

Tasks 1-3 (host) are independent of Tasks 4-6 (firmware) and can be done in parallel. Task 7 is optional and depends on Task 4. Task 8 requires Tasks 1-6.

**Deployment order:** Firmware must be flashed before the host daemon starts sending `"dizzy"` animation names, or `parse_anim_name` returns -1 and the session crab vanishes.
