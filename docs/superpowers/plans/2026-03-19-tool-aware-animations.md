# Tool-Aware Animations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Clawd show distinct animations based on which Claude Code tool is being used (Read→debugger, Bash→building, Edit→typing, WebSearch→wizard, Agent→conducting, LSP/MCP→beacon) instead of the same "typing" animation for all tools.

**Architecture:** The `tool_name` field from `PreToolUse` hook payloads flows through the hook handler → protocol → daemon, where a mapping function selects the animation name. The wire protocol is unchanged — `set_sessions` already sends animation name strings. Firmware adds 4 new animation names to `parse_anim_name()` and 4 new sprite animations.

**Tech Stack:** Python (host daemon), C (ESP-IDF firmware + SDL2 simulator), sprite pipeline (svg2frames.py + png2rgb565.py)

**Spec:** `docs/superpowers/specs/2026-03-19-tool-aware-animations-design.md`

---

### Task 1: Pass tool_name through protocol layer

**Files:**
- Modify: `host/clawd_tank_daemon/protocol.py:22-26`
- Modify: `host/clawd_tank_daemon/protocol.py:155`
- Test: `host/tests/test_protocol.py`

- [ ] **Step 1: Write failing test — tool_name preserved in PreToolUse message**

In `host/tests/test_protocol.py`, add after the existing `test_pre_tool_use_produces_tool_use_event` test:

```python
def test_pre_tool_use_preserves_tool_name():
    hook = {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-2",
        "tool_name": "Bash",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["tool_name"] == "Bash"


def test_pre_tool_use_missing_tool_name_defaults_empty():
    hook = {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-2",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["tool_name"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py::test_pre_tool_use_preserves_tool_name tests/test_protocol.py::test_pre_tool_use_missing_tool_name_defaults_empty -v`

Expected: FAIL — `tool_name` key not in message

- [ ] **Step 3: Add tool_name to PreToolUse message in protocol.py**

In `host/clawd_tank_daemon/protocol.py:22-26`, change the `PreToolUse` handler:

```python
    if event_name == "PreToolUse":
        return {
            "event": "tool_use",
            "session_id": session_id,
            "tool_name": hook.get("tool_name", ""),
        }
```

- [ ] **Step 4: Write failing test — v1 payload counts new animation names as working**

In `host/tests/test_protocol.py`, add:

```python
def test_display_state_to_v1_debugger_counts_as_working():
    state = {"anims": ["debugger"], "ids": [1], "subagents": 0}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "working_1"


def test_display_state_to_v1_wizard_counts_as_working():
    state = {"anims": ["wizard", "conducting"], "ids": [1, 2], "subagents": 0}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "working_2"


def test_display_state_to_v1_beacon_counts_as_working():
    state = {"anims": ["beacon"], "ids": [1], "subagents": 0}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "working_1"
```

- [ ] **Step 5: Run to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py::test_display_state_to_v1_debugger_counts_as_working tests/test_protocol.py::test_display_state_to_v1_wizard_counts_as_working tests/test_protocol.py::test_display_state_to_v1_beacon_counts_as_working -v`

Expected: FAIL — new anim names not counted as working, status shows "idle"

- [ ] **Step 6: Expand WORKING_ANIMS in v1 payload**

In `host/clawd_tank_daemon/protocol.py:155`, change:

```python
    working = sum(1 for a in state.get("anims", []) if a in ("typing", "building"))
```

to:

```python
    WORKING_ANIMS = {"typing", "building", "debugger", "wizard", "conducting", "beacon"}
    working = sum(1 for a in state.get("anims", []) if a in WORKING_ANIMS)
```

- [ ] **Step 7: Run all protocol tests**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v`

Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add host/clawd_tank_daemon/protocol.py host/tests/test_protocol.py
git commit -m "feat: pass tool_name through protocol and expand v1 WORKING_ANIMS"
```

---

### Task 2: Tool-aware animation mapping in daemon

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:215-219,253-256`
- Test: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests — tool_name maps to correct animation**

In `host/tests/test_session_state.py`, add:

