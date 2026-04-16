# Daemon Resilience Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the daemon and menu bar app so that orphaned processes, stale socket files, silent daemon crashes, and outdated hooks are detected and recovered automatically.

**Architecture:** Four independent hardening tasks: (1) auto-update hooks on startup when outdated; (2) wrap daemon thread with exception logging, health monitoring, and periodic health check timer; (3) kill orphaned sim processes on startup with process name verification; (4) sync `_last_display_state` after transport replay. Each task is independently testable and deployable. Stale socket cleanup already exists in `socket_server.py:26-27` and needs no changes.

**Tech Stack:** Python (host daemon + menu bar app). Pytest for tests.

**Known limitation (not addressed):** If the daemon is killed during an active session (e.g., app rebuild), the `Stop` hook fires but the daemon isn't running to receive it. The persisted session stays "working" until staleness eviction (10 min). A proper fix would require heartbeat probing or startup re-scan — tracked for future work.

---

## File Structure

| File | Changes |
|------|---------|
| `host/clawd_tank_menubar/app.py` (modify) | Auto-update hooks, daemon thread exception logging, health check timer |
| `host/clawd_tank_daemon/sim_process.py` (modify) | Kill orphaned sim process with name verification |
| `host/clawd_tank_daemon/daemon.py` (modify) | Sync `_last_display_state` after replay |

---

## Chunk 1: Hardening

### Task 1: Auto-update hooks when outdated

**Files:**
- Modify: `host/clawd_tank_menubar/app.py:487-493` (main function, after `install_notify_script`)

Currently `install_notify_script()` runs on every launch (writes the script), but `install_hooks()` only runs when the user clicks "Install Claude Code Hooks". This means adding new hook types (like `SubagentStart`) requires a manual reinstall.

The fix: call `install_hooks()` automatically on startup if `are_hooks_installed()` returns `False`.

**Note:** `install_hooks()` uses `settings["hooks"].update(HOOKS_CONFIG)` which replaces the entire entry for each hook event name. This is a pre-existing behavior, but auto-updating makes it more impactful. We log when auto-updating so the user can see what happened.

- [ ] **Step 1: Implement auto-update in main()**

After `hooks.install_notify_script()` in `main()`, add:

```python
    if not hooks.are_hooks_installed():
        logger.info("Hooks outdated, auto-updating...")
        hooks.install_hooks()
```

Note: `are_hooks_installed()` already checks that every key in `HOOKS_CONFIG` is present and points to our script. If any are missing (new hooks added), it returns `False`.

- [ ] **Step 2: Verify hooks menu item state**

In `__init__`, the hooks menu item state is set once:
```python
self._hooks_item.state = hooks.are_hooks_installed()
```

This will now always be `True` after auto-install. No change needed — `main()` runs before `ClawdTankApp()` is constructed.

- [ ] **Step 3: Test manually**

Remove `SubagentStart` from `~/.claude/settings.json` hooks, restart the app, verify the log shows "Hooks outdated, auto-updating..." and the hook is restored.

- [ ] **Step 4: Commit**

```bash
git add host/clawd_tank_menubar/app.py
git commit -m "feat(menubar): auto-update hooks on startup when outdated"
```

### Task 2: Daemon thread exception logging and health monitoring

**Files:**
- Modify: `host/clawd_tank_menubar/app.py:161-173` (`_start_daemon_thread`)

The daemon thread runs `asyncio.run()` inside a bare `threading.Thread`. If the event loop crashes, the thread dies silently — the menu bar app continues running but is completely deaf to hooks. Additionally, the death is never discovered because `_update_menu_state` is only triggered by events that require the daemon to be alive (catch-22).

- [ ] **Step 1: Wrap run_loop with exception logging**

Change `_start_daemon_thread` from:

```python
    def _start_daemon_thread(self):
        """Start the daemon's asyncio event loop in a background thread."""
        self._daemon = ClawdDaemon(observer=self, headless=False)

        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop_ready.set()
            self._loop.run_until_complete(self._daemon.run())

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        self._loop_ready.wait(timeout=5)
```

to:

```python
    def _start_daemon_thread(self):
        """Start the daemon's asyncio event loop in a background thread."""
        self._daemon = ClawdDaemon(observer=self, headless=False)

        def run_loop():
            try:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop_ready.set()
                self._loop.run_until_complete(self._daemon.run())
            except Exception:
                logger.exception("Daemon thread crashed")
                self._loop_ready.set()  # unblock main thread if still waiting

        self._daemon_thread = threading.Thread(target=run_loop, daemon=True)
        self._daemon_thread.start()
        self._loop_ready.wait(timeout=5)
```

- [ ] **Step 2: Add daemon health check property**

Add to `ClawdTankApp`:

```python
    @property
    def _daemon_alive(self) -> bool:
        return (
            hasattr(self, '_daemon_thread')
            and self._daemon_thread.is_alive()
        )
```

- [ ] **Step 3: Show warning icon when daemon is dead**

In `_update_menu_state`, add a check at the beginning:

```python
        if not self._daemon_alive:
            self.icon = self._icon_path("crab-disconnected")
            return
```

- [ ] **Step 4: Add periodic health check timer**

The `_update_menu_state` method is only called on connection/notification change events — events that require the daemon to be alive. To break this catch-22, add a periodic timer that triggers the menu state update independently.

