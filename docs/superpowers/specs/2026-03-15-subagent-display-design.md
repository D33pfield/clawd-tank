# Subagent & Multi-Session Display Design

## Problem

The daemon tracks per-session subagent IDs, but the display has no visual distinction between a session working alone and one with multiple subagents. The intensity tiers (Typing/Juggling/Building) only count sessions, not what's happening within each session. Additionally, multiple simultaneous sessions have no clear visual representation beyond the intensity tier mapping.

## Design

### Core Concepts

- **Full Clawds represent sessions.** Each active session gets its own full-size Clawd sprite on the display with the appropriate state animation (typing, thinking, idle, etc.).
- **A HUD counter represents subagents.** A mini-crab icon + pixel-art "×N" counter in the top-left corner shows the total number of active subagents across all sessions. Only visible when count > 0.
- **Badges handle overflow.** When there are more sessions than fit on screen, a "+N" badge in the top-right shows the overflow count.

### Full Screen Layout (320×172, no notifications)

#### Session Display

| Active Sessions | Layout | Clawd Scale |
|----------------|--------|-------------|
| 1 | 1 Clawd, centered | 4.27 (64px) |
| 2 | 2 Clawds, side by side | 4.27 (64px) |
| 3 | 3 Clawds, spread evenly | 3.5 (52px) |
| 4 | 4 Clawds, spread evenly | ~3.0 (45px) |
| 5+ | 4 Clawds visible + blue "+N" badge top-right | ~3.0 (45px) |

Each Clawd displays the animation matching its session state (typing, thinking, confused, idle, etc.) independently.

#### Subagent HUD Counter

- **Position:** Top-left corner of the scene (x=4, y=4 in display coordinates)
- **Components:** Animated mini-crab sprite icon (scale ~2.0) + pixel-art "×N" text in yellow (#FFC107)
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
| Clawd | 1 Clawd at full scale (4.27). Displays the highest-priority session animation. |
| Subagent HUD | Mini-crab icon + yellow "×N" in top-left corner (same as full screen) |
| Session badge | Blue "×N" badge in top-right of scene area, showing **total** session count. Only shown when sessions > 1. |

Priority order for which session's animation to display: working > thinking > confused > idle (same as existing `_compute_display_state()` logic).

### New Sprites & Assets Needed

1. **Mini-crab sprite** (~16×16 at original scale)
   - `mini-crab-typing` — bouncing body, waving arms (for HUD icon)
   - `mini-crab-idle` — subtle bob (optional, for variety)
   - The sprite uses the same salmon-orange (#DE886D) color as Clawd, with 4 small legs, tiny arms, and small black eyes

2. **Pixel-art bitmap font** — digits 0-9 plus × symbol
   - 5×5 pixel grid per character
   - Rendered as filled rectangles at configurable pixel size
   - Yellow (#FFC107) for subagent counter, blue (#8BC6FC) for session badges

3. **Session overflow badge** — "+N" indicator for when > 4 sessions exist

### Protocol Changes

#### New `set_status` Values

The daemon needs to communicate both session count and subagent count to the firmware. Current `set_status` payload:

```json
{"action": "set_status", "status": "working_1"}
```

Extended payload options (to be finalized in implementation plan):

**Option A — Extend set_status with counts:**
```json
{"action": "set_status", "status": "working_1", "subagents": 3, "sessions": 2}
```

**Option B — Separate action for subagent count:**
```json
{"action": "set_subagents", "count": 3}
```

The implementation plan should determine which approach best fits the existing architecture.

#### Per-Session Animation

Currently the display shows one animation based on the computed display state. With multiple Clawds, the firmware needs to know each session's individual state. Options:

**Option A — Send per-session states:**
```json
{"action": "set_sessions", "sessions": [
  {"state": "working", "anim": "typing"},
  {"state": "thinking", "anim": "thinking"}
]}
```

**Option B — Send ordered list of animations:**
```json
{"action": "set_status", "anims": ["typing", "thinking"], "subagents": 3}
```

### Firmware Scene Changes

#### Scene Layout Manager

The scene needs a layout manager that:
1. Accepts a list of session animations + subagent count
2. Computes Clawd positions based on session count (scale, x-position)
3. Renders 1-4 Clawd sprites with independent animations
4. Renders the HUD counter overlay (mini-crab icon + pixel-art text)
5. Renders the overflow badge when needed
6. Positions the clock centered when in full screen

#### Multi-Sprite Rendering

Currently the scene has a single `sprite_img` LVGL object. This needs to expand to an array of up to 4 sprite images, each with independent animation state, frame index, and position.

#### HUD Overlay

A new LVGL layer on top of the scene for:
- Mini-crab icon (small sprite or canvas-drawn)
- Pixel-art counter text (rendered as LVGL canvas or custom draw callback)
- Session overflow badge

### Daemon Changes

#### Display State Computation

`_compute_display_state()` currently returns a single string like `"working_2"`. It needs to return richer data:

```python
{
    "session_anims": ["typing", "thinking"],  # ordered list of per-session animations
    "subagent_count": 5,                       # total across all sessions
}
```

#### Session Animation Mapping

Each session's state maps to an animation:
- `working` → typing (or building if session has active subagents? TBD)
- `thinking` → thinking
- `confused` → confused
- `idle` → idle
- `registered` → idle

### Scene Width Transitions

When transitioning between full screen (320px) and narrow (107px, notifications present):
- Full → Narrow: Remove extra Clawds, keep highest-priority one, add session badge
- Narrow → Full: Spawn additional Clawds at their positions, remove session badge

### Edge Cases

- **Session ends while Clawd is visible:** Remove that Clawd, reposition remaining ones (animate the transition)
- **Subagent count changes rapidly:** Debounce the HUD counter update to avoid flickering
- **All subagents stop:** Hide the HUD counter smoothly (fade or instant)
- **0 sessions (sleeping):** No Clawds, no HUD, sleeping animation as today
- **Disconnected:** Single disconnected Clawd as today, no HUD

## Tools Created

- `tools/scene-layout-editor.html` — Interactive layout editor for positioning sprites on the 320×172 display. Supports dragging, scale adjustment, animation selection, HUD counter preview, import/export of layouts, and presets. Reusable for future scene layout work.
- `assets/svg-animations/mini-crab-typing.svg` — Placeholder mini-crab animation SVG
- `assets/svg-animations/clawd-working-beacon.svg` — Signal beacon animation (saved for future use)