```python
def test_working_with_bash_tool_returns_building():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time(), "tool_name": "Bash"})
    state = d._compute_display_state()
    assert state["anims"] == ["building"]


def test_working_with_read_tool_returns_debugger():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time(), "tool_name": "Read"})
    state = d._compute_display_state()
    assert state["anims"] == ["debugger"]


def test_working_with_grep_tool_returns_debugger():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time(), "tool_name": "Grep"})
    state = d._compute_display_state()
    assert state["anims"] == ["debugger"]


def test_working_with_edit_tool_returns_typing():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time(), "tool_name": "Edit"})
    state = d._compute_display_state()
    assert state["anims"] == ["typing"]


def test_working_with_websearch_returns_wizard():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time(), "tool_name": "WebSearch"})
    state = d._compute_display_state()
    assert state["anims"] == ["wizard"]


def test_working_with_agent_returns_conducting():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time(), "tool_name": "Agent"})
    state = d._compute_display_state()
    assert state["anims"] == ["conducting"]


def test_working_with_lsp_returns_beacon():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time(), "tool_name": "LSP"})
    state = d._compute_display_state()
    assert state["anims"] == ["beacon"]


def test_working_with_mcp_tool_returns_beacon():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time(), "tool_name": "mcp__firebase__list_projects"})
    state = d._compute_display_state()
    assert state["anims"] == ["beacon"]


def test_working_with_unknown_tool_returns_typing():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time(), "tool_name": "SomeFutureTool"})
    state = d._compute_display_state()
    assert state["anims"] == ["typing"]


def test_working_no_tool_name_returns_typing():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time()})
    state = d._compute_display_state()
    assert state["anims"] == ["typing"]


def test_subagent_override_trumps_tool_name():
    d = make_daemon()
    _add_session(d, "s1", {
        "state": "working",
        "last_event": time.time(),
        "tool_name": "Read",
        "subagents": {"agent-1"},
    })
    state = d._compute_display_state()
    assert state["anims"] == ["conducting"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py::test_working_with_bash_tool_returns_building tests/test_session_state.py::test_working_with_read_tool_returns_debugger tests/test_session_state.py::test_subagent_override_trumps_tool_name -v`

Expected: FAIL

- [ ] **Step 3: Add TOOL_ANIMATION_MAP and _tool_to_anim to daemon.py**

At the top of `host/clawd_tank_daemon/daemon.py`, after the imports (around line 22), add:

```python
TOOL_ANIMATION_MAP = {
    "Edit": "typing",
    "Write": "typing",
    "NotebookEdit": "typing",
    "Read": "debugger",
    "Grep": "debugger",
    "Glob": "debugger",
    "Bash": "building",
    "Agent": "conducting",
    "WebSearch": "wizard",
    "WebFetch": "wizard",
    "LSP": "beacon",
}


def _tool_to_anim(tool_name: str) -> str:
    if tool_name and tool_name.startswith("mcp__"):
        return "beacon"
    return TOOL_ANIMATION_MAP.get(tool_name, "typing")
```

- [ ] **Step 4: Update _compute_display_state to use tool-aware mapping**

In `host/clawd_tank_daemon/daemon.py`, change `_compute_display_state()` (lines 215-219):

From:
```python
            if state["state"] == "working" or session_subagents:
                if session_subagents:
                    anims.append("building")
                else:
                    anims.append("typing")
```

To:
```python
            if state["state"] == "working" or session_subagents:
                if session_subagents:
                    anims.append("conducting")
                else:
                    anims.append(_tool_to_anim(state.get("tool_name", "")))
```

- [ ] **Step 5: Update existing tests that expect "building" for subagent sessions**

The subagent animation changes from `"building"` to `"conducting"`. Update these 6 existing tests in `host/tests/test_session_state.py` — change all assertions from `"building"` to `"conducting"`:

- `test_idle_session_with_subagents_counts_as_building` → rename to `test_idle_session_with_subagents_counts_as_conducting`, assert `["conducting"]`
- `test_multiple_sessions_with_subagents` → assert `["conducting", "typing"]` (was `["building", "typing"]`)
- `test_working_session_with_subagents_counts_once` → assert `["conducting"]` (was `["building"]`)
- `test_subagent_lifecycle` → assert `["conducting"]` in both places (was `["building"]`)
- `test_display_state_working_with_subagents_becomes_building` → rename to `test_display_state_working_with_subagents_becomes_conducting`, assert `["conducting"]`

