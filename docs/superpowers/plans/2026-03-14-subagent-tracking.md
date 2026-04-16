# Subagent Tracking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Clawd from sleeping while Claude Code subagents are actively working by tracking subagent lifecycle via `SubagentStart`/`SubagentStop` hooks.

**Architecture:** Register two new Claude Code hooks (`SubagentStart`, `SubagentStop`) that forward `agent_id` to the daemon. The daemon tracks active subagents per session and uses this to (a) suppress staleness eviction and (b) count sessions with active subagents as "working" in the display state computation. No firmware changes needed.

**Tech Stack:** Python (host daemon + hook handler). Pytest for tests.

---

## Chunk 1: Protocol & Hook Handler

### Task 1: Protocol — SubagentStart/SubagentStop conversion

**Files:**
- Modify: `host/clawd_tank_daemon/protocol.py:8-77` (add two new event handlers)
- Test: `host/tests/test_protocol.py`

- [ ] **Step 1: Write failing tests for SubagentStart conversion**

```python
def test_subagent_start_produces_subagent_start_event():
    hook = {
        "hook_event_name": "SubagentStart",
        "session_id": "sess-1",
        "agent_id": "agent-abc123",
        "agent_type": "Explore",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "subagent_start"
    assert msg["session_id"] == "sess-1"
    assert msg["agent_id"] == "agent-abc123"


def test_subagent_stop_produces_subagent_stop_event():
    hook = {
        "hook_event_name": "SubagentStop",
        "session_id": "sess-1",
        "agent_id": "agent-abc123",
        "agent_type": "Explore",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "subagent_stop"
    assert msg["session_id"] == "sess-1"
    assert msg["agent_id"] == "agent-abc123"


def test_subagent_events_produce_no_ble_payload():
    assert daemon_message_to_ble_payload({"event": "subagent_start", "session_id": "s", "agent_id": "a"}) is None
    assert daemon_message_to_ble_payload({"event": "subagent_stop", "session_id": "s", "agent_id": "a"}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v -k "subagent"`
Expected: FAIL — `SubagentStart`/`SubagentStop` not handled, returns `None` / raises `ValueError`

- [ ] **Step 3: Implement SubagentStart/SubagentStop in protocol.py**

In `hook_payload_to_daemon_message`, add before the final `return None`:
```python
    if event_name == "SubagentStart":
        return {
            "event": "subagent_start",
            "session_id": session_id,
            "agent_id": hook.get("agent_id", ""),
        }

    if event_name == "SubagentStop":
        return {
            "event": "subagent_stop",
            "session_id": session_id,
            "agent_id": hook.get("agent_id", ""),
        }
```

In `daemon_message_to_ble_payload`, update the early-return set:
```python
    if event in ("session_start", "tool_use", "compact", "subagent_start", "subagent_stop"):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_daemon/protocol.py host/tests/test_protocol.py
git commit -m "feat(protocol): handle SubagentStart/SubagentStop hook events"
```

### Task 2: Hook handler script — forward SubagentStart/SubagentStop

