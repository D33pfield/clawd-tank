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

| Active Sessions | Layout | Rendered Size |
|----------------|--------|---------------|
| 1 | 1 Clawd, centered | 64×64px |
| 2 | 2 Clawds, side by side | 64×64px each |
| 3 | 3 Clawds, spread evenly | 52×52px each |
| 4 | 4 Clawds, spread evenly | 45×45px each |
| 5+ | 4 Clawds visible + blue "+N" badge top-right | 45×45px each |

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

#### Clock

The time display moves to **horizontally centered** in full screen mode to avoid conflicting with the HUD counter (top-left) and overflow badge (top-right).

### Narrow Screen Layout (107×172, with notifications)

When notifications are active, the scene width shrinks to 107px. Only 1 Clawd fits.

| Element | Behavior |
|---------|----------|
| Clawd | 1 Clawd at 64×64px. Displays the highest-priority session animation. |
| Subagent HUD | Mini-crab icon + yellow "×N" in top-left corner (same as full screen) |
| Session badge | Blue "×N" badge in top-right of scene area, showing **total** session count. Only shown when sessions > 1. |

Priority order for which session's animation to display: working > thinking > confused > idle (same as existing `_compute_display_state()` logic).

### Scaling Strategy

The current sprite assets are 64×64px RLE-compressed RGB565 frames. For sessions 3+ where Clawds render smaller than 64px:

**Approach: LVGL software scaling via `lv_image_set_scale()`.**

LVGL 9 supports runtime image scaling. The firmware decodes each frame to its existing 64×64 ARGB8888 buffer, then LVGL scales it down during rendering. This avoids creating separate sprite assets at each target size.

Performance considerations:
- Software bilinear scaling on ESP32-C6 RISC-V at 8fps
- For 3 sessions: 3 sprites × 52×52 target (downscaled from 64×64) at 8fps
- For 4 sessions: 4 sprites × 45×45 target at 8fps
- If scaling proves too slow, fall back to nearest-neighbor scaling (`LV_IMAGE_ALIGN_STRETCH` with disabled anti-aliasing) which is faster but blockier — acceptable at these small sizes

### Resource Budget

**Frame buffers (PSRAM):**
- Current: 1 × 64×64×4 = 16,384 bytes
- Max (4 sessions): 4 × 64×64×4 = 65,536 bytes (64KB)
- Mini-crab HUD icon: 1 × 16×16×4 = 1,024 bytes
- Total max: ~66KB PSRAM — well within the 4MB PSRAM budget

**LVGL objects:**
- Current: 1 sprite image + background objects
- Max: 4 sprite images + HUD overlay container + counter label + badge label
- Additional ~10 LVGL objects — negligible

**RLE sprite data (flash):**
- Current: ~11 animations × ~20 frames × ~800 bytes = ~176KB
- No change — same sprites, just scaled at runtime
- Mini-crab sprite: ~8 frames × ~200 bytes = ~1.6KB additional

### Protocol Changes

#### New `set_sessions` Action

A single new action replaces `set_status` for the multi-session case. The daemon sends the full session state in one payload:

```json
{
  "action": "set_sessions",
  "anims": ["typing", "thinking"],
  "subagents": 3
}
```

- `anims`: Ordered list of animation names, one per visible session. Ordered by priority (highest first). Max 4 entries — daemon truncates if more sessions exist.
- `subagents`: Total active subagent count across all sessions.

The firmware derives session count from `len(anims)`, overflow count from comparing against the daemon's separate session count (included as optional field when > 4):

```json
{
  "action": "set_sessions",
  "anims": ["typing", "thinking", "building", "idle"],
  "subagents": 5,
  "overflow": 2
}
```

The `overflow` field is only present when there are more sessions than the 4 visible.

**Backwards compatibility:** The old `set_status` action continues to work for firmware that hasn't been updated. The daemon detects the firmware version (or protocol capability) and falls back to `set_status` with the legacy single-animation format if needed. The simplest approach: try `set_sessions` first; if the firmware doesn't understand it, fall back to `set_status`.