Also update `test_display_state_to_v1_building_counts_as_working` in `host/tests/test_protocol.py` to also test `"conducting"` counting as working (already covered by the new test `test_display_state_to_v1_wizard_counts_as_working` which uses `"conducting"`).

- [ ] **Step 6: Store tool_name in _update_session_state**

In `host/clawd_tank_daemon/daemon.py`, update the `tool_use` handler (lines 253-256). Change the method signature to accept `msg`:

Change line 238:
```python
    def _update_session_state(self, event: str, hook: str, session_id: str, agent_id: str = "") -> bool:
```
to:
```python
    def _update_session_state(self, event: str, hook: str, session_id: str, agent_id: str = "", tool_name: str = "") -> bool:
```

Change lines 253-256:
```python
        elif event == "tool_use":
            self._session_states.setdefault(session_id, {"state": "working", "last_event": now})
            self._session_states[session_id]["state"] = "working"
            self._session_states[session_id]["last_event"] = now
```
to:
```python
        elif event == "tool_use":
            self._session_states.setdefault(session_id, {"state": "working", "last_event": now})
            self._session_states[session_id]["state"] = "working"
            self._session_states[session_id]["tool_name"] = tool_name
            self._session_states[session_id]["last_event"] = now
```

Update the call site in `_handle_message()` (line 151):
```python
        changed = self._update_session_state(event, hook, session_id, msg.get("agent_id", ""), msg.get("tool_name", ""))
```

- [ ] **Step 7: Run all session state tests**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`

Expected: ALL PASS

- [ ] **Step 8: Run full test suite to check for regressions**

Run: `cd host && .venv/bin/pytest -v`

Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat: tool-aware animation mapping in daemon"
```

---

### Task 3: Update embedded hook script in hooks.py

**Files:**
- Modify: `host/clawd_tank_menubar/hooks.py:42-43`

- [ ] **Step 1: Update the embedded NOTIFY_SCRIPT**

In `host/clawd_tank_menubar/hooks.py`, find the `PreToolUse` handler in the `hook_to_message` function inside `NOTIFY_SCRIPT` (around line 42-43):

From:
```python
        if event_name == "PreToolUse":
            return {"event": "tool_use", "session_id": session_id}
```

To:
```python
        if event_name == "PreToolUse":
            return {"event": "tool_use", "session_id": session_id, "tool_name": hook.get("tool_name", "")}
```

- [ ] **Step 2: Run menubar tests**

Run: `cd host && .venv/bin/pytest tests/test_menubar.py -v`

Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add host/clawd_tank_menubar/hooks.py
git commit -m "feat: pass tool_name in embedded hook script"
```

---

### Task 4: Add 4 new animation enums to firmware

**Files:**
- Modify: `firmware/main/scene.h:6-22`
- Modify: `firmware/main/ble_service.c:57-65`
- Modify: `simulator/sim_ble_parse.c:11-19`
- Modify: `firmware/main/scene.c:1372-1375` (anim_id_to_name)

- [ ] **Step 1: Add enum values to scene.h**

In `firmware/main/scene.h`, add 4 new values before `CLAWD_ANIM_MINI_CLAWD`:

```c
typedef enum {
    CLAWD_ANIM_IDLE,
    CLAWD_ANIM_ALERT,
    CLAWD_ANIM_HAPPY,
    CLAWD_ANIM_SLEEPING,
    CLAWD_ANIM_DISCONNECTED,
    CLAWD_ANIM_THINKING,
    CLAWD_ANIM_TYPING,
    CLAWD_ANIM_JUGGLING,
    CLAWD_ANIM_BUILDING,
    CLAWD_ANIM_CONFUSED,
    CLAWD_ANIM_DIZZY,
    CLAWD_ANIM_SWEEPING,
    CLAWD_ANIM_WALKING,
    CLAWD_ANIM_GOING_AWAY,
    CLAWD_ANIM_DEBUGGER,
    CLAWD_ANIM_WIZARD,
    CLAWD_ANIM_CONDUCTING,
    CLAWD_ANIM_BEACON,
    CLAWD_ANIM_MINI_CLAWD,
} clawd_anim_id_t;
```

- [ ] **Step 2: Add parse entries in ble_service.c**

In `firmware/main/ble_service.c`, add 4 lines to `parse_anim_name()` before `return -1`:

```c
    if (strcmp(str, "debugger") == 0)   return CLAWD_ANIM_DEBUGGER;
    if (strcmp(str, "wizard") == 0)     return CLAWD_ANIM_WIZARD;
    if (strcmp(str, "conducting") == 0) return CLAWD_ANIM_CONDUCTING;
    if (strcmp(str, "beacon") == 0)     return CLAWD_ANIM_BEACON;
