# Tool-Aware Animations Design

## Problem

Every `PreToolUse` hook event is treated identically — the `tool_name` field is discarded in `protocol.py`, and all tools produce the same `"working"` session state, which always maps to the `typing` animation (or `building` when subagents are active). There is no visual distinction between Claude reading files, editing code, running bash commands, searching the web, or spawning subagents.

Zellaude (a Zellij status-bar plugin for Claude Code) demonstrates that 6 tool categories provide meaningful differentiation. We have an even richer animation library with several unused SVG animations that naturally map to specific tool types.

## Solution

Preserve `tool_name` from the `PreToolUse` hook payload through the daemon pipeline and map it to tool-specific animations. The wire protocol (`set_sessions`) already sends animation name strings per session — we just send more varied names instead of always `"typing"`.

### Approach: Tool-name mapping in daemon only

All mapping logic lives in `daemon.py`. The firmware learns 4 new animation names via `parse_anim_name()`. No protocol version change needed.

## Tool-to-Animation Mapping

| Tool Name(s) | Animation | Visual Description |
|---|---|---|
| `Edit`, `Write`, `NotebookEdit` | `typing` | Furious typing at laptop with floating data packets |
| `Read`, `Grep`, `Glob` | `debugger` | Sneaking with magnifying glass, searching/inspecting |
| `Bash` | `building` | Hard hat + hammer on anvil, sparks flying |
| `Agent` (Task) | `conducting` | Arms waving gracefully, data streaming overhead |
| `WebSearch`, `WebFetch` | `wizard` | Floating with wizard hat + wand, magic sparkles |
| `LSP`, MCP tools (`mcp__*`) | `beacon` | Antenna with radio waves radiating outward |
| Any other tool | `typing` | Safe fallback for unrecognized tool names |

**Subagent override:** When a session has active subagents (tracked via `SubagentStart`/`SubagentStop`), the animation is `conducting` regardless of the current tool — subagent orchestration takes visual priority.

## Data Flow Changes

### 1. Hook handler (`host/clawd-tank-notify` + `host/clawd_tank_menubar/hooks.py`)

Extract `tool_name` from the `PreToolUse` hook payload and include it in the daemon message. **Both** the standalone script and the embedded `NOTIFY_SCRIPT` in `hooks.py` must be updated — the menu bar app deploys the embedded copy.

```python
# Before
{"event": "tool_use", "session_id": "..."}

# After
{"event": "tool_use", "session_id": "...", "tool_name": "Bash"}
```

### 2. Protocol conversion (`host/clawd_tank_daemon/protocol.py`)

Pass through `tool_name` from the hook payload into the daemon message for `PreToolUse` events.

### 3. Daemon state management (`host/clawd_tank_daemon/daemon.py`)

- Store `tool_name` on the session state dict: `self._session_states[session_id]["tool_name"] = msg.get("tool_name", "")`
- Pass the full `msg` dict (or `tool_name` field) through `_handle_message()` → `_update_session_state()` for `tool_use` events
- In `_compute_display_state()`, determine animation with this priority:
  1. If session has active subagents → `"conducting"` (short-circuit, skip tool lookup)
  2. Else if session state is `"working"` → `_tool_to_anim(session["tool_name"])`
  3. Else → existing state-based mapping (thinking, confused, dizzy, idle)

Tool-name to animation mapping function:

```python
TOOL_ANIMATION_MAP = {
    # Edit/Write tools
    "Edit": "typing",
    "Write": "typing",
    "NotebookEdit": "typing",
    # Search/Read tools
    "Read": "debugger",
    "Grep": "debugger",
    "Glob": "debugger",
    # Execute tools
    "Bash": "building",
    # Agent tools
    "Agent": "conducting",
    # Web tools
    "WebSearch": "wizard",
    "WebFetch": "wizard",
    # LSP
    "LSP": "beacon",
}

def _tool_to_anim(tool_name: str) -> str:
    if tool_name and tool_name.startswith("mcp__"):
        return "beacon"
    return TOOL_ANIMATION_MAP.get(tool_name, "typing")
```