Add to `ClawdTankApp` (as a `rumps.timer` decorated method):

```python
    @rumps.timer(30)
    def _health_check(self, _):
        """Periodic check to detect daemon thread death."""
        if not self._daemon_alive:
            self._update_menu_state()
```

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_menubar/app.py
git commit -m "fix(menubar): log daemon thread exceptions, add health check timer"
```

### Task 3: Kill orphaned simulator process on startup

**Files:**
- Modify: `host/clawd_tank_daemon/sim_process.py:65-82` (`start` method)

When the sim port is already in use, the daemon currently connects to the existing process. If that's an orphan from a crashed quit, its window is hidden and the user can't interact with it.

The fix: when the port is in use, identify the process by name, kill it, wait for the port to be released, then spawn fresh.

**Behavioral change:** Developers who manually start a standalone simulator and want the daemon to connect to it will need to use a different port. The daemon now always spawns its own sim process.

- [ ] **Step 1: Add _kill_orphaned_sim method**

Add `import subprocess` to the top-level imports in `sim_process.py`.

Add to `SimProcessManager`:

```python
    async def _kill_orphaned_sim(self) -> None:
        """Kill any orphaned clawd-tank-sim processes listening on our port."""
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{self._port}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return
            for pid_str in result.stdout.strip().split('\n'):
                if not pid_str.strip():
                    continue
                pid = int(pid_str)
                # Verify it's actually a clawd-tank-sim process
                ps_result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "comm="],
                    capture_output=True, text=True, timeout=5,
                )
                if "clawd-tank-sim" not in ps_result.stdout:
                    logger.warning("PID %d on port %d is not clawd-tank-sim, skipping", pid, self._port)
                    continue
                logger.info("Killing orphaned clawd-tank-sim (PID %d) on port %d", pid, self._port)
                os.kill(pid, signal.SIGKILL)
        except (subprocess.TimeoutExpired, ValueError, ProcessLookupError, PermissionError):
            pass
```

- [ ] **Step 2: Update start() to kill orphans and always spawn fresh**

Replace the entire `start` method:

```python
    async def start(self) -> Optional[SimClient]:
        if await self._is_port_in_use():
            logger.warning("Port %d already in use, killing orphaned process", self._port)
            await self._kill_orphaned_sim()
            # Wait for port to be released, with timeout
            for _ in range(10):
                await asyncio.sleep(0.5)
                if not await self._is_port_in_use():
                    break
            else:
                logger.error("Port %d still in use after killing orphan", self._port)
                return None

        binary = self._find_binary()
        if not binary:
            logger.error("clawd-tank-sim binary not found")
            return None
        logger.info("Starting simulator: %s --listen %d --hidden", binary, self._port)
        self._process = await asyncio.create_subprocess_exec(
            binary, "--listen", str(self._port), "--hidden",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._log_stream(self._process.stdout, logging.INFO))
        asyncio.create_task(self._log_stream(self._process.stderr, logging.WARNING))
        await asyncio.sleep(0.3)
        self._client = SimClient(port=self._port, on_event_cb=self._handle_sim_event)
        return self._client
```

- [ ] **Step 3: Run tests**

Run: `cd host && .venv/bin/pytest tests/test_sim_process.py -v`
Expected: ALL PASS

- [ ] **Step 4: Run full test suite**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_daemon/sim_process.py
git commit -m "fix(sim): kill orphaned simulator process instead of connecting to it"
```

### Task 4: Sync display state after transport replay

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:298-303` (`_replay_active_for`)

`_replay_active_for` sends the current display state on transport connect but doesn't update `_last_display_state`. This means the next `_broadcast_display_state_if_changed` call would redundantly broadcast the same state.

- [ ] **Step 1: Update _replay_active_for**

Change:

```python
        # Send current display state
        state = self._compute_display_state()
        status_payload = json.dumps({"action": "set_status", "status": state})
        await transport.write_notification(status_payload)
```

to:

```python
        # Send current display state
        state = self._compute_display_state()
        self._last_display_state = state
        status_payload = json.dumps({"action": "set_status", "status": state})
        await transport.write_notification(status_payload)
```

- [ ] **Step 2: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py
git commit -m "fix(daemon): track last_display_state after replay to avoid duplicate broadcasts"
```

### Task 5: Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `TODO.md`

- [ ] **Step 1: Update CLAUDE.md**

In the Host section, update the `clawd_tank_menubar` description to mention auto-hook-update and daemon health monitoring.

- [ ] **Step 2: Update TODO.md**

Add a new completed section:

```markdown
## Daemon Resilience (v1.2.1) — Complete

- [x] **Auto-update hooks on startup** — Hooks are checked and updated automatically on app launch when outdated, removing the need for manual "Install Hooks" clicks after code updates.
- [x] **Daemon thread crash logging** — Daemon thread exceptions are caught and logged instead of dying silently. Periodic health check timer detects dead daemon and shows disconnected icon.
- [x] **Orphaned sim process cleanup** — On startup, orphaned simulator processes on the listen port are identified by name and killed instead of being connected to.
- [x] **Display state sync on replay** — `_last_display_state` is updated after transport replay to prevent duplicate broadcasts.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md TODO.md
git commit -m "docs: document daemon resilience improvements"
```