```

- [ ] **Step 3: Mirror in sim_ble_parse.c**

In `simulator/sim_ble_parse.c`, add the same 4 lines to `parse_anim_name()` before `return -1`:

```c
    if (strcmp(str, "debugger") == 0)   return CLAWD_ANIM_DEBUGGER;
    if (strcmp(str, "wizard") == 0)     return CLAWD_ANIM_WIZARD;
    if (strcmp(str, "conducting") == 0) return CLAWD_ANIM_CONDUCTING;
    if (strcmp(str, "beacon") == 0)     return CLAWD_ANIM_BEACON;
```

- [ ] **Step 4: Update anim_id_to_name in scene.c**

In `firmware/main/scene.c:1372-1375`, update the names array:

```c
    static const char *names[] = {
        "idle", "alert", "happy", "sleeping", "disconnected",
        "thinking", "typing", "juggling", "building", "confused",
        "dizzy", "sweeping", "walking", "going_away",
        "debugger", "wizard", "conducting", "beacon",
        "mini_clawd"
    };
```

- [ ] **Step 5: Commit**

```bash
git add firmware/main/scene.h firmware/main/ble_service.c simulator/sim_ble_parse.c firmware/main/scene.c
git commit -m "feat: add debugger/wizard/conducting/beacon animation enums"
```

---

### Task 5: Generate sprite assets via pipeline

**Files:**
- Create: `firmware/main/assets/clawd_debugger.h`
- Create: `firmware/main/assets/clawd_wizard.h`
- Create: `firmware/main/assets/clawd_conducting.h`
- Create: `firmware/main/assets/clawd_beacon.h`

This task uses the sprite pipeline tools. Each animation goes through: SVG → PNG frames → RLE header → auto-crop.

- [ ] **Step 1: Generate debugger sprite**

```bash
python tools/svg2frames.py assets/svg-animations/clawd-working-debugger.svg /tmp/debugger-frames/ --fps 8 --duration auto --scale 4
python tools/png2rgb565.py /tmp/debugger-frames/ firmware/main/assets/clawd_debugger.h --name clawd_debugger
```

- [ ] **Step 2: Generate wizard sprite**

```bash
python tools/svg2frames.py assets/svg-animations/clawd-working-wizard.svg /tmp/wizard-frames/ --fps 8 --duration auto --scale 4
python tools/png2rgb565.py /tmp/wizard-frames/ firmware/main/assets/clawd_wizard.h --name clawd_wizard
```

- [ ] **Step 3: Generate conducting sprite**

```bash
python tools/svg2frames.py assets/svg-animations/clawd-working-conducting.svg /tmp/conducting-frames/ --fps 8 --duration auto --scale 4
python tools/png2rgb565.py /tmp/conducting-frames/ firmware/main/assets/clawd_conducting.h --name clawd_conducting
```

- [ ] **Step 4: Generate beacon sprite**

```bash
python tools/svg2frames.py assets/svg-animations/clawd-working-beacon.svg /tmp/beacon-frames/ --fps 8 --duration auto --scale 4
python tools/png2rgb565.py /tmp/beacon-frames/ firmware/main/assets/clawd_beacon.h --name clawd_beacon
```

- [ ] **Step 5: Auto-crop all sprites**

```bash
python tools/crop_sprites.py
```

- [ ] **Step 6: Commit**

```bash
git add firmware/main/assets/clawd_debugger.h firmware/main/assets/clawd_wizard.h firmware/main/assets/clawd_conducting.h firmware/main/assets/clawd_beacon.h
git commit -m "feat: add debugger/wizard/conducting/beacon sprite assets"
```

---

### Task 6: Wire sprites into scene.c animation table

**Files:**
- Modify: `firmware/main/scene.c:19-33` (includes)
- Modify: `firmware/main/scene.c:47-59` (frame timing defines)
- Modify: `firmware/main/scene.c:74-200` (anim_defs table)

- [ ] **Step 1: Add includes for new sprite headers**

In `firmware/main/scene.c`, alongside the existing sprite includes, add:

```c
#include "assets/clawd_debugger.h"
#include "assets/clawd_wizard.h"
#include "assets/clawd_conducting.h"
#include "assets/clawd_beacon.h"
```

- [ ] **Step 2: Add frame timing defines**

After `GOING_AWAY_FRAME_MS`:

```c
#define DEBUGGER_FRAME_MS   (1000 / 8)   /* 125ms @ 8fps */
#define WIZARD_FRAME_MS     (1000 / 8)   /* 125ms @ 8fps */
#define CONDUCTING_FRAME_MS (1000 / 8)   /* 125ms @ 8fps */
#define BEACON_FRAME_MS     (1000 / 8)   /* 125ms @ 8fps */
```

- [ ] **Step 3: Add entries to anim_defs table**

After `CLAWD_ANIM_GOING_AWAY` entry, add:

```c
    [CLAWD_ANIM_DEBUGGER] = {
        .rle_data = clawd_debugger_rle_data,
        .frame_offsets = clawd_debugger_frame_offsets,
        .frame_count = CLAWD_DEBUGGER_FRAME_COUNT,
        .frame_ms = DEBUGGER_FRAME_MS,
        .looping = true,
        .width = CLAWD_DEBUGGER_WIDTH,
        .height = CLAWD_DEBUGGER_HEIGHT,
        .y_offset = -8,
    },
    [CLAWD_ANIM_WIZARD] = {
        .rle_data = clawd_wizard_rle_data,
        .frame_offsets = clawd_wizard_frame_offsets,
        .frame_count = CLAWD_WIZARD_FRAME_COUNT,
        .frame_ms = WIZARD_FRAME_MS,
        .looping = true,
        .width = CLAWD_WIZARD_WIDTH,
        .height = CLAWD_WIZARD_HEIGHT,
        .y_offset = -8,
    },
    [CLAWD_ANIM_CONDUCTING] = {
        .rle_data = clawd_conducting_rle_data,
        .frame_offsets = clawd_conducting_frame_offsets,
        .frame_count = CLAWD_CONDUCTING_FRAME_COUNT,
        .frame_ms = CONDUCTING_FRAME_MS,
        .looping = true,
        .width = CLAWD_CONDUCTING_WIDTH,
        .height = CLAWD_CONDUCTING_HEIGHT,
        .y_offset = -8,
    },
    [CLAWD_ANIM_BEACON] = {
        .rle_data = clawd_beacon_rle_data,
        .frame_offsets = clawd_beacon_frame_offsets,
        .frame_count = CLAWD_BEACON_FRAME_COUNT,
        .frame_ms = BEACON_FRAME_MS,
        .looping = true,
        .width = CLAWD_BEACON_WIDTH,
        .height = CLAWD_BEACON_HEIGHT,
        .y_offset = -8,
    },
