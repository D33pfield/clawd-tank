# Subagent & Multi-Session Display Design

## Problem

The daemon tracks per-session subagent IDs, but the display has no visual distinction between a session working alone and one with multiple subagents. The intensity tiers (Typing/Juggling/Building) only count sessions, not what's happening within each session. Additionally, multiple simultaneous sessions have no clear visual representation beyond the intensity tier mapping.

## Design

### Core Concepts

- **Full Clawds represent sessions.** Each active session gets its own Clawd sprite on the display with the appropriate state animation (typing, thinking, idle, etc.).
- **A HUD counter represents subagents.** A mini-crab icon + pixel-art "×N" counter in the top-left corner shows the total number of active subagents across all sessions. Only visible when count > 0.
- **Badges handle overflow.** When there are more sessions than fit on screen, a "+N" badge in the top-right shows the additional count.

### Full Screen Layout (320×172, no notifications)

#### Session Display

Sprite sizes vary by animation (not a fixed size):

| Animation | Native Size | y_offset |
|-----------|------------|----------|
| Idle, Alert, Building, Confused, Juggling, Sweeping, Thinking, Typing | 180×180px | 8 |
| Happy | 160×160px | 28 |
| Sleeping | 160×160px | 8 |

These sprites are larger than the 320×172 display — they're designed to extend beyond the visible area (clipped by LVGL container). The `y_offset` in the firmware pushes them below the scene bottom, and the top portion may extend above the scene.

**All sprites render at native size — no scaling.** For multi-session layouts, the Clawds' X centers are spaced evenly across the 320px width. The character body occupies only a fraction of the sprite canvas (roughly 60-70px of body within 180px canvas), so overlap between adjacent sprites is mostly in the transparent areas and looks natural.

| Active Sessions | X Centers (320px width) |
|----------------|------------------------|
| 1 | 160 (centered) |
| 2 | 107, 213 |
| 3 | 80, 160, 240 |
| 4 | 64, 128, 192, 256 |
| 5+ | 4 Clawds at 64, 128, 192, 256 + blue "+N" badge top-right |

Note: Disconnected animation (200×160px) is not relevant here — it's a whole-device state shown only when BLE is disconnected (no sessions exist).

Each Clawd displays the animation matching its session state independently.

#### Session Animation Mapping

Each session's state maps to an animation:
- `working` with active subagents → `building` (visually connotes delegated/parallel work)
- `working` without subagents → `typing`
- `thinking` → `thinking`
- `confused` → `confused`
- `idle` → `idle`
- `registered` → `idle`

#### Subagent HUD Counter

