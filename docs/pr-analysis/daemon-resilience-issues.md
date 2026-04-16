# Daemon Resilience Issues — Session Report

Date: 2026-03-14

## Issues Observed

### 1. Orphaned simulator process prevents fresh start

**Symptom:** On app launch, log shows `Port 19872 already in use, connecting to existing simulator`. The daemon connects to a stale sim process from a previous run instead of spawning a new one. The stale sim was started with `--hidden` so the window never shows.

**Root cause:** The quit handler sometimes fails to kill the sim process (timeout, crash, force-quit). The next launch finds the port occupied and connects to the orphan instead of spawning fresh.

**Impact:** Simulator window doesn't appear. User has to manually `pkill -f clawd-tank-sim`.

---

### 2. Socket file left behind after unclean shutdown

**Symptom:** The Unix socket file `~/.clawd-tank/sock` exists but nothing is accepting connections. Notify script gets `ConnectionRefusedError` and exits silently. All hooks are lost.

**Root cause:** The daemon creates the socket file on startup but if the process is killed (SIGKILL, force-quit, crash), the file isn't cleaned up. The next daemon instance may see the stale file or fail to bind.

**Impact:** Complete loss of hook events — Clawd shows sleeping/stale state even though Claude Code sessions are active.

---

### 3. No `Stop` event after subagent tool calls

**Symptom:** Session stays in `working` state indefinitely after subagents finish. Logs show continuous `tool_use` events from subagent tool calls, then nothing — no `Stop` event to transition to `idle`.

**Timeline from logs:**
```
22:25:43 — last tool_use event
(gap — no events)
22:35:49 — staleness eviction kicks in, removes sessions
```

**Root cause:** When the app is rebuilt and reinstalled during an active session, the daemon dies. The `Stop` hook fires but the daemon isn't running to receive it. On restart, the persisted state still says `working` and no correcting event arrives until the user interacts again.

**Impact:** Clawd stuck on working animation until staleness eviction (10 min), then goes to sleep instead of idle.

---

### 4. Hooks not installed after code changes

**Symptom:** New hooks (`SubagentStart`, `SubagentStop`) added to `HOOKS_CONFIG` but not present in `~/.claude/settings.json`. Zero `subagent_start`/`subagent_stop` events in logs.

**Root cause:** `install_notify_script()` runs on every app launch (writes the script), but `install_hooks()` only runs when the user clicks "Install Claude Code Hooks" in the menu. Adding new hook types requires the user to manually reinstall.

**Impact:** Subagent tracking doesn't work until user explicitly reinstalls hooks. No indication in the UI that hooks are outdated.

---

### 5. Daemon thread crashes silently

**Symptom:** App is running (menu bar icon visible), sim is running, but socket doesn't exist and no events are processed. No error in logs — just the hook install messages and then silence.

**Root cause:** The daemon runs in a background thread via `asyncio.run()`. If the event loop crashes or fails to start, the menu bar app continues running normally with no indication. The log shows hooks installed but never "Listening on" for the socket.

**Impact:** App appears functional but is completely deaf to hook events. User has no way to know the daemon died.

---

## Hardening Recommendations

### A. Stale socket cleanup on startup
Before binding the socket, check if the file exists and remove it. The lock file mechanism already prevents two daemons, so a stale socket is always safe to remove.

### B. Stale sim process detection
On startup, if the sim port is in use, check if the process is actually a clawd-tank-sim we own. If it's an orphan from a previous run, kill it and start fresh.

### C. Auto-reinstall hooks when outdated
Compare installed hooks against `HOOKS_CONFIG` on every startup. If they differ, auto-update `settings.json` instead of waiting for manual "Install Hooks" click.

### D. Daemon health monitoring
The menu bar app should monitor the daemon thread. If it dies, show a warning indicator and attempt restart. Could be as simple as checking if the socket file exists periodically.

### E. Graceful session state on daemon restart
When the daemon starts and loads persisted sessions, immediately broadcast the computed display state to connected transports. Currently it only broadcasts on state *changes*, so loaded state may not be sent until the first hook event arrives.

### F. Log daemon thread exceptions
Wrap the daemon thread's `asyncio.run()` in a try/except that logs any uncaught exception to the log file before the thread dies.