```

- [ ] **Step 4: Build simulator to verify compilation**

```bash
cd simulator && cmake -B build && cmake --build build
```

Expected: Build succeeds with no errors

- [ ] **Step 5: Commit**

```bash
git add firmware/main/scene.c
git commit -m "feat: wire debugger/wizard/conducting/beacon into scene animation table"
```

---

### Task 7: End-to-end verification

**Files:** No new files — verification only.

- [ ] **Step 1: Run all Python tests**

```bash
cd host && .venv/bin/pytest -v
```

Expected: ALL PASS

- [ ] **Step 2: Run C unit tests**

```bash
cd firmware/test && make test
```

Expected: ALL PASS

- [ ] **Step 3: Test with simulator interactively**

```bash
./simulator/build/clawd-tank-sim --headless --events 'connect; wait 500; set_sessions anims=["debugger"] ids=[1] subagents=0; wait 2000; set_sessions anims=["wizard"] ids=[1] subagents=0; wait 2000; set_sessions anims=["conducting"] ids=[1] subagents=0; wait 2000; set_sessions anims=["beacon"] ids=[1] subagents=0; wait 2000; disconnect' --screenshot-dir /tmp/tool-anim-shots/ --screenshot-on-event
```

Verify each animation renders correctly in the screenshots.

- [ ] **Step 4: Build and install the app**

```bash
cd host && ./build.sh --install
```

Kill and relaunch Clawd Tank. Verify the new animations appear when Claude Code uses different tools.