- **Position:** Top-left corner of the scene (x=4, y=4 in display coordinates)
- **Components:** Animated mini-crab sprite icon + pixel-art "×N" text in yellow (#FFC107)
- **Visibility:** Only shown when total subagent count > 0; hidden when no subagents are active
- **Count:** Total subagents across all sessions (not per-session)
- **Font:** Custom pixel-art bitmap digits (5×5 grid per character, rendered as filled rectangles)

#### Session Overflow Badge

- **Position:** Top-right corner of the scene
- **Style:** Blue (#0082FC) background badge with pixel-art "+N" text
- **Visibility:** Only shown when active sessions > 4
- **Value:** Number of sessions beyond the 4 visible (e.g., 6 sessions → "+2")
- **Badge format depends on scene width:** In full screen, the badge shows "+N" (overflow beyond 4 visible). In narrow screen, the badge shows "×N" (total session count, since only 1 is visible). The firmware determines which format to use based on the current scene width — no extra data from the daemon needed.

#### Clock

The time display moves to **horizontally centered** in full screen mode to avoid conflicting with the HUD counter (top-left) and overflow badge (top-right).

### Narrow Screen Layout (107×172, with notifications)

When notifications are active, the scene width shrinks to 107px. Only 1 Clawd fits.

| Element | Behavior |
|---------|----------|
| Clawd | 1 Clawd at native size (no scaling). Displays the highest-priority session animation. |
| Subagent HUD | Mini-crab icon + yellow "×N" in top-left corner (same as full screen) |
| Session badge | Blue "×N" badge in top-right of scene area, showing **total** session count. Only shown when sessions > 1. |

In narrow mode, the single visible Clawd shows `anims[0]` (the oldest session). The firmware picks this from the `set_sessions` payload — no special priority logic needed since the daemon already sends the full list.

### Rendering Approach

All sprites render at their native size (160-200px). No runtime scaling. Multiple Clawds are positioned by setting their X center and relying on LVGL's clipping to handle any overflow beyond the 320×172 scene. The sprites' transparent canvas areas allow natural overlap without visual artifacts.

### Resource Budget

**Frame buffers (PSRAM):**
- Current: 1 × 180×180×4 = 129,600 bytes (~127KB)
- Max (4 sessions): 4 × 180×180×4 = 518,400 bytes (~506KB)
- Mini-crab HUD icon: 1 × 16×16×4 = 1,024 bytes
- Total max: ~507KB PSRAM — within the 4MB PSRAM budget but significant
- Optimization: allocate frame buffers lazily (only when a slot is activated), free when deactivated. Steady-state for 1 session = ~127KB (same as today).
- Note: `ensure_frame_buf()` already handles dynamic reallocation; the buffer grows to fit the largest animation's frame size.
- No scaling overhead — all sprites render at native size.

**LVGL objects:**
- Current: 1 sprite image + background objects
- Max: 4 sprite images + HUD overlay container + counter label + badge label
- Additional ~10 LVGL objects — negligible

**RLE sprite data (flash):**
- Current: ~11 animations × variable frames — already in flash, no change
- Mini-crab sprite: ~8 frames × ~200 bytes = ~1.6KB additional

### Protocol Changes

#### Protocol Versioning

A new read-only BLE GATT characteristic exposes the firmware's protocol version. The daemon reads it on connect and adapts its behavior accordingly.

**Firmware side:**
- New GATT characteristic UUID: TBD (to be generated during implementation, added alongside existing `notif_chr` and `config_chr`)
- Returns a simple integer as a UTF-8 string (e.g., `"2"`)
- Current firmware (before this feature) has no version characteristic → daemon treats absence as version 1

**Daemon side:**
- On transport connect, after `_sync_time_for()`, read the protocol version
- Store per-transport: `self._transport_versions[name] = version`
- Use the version to decide which actions to send

**Simulator TCP side:**
- The simulator is always built from the same source as the firmware, so the daemon assumes it supports the latest protocol version. No version query needed.

**Version changelog:** See `docs/protocol-changelog.md` for the full version history.

#### New `set_sessions` Action (Protocol v2)

A single new action replaces `set_status` for the multi-session case. The daemon sends the full session state in one payload:

```json
{
  "action": "set_sessions",
  "anims": ["typing", "thinking"],
  "subagents": 3
}
```

- `anims`: Ordered list of animation names, one per visible session. **Ordered by session arrival time** (oldest first) — this preserves spatial consistency so each Clawd stays in the same position across updates. Max 4 entries. When there are more than 4 sessions, the daemon keeps the 4 oldest and puts the rest in `overflow`. If a middle session ends, later sessions shift down (e.g., sessions [A, B, C] → B ends → [A, C]). Valid values: `idle`, `typing`, `thinking`, `building`, `confused`.
- `subagents`: Total active subagent count across all sessions.

The firmware derives session count from `len(anims)`, overflow count from the optional `overflow` field:

```json
{
  "action": "set_sessions",
  "anims": ["typing", "thinking", "building", "idle"],
  "subagents": 5,
  "overflow": 2
}
```

The `overflow` field is only present when there are more sessions than the 4 visible.

**Backwards compatibility:** The daemon checks the transport's protocol version on connect:
- **v1 (no version characteristic):** Daemon falls back to `set_status` with the legacy single-animation format (`working_1`, `working_2`, etc.)
- **v2+:** Daemon sends `set_sessions` with the full per-session data

**Existing special cases preserved:**
- `sweeping` (PreCompact) is still sent as a `set_status` oneshot before the regular state update — applies to all visible Clawds
- `sleeping` and `disconnected` remain as `set_status` since they're whole-device states, not per-session
- Receiving `set_sessions` implicitly clears sleeping/disconnected state (sessions exist = device is awake and connected)

#### Firmware Event System Changes

The existing `ble_evt_t` struct carries a single `uint8_t status` for `BLE_EVT_SET_STATUS`. A new event type is needed for the richer `set_sessions` data:

```c
#define MAX_VISIBLE_SESSIONS 4

typedef struct {
    uint8_t type;  /* BLE_EVT_SET_STATUS, BLE_EVT_SET_SESSIONS, etc. */
    union {
        /* Existing: BLE_EVT_SET_STATUS */
        uint8_t status;

        /* New: BLE_EVT_SET_SESSIONS */
        struct {
            uint8_t anim_count;
            uint8_t anims[MAX_VISIBLE_SESSIONS]; /* clawd_anim_id_t values */
            uint8_t subagent_count;
            uint8_t overflow;
        } sessions;

        /* Existing notification fields... */
        // ...
    };
    char id[49];
    char project[65];
    char message[129];
} ble_evt_t;
```

The `parse_notification_json()` function in `ble_service.c` (and `sim_ble_parse.c`) parses the `set_sessions` action, maps animation name strings to `clawd_anim_id_t` enum values, and posts a `BLE_EVT_SET_SESSIONS` event to the queue. The UI manager handles this new event type by updating the scene's slot array.

#### BLE MTU Analysis

Worst-case payload (4 sessions, overflow, high subagent count):
```json
{"action":"set_sessions","anims":["typing","thinking","building","idle"],"subagents":12,"overflow":3}
```
Length: ~95 bytes. Well within the 256-byte BLE MTU limit.

#### Simulator TCP Parser

`simulator/sim_ble_parse.c` needs to be updated alongside `firmware/main/ble_service.c` to parse the new `set_sessions` action. Both files share the same JSON parsing approach (cJSON) so the implementation is mirrored.

### Firmware Scene Changes

#### Scene Layout Manager

The scene needs a layout manager that:
1. Accepts a list of session animations + subagent count + overflow count
2. Computes Clawd X positions based on session count (evenly spaced centers)
3. Renders 1-4 Clawd sprites with independent animations
4. Renders the HUD counter overlay when subagents > 0
5. Renders the overflow badge when overflow > 0
6. Positions the clock centered when in full screen

#### Multi-Sprite Rendering

Currently the scene has a single `sprite_img` LVGL object, `frame_buf`, `cur_anim`, and `frame_idx`. These expand to arrays of up to 4:

```c
#define MAX_VISIBLE_SESSIONS 4

typedef struct {
    lv_obj_t *sprite_img;
    uint8_t  *frame_buf;
    int       frame_buf_size;
    clawd_anim_id_t cur_anim;
    clawd_anim_id_t fallback_anim;
    int frame_idx;
    bool active;
} clawd_slot_t;
```

The `scene_t` struct gains `clawd_slot_t slots[MAX_VISIBLE_SESSIONS]` and the existing single-sprite functions (`scene_tick`, `decode_and_apply_frame`, etc.) are refactored to iterate over active slots.

Frame buffers are allocated from PSRAM on demand (when a slot becomes active) and freed when no longer needed, keeping the steady-state memory usage at 1 buffer for the common single-session case.

Per-slot `fallback_anim` works the same as the current single-sprite system: when a oneshot (e.g., alert, happy) finishes, the slot returns to its fallback animation. The `sweeping` oneshot (sent via `set_status`) triggers on all active slots simultaneously — each slot plays the sweeping animation then returns to its own fallback.

#### HUD Overlay

A new LVGL layer on top of the scene for:
- Mini-crab icon (small RLE-compressed sprite, same pipeline as Clawd sprites)
- Pixel-art counter text (rendered via LVGL canvas `lv_canvas_draw_rect()` calls, or a pre-rendered digit atlas)
- Session overflow badge (same pixel-art text rendering, blue color)

#### Transition Animations

When a **new session starts** (Clawd enters):
1. New Clawd appears off-screen to the right (x > 320) playing a **crab-walking animation**
2. All existing Clawds switch to the crab-walking animation and slide toward their new X positions (LVGL `lv_anim_t`, ease-out, ~600ms)
3. The new Clawd walks in from the right to its target X position over the same duration
4. Once all Clawds reach their positions, each switches to its session-state animation (typing, thinking, etc.)
5. **Walking direction:** Each Clawd faces the direction it's moving. Moving left = native left-facing sprite. Moving right = horizontally flipped sprite.

When a **session ends** (Clawd exits):
1. The departing Clawd fades out over 400ms (opacity → 0)
2. Remaining Clawds switch to crab-walking animation and slide to their new X positions (~600ms, ease-out)
3. Once in position, each resumes its session-state animation

**Walking animation:** A new sprite showing Clawd crab-walking sideways. If not yet available, fall back to sliding the current animation without switching to a walk cycle.

**HUD counter changes:** Instant show/hide — no animation needed for HUD elements.

### Daemon Changes

#### Display State Computation

`_compute_display_state()` returns a dict instead of a string:

```python
MAX_VISIBLE = 4

def _compute_display_state(self) -> dict:
    if not self._session_states:
        return {"status": "sleeping"}

    # Session order is preserved by _session_order list (maintained on
    # session_start/subagent_start — append new, remove on SessionEnd).
    # This ensures Clawds stay in consistent positions across updates.
    anims = []
    for sid in self._session_order[:MAX_VISIBLE]:
        s = self._session_states.get(sid)
        if not s:
            continue
        has_subs = bool(s.get("subagents"))
        if s["state"] == "working":
            anims.append("building" if has_subs else "typing")
        elif s["state"] == "thinking":
            anims.append("thinking")
        elif s["state"] == "confused":
            anims.append("confused")
        else:
            anims.append("idle")

    total_subagents = sum(
        len(s.get("subagents", set()))
        for s in self._session_states.values()
    )

    total_sessions = len(self._session_states)
    overflow = max(0, total_sessions - MAX_VISIBLE)

    result = {"anims": anims, "subagents": total_subagents}
    if overflow > 0:
        result["overflow"] = overflow
    return result
```

The daemon maintains `self._session_order: list[str]` — an ordered list of session IDs by arrival time. New sessions are appended; ended sessions are removed (later sessions shift down). This list is the source of truth for the `anims` array ordering, ensuring spatial consistency on the display.

Change detection in `_broadcast_display_state_if_changed()`: compare the new dict against `self._last_display_state` using `==`. `_last_display_state` changes from `str` to `dict`.

### Scene Width Transitions

When transitioning between full screen (320px) and narrow (107px, notifications present):
- **Full → Narrow:** Remove extra Clawds (fade out), keep highest-priority one, add session badge if sessions > 1
- **Narrow → Full:** Spawn additional Clawds at their positions (walk in from right), remove session badge

### Edge Cases

- **Session ends while Clawd is visible:** Fade out that Clawd, remaining ones walk to new positions (~600ms)
- **Subagent count changes rapidly:** No debounce needed — the HUD counter is just a number redraw, cheap
- **All subagents stop:** Hide the HUD counter instantly
- **0 sessions (sleeping):** No Clawds, no HUD, sleeping animation as today
- **Disconnected:** Single disconnected Clawd as today, no HUD
- **Old firmware:** Daemon falls back to `set_status` with single animation string
- **`set_sessions` received while sleeping/disconnected:** Implicitly wakes the device (sessions exist = active)

### New Sprites & Assets

1. **Clawd crab-walking sprite** (same size as other Clawd animations, ~180×180)
   - Clawd shuffling sideways like a real crab — legs doing a lateral scuttle, body rocking side-to-side, eyes/face turned toward the direction of movement
   - Only left-facing variant needed; right-facing is achieved at runtime via LVGL horizontal flip
   - Entrance from the right uses the native left-facing sprite (walking toward the scene). Repositioning may need either direction depending on which way the Clawd needs to move.

2. **Mini-crab sprite** (~16×16 at original scale)
   - `mini-crab-typing` — bouncing body, waving arms (for HUD icon)
   - Same salmon-orange (#DE886D) as Clawd, 4 small legs, tiny arms, small black eyes
   - Must go through sprite pipeline: `svg2frames.py` → `png2rgb565.py` → RLE header

3. **Pixel-art bitmap font** — digits 0-9, × symbol, + symbol
   - 5×5 pixel grid per character
   - Rendered as filled rectangles at configurable pixel size
   - Yellow (#FFC107) for subagent counter, blue (#8BC6FC) for session badges
   - Implemented as a bitmap lookup table in C (same approach as the digit bitmasks in the layout editor)

## Tools Created

- `tools/scene-layout-editor.html` — Interactive layout editor for positioning sprites on the 320×172 display. Supports dragging, scale adjustment, animation selection, HUD counter with adjustable count, import/export of layouts, and presets. Reusable for future scene layout work.
- `assets/svg-animations/mini-crab-typing.svg` — Placeholder mini-crab animation SVG (needs sprite pipeline conversion for firmware)
- `assets/svg-animations/clawd-working-beacon.svg` — Signal beacon animation (saved for future use, not part of this feature)