**Files:**
- Modify: `host/clawd_tank_menubar/hooks.py:18-107` (inline script's `hook_to_message` function)
- Modify: `host/clawd_tank_menubar/hooks.py:111-136` (HOOKS_CONFIG dict)

- [ ] **Step 1: Add SubagentStart/SubagentStop to the inline hook script**

In the `hook_to_message` function inside `NOTIFY_SCRIPT` (after the `SessionEnd` handler, before `return None`), add:

```python
        if event_name == "SubagentStart":
            return {
                "event": "subagent_start",
                "session_id": session_id,
                "agent_id": hook.get("agent_id", ""),
            }

        if event_name == "SubagentStop":
            return {
                "event": "subagent_stop",
                "session_id": session_id,
                "agent_id": hook.get("agent_id", ""),
            }
```

- [ ] **Step 2: Add SubagentStart/SubagentStop to HOOKS_CONFIG**

```python
    "SubagentStart": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
    "SubagentStop": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
```

- [ ] **Step 3: Verify the inline script and protocol.py stay in sync**

Visually confirm that `hook_to_message()` in the inline script handles the same events as `hook_payload_to_daemon_message()` in `protocol.py`. Both should now handle: `SessionStart`, `PreToolUse`, `PreCompact`, `Stop`, `Notification`, `UserPromptSubmit`, `SessionEnd`, `SubagentStart`, `SubagentStop`.

- [ ] **Step 4: Run full test suite to verify nothing broke**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_menubar/hooks.py
git commit -m "feat(hooks): register SubagentStart/SubagentStop hook events"
```

## Chunk 2: Daemon Subagent Tracking

### Task 3: Daemon — track active subagents per session

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:125-219` (session state tracking)
- Test: `host/tests/test_session_state.py`

The session state dict currently stores `{"state": str, "last_event": float}`. We add an optional `"subagents"` key holding a `set` of active `agent_id` strings.

- [ ] **Step 1: Write failing tests for subagent_start/subagent_stop events**

Add to `test_session_state.py`:

```python
# --- Subagent tracking ---

@pytest.mark.asyncio
async def test_subagent_start_tracks_agent_id():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert "a1" in d._session_states["s1"]["subagents"]

@pytest.mark.asyncio
async def test_subagent_stop_removes_agent_id():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time(), "subagents": {"a1"}}
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert "a1" not in d._session_states["s1"].get("subagents", set())

@pytest.mark.asyncio
async def test_subagent_start_creates_session_if_missing():
    d = make_daemon()
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert "s1" in d._session_states
    assert "a1" in d._session_states["s1"]["subagents"]

@pytest.mark.asyncio
async def test_subagent_start_refreshes_last_event():
    d = make_daemon()
    old_time = time.time() - 500
    d._session_states["s1"] = {"state": "working", "last_event": old_time}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["last_event"] > old_time

@pytest.mark.asyncio
async def test_subagent_stop_refreshes_last_event():
    d = make_daemon()
    old_time = time.time() - 500
    d._session_states["s1"] = {"state": "working", "last_event": old_time, "subagents": {"a1"}}
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["last_event"] > old_time

@pytest.mark.asyncio
async def test_subagent_stop_for_unknown_agent_is_noop():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    # Should not crash
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "unknown"})
    assert d._session_states["s1"]["state"] == "working"

@pytest.mark.asyncio
async def test_multiple_subagents_tracked():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a2"})
    assert d._session_states["s1"]["subagents"] == {"a1", "a2"}
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["subagents"] == {"a2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "subagent"`
Expected: FAIL — `subagent_start`/`subagent_stop` events not handled

- [ ] **Step 3: Implement subagent tracking in _update_session_state**

In `daemon.py`, add to `_update_session_state` (after the existing `elif event == "dismiss":` block):

```python
        elif event == "subagent_start":
            self._session_states.setdefault(session_id, {"state": "working", "last_event": now})
            self._session_states[session_id].setdefault("subagents", set())
            self._session_states[session_id]["subagents"].add(agent_id)
            self._session_states[session_id]["last_event"] = now
        elif event == "subagent_stop":
            if session_id in self._session_states:
                self._session_states[session_id].get("subagents", set()).discard(agent_id)
                self._session_states[session_id]["last_event"] = now
```

Note: `_update_session_state` needs the `agent_id` parameter. Update its signature to:
```python
def _update_session_state(self, event: str, hook: str, session_id: str, agent_id: str = "") -> None:
```

And update the call site in `_handle_message`:
```python
self._update_session_state(event, hook, session_id, msg.get("agent_id", ""))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat(daemon): track active subagents per session"
```

### Task 4: Daemon — suppress eviction for sessions with active subagents

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:211-219` (`_evict_stale_sessions`)
- Test: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests**

```python
def test_staleness_skips_sessions_with_active_subagents():
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,
        "subagents": {"a1"},
    }
    d._evict_stale_sessions()
    assert "s1" in d._session_states  # NOT evicted

def test_staleness_evicts_after_all_subagents_stop():
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,
        "subagents": set(),  # empty — all subagents stopped
    }
    d._evict_stale_sessions()
    assert "s1" not in d._session_states  # evicted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py::test_staleness_skips_sessions_with_active_subagents -v`
Expected: FAIL — session gets evicted despite having subagents

- [ ] **Step 3: Update _evict_stale_sessions**

```python
def _evict_stale_sessions(self) -> None:
    now = time.time()
    stale = [
        sid for sid, s in self._session_states.items()
        if now - s["last_event"] > self._session_staleness_timeout
        and not s.get("subagents")
    ]
    for sid in stale:
        logger.info("Evicting stale session: %s", sid[:12])
        del self._session_states[sid]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "staleness"`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "fix(daemon): suppress eviction for sessions with active subagents"
```

### Task 5: Daemon — sessions with active subagents count as "working"

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:166-177` (`_compute_display_state`)
- Test: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests**

```python
def test_idle_session_with_subagents_counts_as_working():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),
        "subagents": {"a1"},
    }
    assert d._compute_display_state() == "working_1"

def test_multiple_sessions_with_subagents_count_working():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(), "subagents": {"a1"},
    }
    d._session_states["s2"] = {
        "state": "working", "last_event": time.time(),
    }
    assert d._compute_display_state() == "working_2"

def test_session_with_empty_subagents_not_counted_as_working():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),
        "subagents": set(),
    }
    assert d._compute_display_state() == "idle"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "subagents_count"`