**Existing special cases preserved:**
- `sweeping` (PreCompact) is still sent as a `set_status` oneshot before the regular state update
- `sleeping`, `disconnected` remain as `set_status` since they're whole-device states, not per-session

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
2. Computes Clawd positions and scale based on session count
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
    clawd_anim_id_t cur_anim;
    clawd_anim_id_t fallback_anim;
    int frame_idx;
    bool active;
} clawd_slot_t;
```

The `scene_t` struct gains `clawd_slot_t slots[MAX_VISIBLE_SESSIONS]` and the existing single-sprite functions (`scene_tick`, `decode_and_apply_frame`, etc.) are refactored to iterate over active slots.

Frame buffers are allocated from PSRAM on demand (when a slot becomes active) and freed when no longer needed, keeping the steady-state memory usage at 1 buffer for the common single-session case.

#### HUD Overlay

A new LVGL layer on top of the scene for:
- Mini-crab icon (small RLE-compressed sprite, same pipeline as Clawd sprites)
- Pixel-art counter text (rendered via LVGL canvas `lv_canvas_draw_rect()` calls, or a pre-rendered digit atlas)
- Session overflow badge (same pixel-art text rendering, blue color)

#### Transition Animations

When session count changes:
- **Clawd appears:** Fades in over 400ms (matching existing `SCENE_ANIM_MS`)
- **Clawd disappears:** Fades out over 400ms
- **Remaining Clawds reposition:** Slide to new positions over 400ms with ease-out (using LVGL's `lv_anim_t`)
- **HUD counter appears/disappears:** Instant show/hide (no animation — it's a small HUD element)

### Daemon Changes

#### Display State Computation

`_compute_display_state()` returns a dict instead of a string:

```python
def _compute_display_state(self) -> dict:
    if not self._session_states:
        return {"status": "sleeping"}

    # Collect per-session animations, ordered by priority
    session_anims = []
    for sid, s in self._session_states.items():
        has_subs = bool(s.get("subagents"))
        if s["state"] == "working":
            session_anims.append(("building" if has_subs else "typing", 4))
        elif s["state"] == "thinking":
            session_anims.append(("thinking", 3))
        elif s["state"] == "confused":
            session_anims.append(("confused", 2))
        else:
            session_anims.append(("idle", 1))

    # Sort by priority (highest first), take top 4
    session_anims.sort(key=lambda x: -x[1])
    anims = [a[0] for a in session_anims[:MAX_VISIBLE]]

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

Change detection in `_broadcast_display_state_if_changed()`: compare the new dict against `self._last_display_state` using `==`. The `anims` list is always sorted by priority (highest first), so ordering is stable for the same set of sessions. `_last_display_state` changes from `str` to `dict`.

### Scene Width Transitions

When transitioning between full screen (320px) and narrow (107px, notifications present):
- **Full → Narrow:** Remove extra Clawds (fade out), keep highest-priority one, add session badge if sessions > 1
- **Narrow → Full:** Spawn additional Clawds at their positions (fade in), remove session badge

### Edge Cases

- **Session ends while Clawd is visible:** Fade out that Clawd, slide remaining ones to new positions (400ms)
- **Subagent count changes rapidly:** No debounce needed — the HUD counter is just a number redraw, cheap
- **All subagents stop:** Hide the HUD counter instantly
- **0 sessions (sleeping):** No Clawds, no HUD, sleeping animation as today
- **Disconnected:** Single disconnected Clawd as today, no HUD
- **Old firmware:** Daemon falls back to `set_status` with single animation string

### New Sprites & Assets

1. **Mini-crab sprite** (~16×16 at original scale)
   - `mini-crab-typing` — bouncing body, waving arms (for HUD icon)
   - Same salmon-orange (#DE886D) as Clawd, 4 small legs, tiny arms, small black eyes
   - Must go through sprite pipeline: `svg2frames.py` → `png2rgb565.py` → RLE header

2. **Pixel-art bitmap font** — digits 0-9, × symbol, + symbol
   - 5×5 pixel grid per character
   - Rendered as filled rectangles at configurable pixel size
   - Yellow (#FFC107) for subagent counter, blue (#8BC6FC) for session badges
   - Implemented as a bitmap lookup table in C (same approach as the digit bitmasks in the layout editor)

## Tools Created

- `tools/scene-layout-editor.html` — Interactive layout editor for positioning sprites on the 320×172 display. Supports dragging, scale adjustment, animation selection, HUD counter with adjustable count, import/export of layouts, and presets. Reusable for future scene layout work.
- `assets/svg-animations/mini-crab-typing.svg` — Placeholder mini-crab animation SVG (needs sprite pipeline conversion for firmware)
- `assets/svg-animations/clawd-working-beacon.svg` — Signal beacon animation (saved for future use, not part of this feature)