### 4. Firmware parser (`firmware/main/ble_service.c` + `simulator/sim_ble_parse.c`)

Add 4 new entries to `parse_anim_name()`:

| Wire Name | Enum Value |
|---|---|
| `"debugger"` | `CLAWD_ANIM_DEBUGGER` |
| `"wizard"` | `CLAWD_ANIM_WIZARD` |
| `"conducting"` | `CLAWD_ANIM_CONDUCTING` |
| `"beacon"` | `CLAWD_ANIM_BEACON` |

### 5. Firmware scene (`firmware/main/scene.h` + `scene.c`)

Add 4 new animation enum values with sprite metadata (frame count, fps, looping flag). Sprite data generated via the existing pipeline. Also update `anim_id_to_name()` string array for debug/`query_state` JSON output.

## Sprite Pipeline

Source SVGs already exist in `assets/svg-animations/`:

| SVG File | Animation Name |
|---|---|
| `clawd-working-debugger.svg` | `debugger` |
| `clawd-working-wizard.svg` | `wizard` |
| `clawd-working-conducting.svg` | `conducting` |
| `clawd-working-beacon.svg` | `beacon` |

Pipeline steps per animation:

```bash
# 1. Render SVG to PNG frames
python tools/svg2frames.py assets/svg-animations/clawd-working-debugger.svg frames/debugger/ --fps 8 --duration auto --scale 4

# 2. Convert to RLE-compressed RGB565 header
python tools/png2rgb565.py frames/debugger/ firmware/main/assets/clawd_debugger.h --name clawd_debugger

# 3. Auto-crop (run once after all sprites)
python tools/crop_sprites.py
```

### Memory Budget

- Each frame (~15x17px after crop): ~500-800 bytes RLE-compressed
- Per animation (8-16 frames at 8fps): ~4-12KB flash
- 4 new animations total: ~16-48KB additional flash
- 8MB flash available: negligible impact
- No additional RAM: frame buffers are lazy-allocated per active slot, and we're not adding slots

Default `y_offset = -8` for all new sprites.

## Edge Cases

**Rapid tool switching:** Each `PreToolUse` updates the session's tool sub-state and sends a new `set_sessions`. Firmware handles animation changes in-place for the same slot (no walk-in/walk-out). No debouncing — seeing the animation change is the feature.

**Thinking between tools:** We do not handle `PostToolUse` today. The session stays in `working` (with its tool sub-state) until the next event changes it. This avoids rapid thinking↔tool flickering.

**Unknown/custom tools:** MCP tools arrive as `mcp__server__tool_name` — any tool starting with `mcp__` maps to `beacon`. All other unrecognized tools fall back to `typing`.

**V1 protocol compatibility:** V1 transports receive `set_status` with aggregated intensity strings (`working_1`/`working_2`/`working_3`). The `display_state_to_v1_payload()` function counts working sessions by matching animation names against `("typing", "building")`. This set must be expanded to include the 4 new animation names (`"debugger"`, `"wizard"`, `"conducting"`, `"beacon"`) so that v1 transports correctly count all tool-active sessions as "working". The v1 `WORKING_ANIMS` set should be:

```python
WORKING_ANIMS = {"typing", "building", "debugger", "wizard", "conducting", "beacon"}
```

**`CLAWD_ANIM_JUGGLING` retirement (v2):** The `juggling` animation was used for v1 intensity tier `working_2` (2 concurrent sessions). With v2 tool-aware animations, each session gets its own tool-specific animation. `juggling` remains available in the firmware enum and continues to work for v1 `set_status`, but no v2 code path produces it.

**Session persistence:** The `tool_name` sub-state is ephemeral — not saved to `sessions.json`. On daemon restart, sessions restore as `working` → `typing` until the next `PreToolUse` event arrives. Active sessions will send new tool events within seconds.

**Firmware fallback (old firmware):** Old firmware receiving an unknown animation name from `parse_anim_name()` returns -1, and the session entry is **skipped** — the session will not be rendered (the crab disappears from the display). This means firmware must be updated before or alongside the daemon update. Deploy firmware first, then update the daemon to send new animation names.