Expected: FAIL — idle sessions with subagents not counted as working

- [ ] **Step 3: Update _compute_display_state**

```python
def _compute_display_state(self) -> str:
    if not self._session_states:
        return "sleeping"
    working_count = sum(
        1 for s in self._session_states.values()
        if s["state"] == "working" or s.get("subagents")
    )
    if working_count > 0:
        return f"working_{min(working_count, 3)}"
    if any(s["state"] == "thinking" for s in self._session_states.values()):
        return "thinking"
    if any(s["state"] == "confused" for s in self._session_states.values()):
        return "confused"
    return "idle"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat(daemon): sessions with active subagents count as working"
```

### Task 6: Edge case tests and integration

**Files:**
- Test: `host/tests/test_session_state.py`

- [ ] **Step 1: Write edge case tests**

```python
@pytest.mark.asyncio
async def test_session_end_clears_subagents():
    """SessionEnd removes session entirely, even with active subagents."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "working", "last_event": time.time(), "subagents": {"a1", "a2"},
    }
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert "s1" not in d._session_states
    # Subsequent SubagentStop for orphaned agent is safe no-op
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert "s1" not in d._session_states

@pytest.mark.asyncio
async def test_duplicate_subagent_start_is_idempotent():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["subagents"] == {"a1"}

def test_working_session_with_subagents_counts_once():
    """A session that is both state=working AND has subagents counts as 1, not 2."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "working", "last_event": time.time(), "subagents": {"a1"},
    }
    assert d._compute_display_state() == "working_1"
```

- [ ] **Step 2: Write integration test for the full subagent lifecycle**

```python
@pytest.mark.asyncio
async def test_subagent_lifecycle_prevents_sleeping():
    """Full lifecycle: session starts, spawns subagent, parent goes idle,
    subagent stops, then session can be evicted."""
    d = make_daemon()

    # Session starts and begins working
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert d._compute_display_state() == "idle"

    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._compute_display_state() == "working_1"

    # Subagent spawned
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert d._compute_display_state() == "working_1"

    # Parent goes idle (Stop hook fires) — but subagent still running
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    # Session state is "idle" but subagent keeps it counted as working
    assert d._session_states["s1"]["state"] == "idle"
    assert d._compute_display_state() == "working_1"

    # Staleness check — should NOT evict (subagent active)
    d._session_staleness_timeout = 0  # force everything to be "stale"
    d._evict_stale_sessions()
    assert "s1" in d._session_states

    # Subagent finishes
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert d._compute_display_state() == "idle"

    # Now staleness check CAN evict
    d._session_states["s1"]["last_event"] = time.time() - 9999
    d._evict_stale_sessions()
    assert "s1" not in d._session_states
    assert d._compute_display_state() == "sleeping"
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "session_end_clears or duplicate_subagent or counts_once or lifecycle_prevents"`
Expected: ALL PASS

- [ ] **Step 4: Run full test suite**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add host/tests/test_session_state.py
git commit -m "test(daemon): add subagent edge case and lifecycle tests"
```

## Chunk 3: Finalize

### Task 7: Update TODO.md and hook installer detection

**Files:**
- Modify: `TODO.md`
- Modify: `CLAUDE.md` (session state model documentation)

- [ ] **Step 1: Update TODO.md**

Move "Agent/subagent tracking" from "Future Considerations" to a completed section. Add a new entry:

```markdown
## Subagent Tracking (v1.2.0) — Complete

- [x] **SubagentStart/SubagentStop hooks** — New hooks registered and forwarded to daemon via the hook handler script.
- [x] **Per-session subagent tracking** — `subagents: set[agent_id]` tracked per session in daemon state dict.
- [x] **Eviction suppression** — Sessions with active subagents are never evicted by staleness checker.
- [x] **Display state integration** — Sessions with active subagents count as "working" in display state computation, preventing Clawd from sleeping during long subagent tasks.
```

- [ ] **Step 2: Update CLAUDE.md session state model**

In the "Session State Model" section, add under bullet points:
```
- **Subagent tracking**: `SubagentStart`/`SubagentStop` hooks track active `agent_id`s per session. Sessions with active subagents are never evicted and count as "working" in display state.
```

- [ ] **Step 3: Reinstall hooks on a running instance**

After implementation, the user needs to reinstall hooks to pick up `SubagentStart`/`SubagentStop`. The menu bar app's "Install Hooks" button handles this, or:

```bash
cd host && .venv/bin/python -c "from clawd_tank_menubar.hooks import install_hooks, install_notify_script; install_notify_script(); install_hooks()"
```

- [ ] **Step 4: Final commit**

```bash
git add TODO.md CLAUDE.md
git commit -m "docs: update TODO and CLAUDE.md for subagent tracking"
```
