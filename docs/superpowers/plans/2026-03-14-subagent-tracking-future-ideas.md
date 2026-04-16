# Subagent Tracking — Future Ideas

Ideas that build on top of the core subagent tracking implementation. Each is independent and can be implemented in any order after the base plan is complete.

---

## 1. "Supervising" Animation

A distinct Clawd animation for when the parent session is idle but subagents are working. Instead of reusing the Typing/Juggling/Building animations, Clawd would have a unique "supervising" pose — e.g., watching a control panel, overseeing smaller crabs, or sitting back with binoculars.

**What it would take:**
- New SVG animation (180x180px, ~12-48 frames)
- `png2rgb565.py` pipeline to generate C header
- New `CLAWD_ANIM_SUPERVISING` enum value in firmware
- New `delegating` display state in daemon (distinct from `working_N`)
- Firmware `ui_manager.c` mapping: `delegating` → `CLAWD_ANIM_SUPERVISING`

**Why it's interesting:** Gives users a clear visual distinction between "Claude is directly working" vs "Claude dispatched agents and is waiting." Currently both look like Typing.

---

## 2. Subagent Count as Intensity Tier

Use the number of active subagents to drive the working intensity animation, independent of or combined with the session count.

**Options:**
- 1 subagent = Typing, 2 = Juggling, 3+ = Building (replaces session count)
- Combine: `working_count = sessions_working + sessions_with_subagents`, capped at 3
- Per-session: use max(1, subagent_count) as that session's contribution to intensity

**What it would take:** Only changes to `_compute_display_state()` in `daemon.py`.

---

## 3. Agent Type in Menu Bar Status

Show which types of subagents are running in the macOS menu bar status line. E.g., "Working (2 agents: Explore, Plan)" instead of just "Working".

**What it would take:**
- Forward `agent_type` in daemon messages (already available in hook payload)
- Store `agent_type` alongside `agent_id` in the subagents tracking
- Add observer method `on_subagent_change(session_id, active_agents: dict[str, str])`
- Menu bar renders agent types in status submenu

---

## 4. Per-Subagent Notification Cards

Show each active subagent as a notification card on the device display. Cards would show the agent type and could auto-dismiss when the subagent stops.

**What it would take:**
- Generate `add` BLE payloads on `subagent_start` (currently returns `None`)
- Generate `dismiss` BLE payloads on `subagent_stop`
- Use `agent_id` as the notification ID
- Use `agent_type` as the project name, "Working..." as message
- Cards auto-dismiss when subagent completes

**Considerations:** Could flood the 8-notification limit if many subagents spawn. May want to only show for long-running agents (filter by agent_type, or delay card creation by N seconds).

---

## 5. Subagent Duration Tracking

Track how long each subagent has been running. Use this for:
- Menu bar: show elapsed time per agent
- Device: show duration on notification cards
- Logging: report subagent durations for performance analysis

**What it would take:**
- Store spawn timestamp alongside `agent_id`
- Compute duration on `subagent_stop`
- Log or display as needed

---

## 6. Team Awareness

Claude Code has agent teams (`TeammateIdle`, `TaskCompleted` hooks). Track team membership and show team-level status:
- "Team working: 3/5 agents active"
- Different animation for team orchestration vs single subagent

**What it would take:** Register `TeammateIdle` and `TaskCompleted` hooks, model team state in daemon. More complex — would need to understand the team topology.
