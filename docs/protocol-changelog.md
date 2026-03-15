# Clawd Tank Protocol Changelog

Protocol version is exposed by the firmware via a read-only BLE GATT characteristic. The daemon reads it on connect to determine which actions are supported. Absence of the version characteristic implies v1.

## Version 2 (planned)

**New GATT characteristic:** Protocol version (read-only, returns `"2"`). UUID: TBD (generated during implementation).

**New action: `set_sessions`**
```json
{
  "action": "set_sessions",
  "anims": ["typing", "thinking"],
  "subagents": 3,
  "overflow": 2
}
```
- `anims` — ordered list of per-session animation names (max 4, highest priority first). Valid values: `idle`, `typing`, `thinking`, `building`, `confused`.
- `subagents` — total active subagent count across all sessions.
- `overflow` (optional) — number of additional sessions beyond the 4 visible. Only present when > 0.

Enables multi-session display (multiple Clawds) and subagent HUD counter. Receiving `set_sessions` implicitly clears sleeping/disconnected state.

Note: `sweeping` is NOT a valid `anims` value — it's still sent as a `set_status` oneshot (applies to all visible Clawds). `juggling` is retired as a v1 intensity tier — replaced by showing individual Clawds per session.

**Existing actions unchanged:** `add`, `dismiss`, `clear`, `set_time`, `set_status` all continue to work as in v1.

## Version 1 (current)

The original protocol. No version characteristic — the daemon infers v1 from its absence.

**GATT Service:** `AECBEFD9-98A2-4773-9FED-BB2166DAA49A`

**Characteristics:**
- **Notification** (`71FFB137-8B7A-47C9-9A7A-4B1B16662D9A`) — write-only. Accepts JSON payloads.
- **Config** (`E9F6E626-5FCA-4201-B80C-4D2B51C40F51`) — read/write. JSON config (brightness, etc.)

**Actions (JSON written to notification characteristic):**

| Action | Payload | Description |
|--------|---------|-------------|
| `add` | `{"action":"add","id":"...","project":"...","message":"..."}` | Add/update a notification card |
| `dismiss` | `{"action":"dismiss","id":"..."}` | Dismiss a notification by ID |
| `clear` | `{"action":"clear"}` | Clear all notifications |
| `set_time` | `{"action":"set_time","epoch":1234567890,"tz":"UTC-3"}` | Sync host time + POSIX timezone |
| `set_status` | `{"action":"set_status","status":"working_1"}` | Set display animation state |

**`set_status` values:**
- `sleeping` — no active sessions
- `idle` — sessions exist but none are working
- `thinking` — waiting for user input
- `working_1` — 1 session working → Typing animation
- `working_2` — 2 sessions working → Juggling animation
- `working_3` — 3+ sessions working → Building animation
- `confused` — idle prompt notification received
- `sweeping` — context compaction in progress (oneshot)

**TCP Simulator:** Same JSON protocol over TCP (port 19872 default). Additional window commands (`show_window`, `hide_window`, `set_window`) and outbound events (`window_hidden`) are transport-level, not part of the notification protocol.
