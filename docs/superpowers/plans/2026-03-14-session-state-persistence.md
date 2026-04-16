# Session State Persistence Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist daemon session state to disk so that restarting the menu bar app immediately shows the correct Clawd animation for already-running Claude Code sessions, instead of sleeping until the next hook fires.

**Architecture:** A new `session_store.py` module handles save/load of session state as JSON (`~/.clawd-tank/sessions.json`). Writes are atomic (temp file + `os.replace`). The daemon saves only when session state or subagents actually change (not on `last_event`-only updates), and loads on startup with an immediate eviction pass. The existing staleness eviction naturally cleans up sessions that died while the daemon was down. Sets (subagents) are serialized as JSON arrays and deserialized back to sets.

**Tech Stack:** Python stdlib (`json`, `pathlib`, `os`, `tempfile`). Pytest for tests.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `host/clawd_tank_daemon/session_store.py` (create) | `save_sessions()` / `load_sessions()` — atomic JSON serialization with set↔list conversion and entry validation |
| `host/clawd_tank_daemon/daemon.py` (modify) | Instance-level `_sessions_path`, save on structural state changes, load on init, immediate eviction on startup |
| `host/tests/test_session_store.py` (create) | Unit tests for save/load round-trips and error handling |
| `host/tests/test_session_state.py` (modify) | Integration tests for daemon persistence behavior |

---

## Chunk 1: Session Store and Daemon Integration

### Task 1: session_store.py — save/load with tests

**Files:**
- Create: `host/clawd_tank_daemon/session_store.py`
- Create: `host/tests/test_session_store.py`

- [ ] **Step 1: Write failing tests**

Create `host/tests/test_session_store.py`:

```python
"""Tests for session state persistence."""

import json
import time
import pytest
from pathlib import Path

from clawd_tank_daemon.session_store import save_sessions, load_sessions


def test_round_trip_basic(tmp_path):
    path = tmp_path / "sessions.json"
    states = {
        "s1": {"state": "working", "last_event": 1234567890.0},
        "s2": {"state": "idle", "last_event": 1234567891.0},
    }
    save_sessions(states, path)
    loaded = load_sessions(path)
    assert loaded == states


def test_round_trip_with_subagents(tmp_path):
    path = tmp_path / "sessions.json"
    states = {
        "s1": {
            "state": "idle",
            "last_event": 1234567890.0,
            "subagents": {"a1", "a2"},
        },
    }
    save_sessions(states, path)
    loaded = load_sessions(path)
    assert loaded["s1"]["subagents"] == {"a1", "a2"}
    assert isinstance(loaded["s1"]["subagents"], set)


def test_round_trip_empty_subagents(tmp_path):
    path = tmp_path / "sessions.json"
    states = {
        "s1": {"state": "idle", "last_event": 1234567890.0, "subagents": set()},
    }
    save_sessions(states, path)
    loaded = load_sessions(path)
    assert loaded["s1"]["subagents"] == set()


def test_round_trip_empty_dict(tmp_path):
    path = tmp_path / "sessions.json"
    save_sessions({}, path)
    loaded = load_sessions(path)
    assert loaded == {}


def test_load_missing_file(tmp_path):
    path = tmp_path / "nonexistent.json"
    loaded = load_sessions(path)
    assert loaded == {}


def test_load_corrupt_file(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("not valid json {{{")
    loaded = load_sessions(path)
    assert loaded == {}


def test_load_empty_file(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("")
    loaded = load_sessions(path)
    assert loaded == {}


def test_load_invalid_entries_skipped(tmp_path):
    """Entries missing required keys are dropped."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "good": {"state": "idle", "last_event": 1.0},
        "bad_no_state": {"last_event": 1.0},
        "bad_no_event": {"state": "idle"},
        "bad_garbage": {"foo": "bar"},
    }))
    loaded = load_sessions(path)
    assert "good" in loaded
    assert "bad_no_state" not in loaded
    assert "bad_no_event" not in loaded
    assert "bad_garbage" not in loaded


def test_save_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "dir" / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": 1.0}}, path)
    assert path.exists()
    loaded = load_sessions(path)
    assert "s1" in loaded


def test_save_overwrites_existing(tmp_path):
    path = tmp_path / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": 1.0}}, path)
    save_sessions({"s2": {"state": "working", "last_event": 2.0}}, path)
    loaded = load_sessions(path)
    assert "s1" not in loaded
    assert "s2" in loaded


def test_save_is_atomic(tmp_path):
    """No partial files left behind — temp files cleaned up."""
    path = tmp_path / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": 1.0}}, path)
    # Only the target file should exist, no .tmp files
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name == "sessions.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_store.py -v`
Expected: FAIL — `session_store` module does not exist

- [ ] **Step 3: Implement session_store.py**

Create `host/clawd_tank_daemon/session_store.py`:

```python
"""Persist daemon session state to disk for restart recovery."""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("clawd-tank")

SESSIONS_PATH = Path.home() / ".clawd-tank" / "sessions.json"


def save_sessions(sessions: dict[str, dict], path: Path = SESSIONS_PATH) -> None:
    """Save session states to JSON atomically. Sets are converted to sorted lists."""
    serializable = {}
    for sid, state in sessions.items():
        entry = {**state}
        if "subagents" in entry:
            entry["subagents"] = sorted(entry["subagents"])
        serializable[sid] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            os.write(fd, json.dumps(serializable).encode())
            os.close(fd)
            os.replace(tmp_path, str(path))
        except OSError:
            os.close(fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError:
        logger.warning("Failed to save session state to %s", path)


def load_sessions(path: Path = SESSIONS_PATH) -> dict[str, dict]:
    """Load session states from JSON. Returns empty dict on any error.

    Entries missing required keys (state, last_event) are silently dropped.
    """
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
        valid = {}
        for sid, state in data.items():
            if "state" not in state or "last_event" not in state:
                continue
            if "subagents" in state:
                state["subagents"] = set(state["subagents"])
            valid[sid] = state
        return valid
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_store.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_daemon/session_store.py host/tests/test_session_store.py
git commit -m "feat(daemon): add session state save/load for restart recovery"
```

### Task 2: Daemon — integrate persistence

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:1-19` (imports)
- Modify: `host/clawd_tank_daemon/daemon.py:86-127` (`__init__` — add `_sessions_path`, load sessions, run initial eviction)
- Modify: `host/clawd_tank_daemon/daemon.py:129-164` (`_handle_message` — save after structural state change)
- Modify: `host/clawd_tank_daemon/daemon.py:182-225` (`_update_session_state` — return bool for structural changes)
- Modify: `host/clawd_tank_daemon/daemon.py:227-236` (`_evict_stale_sessions` — save after eviction)
- Test: `host/tests/test_session_state.py`

**Key design decisions from review:**
1. **Instance-level path** — `self._sessions_path` allows tests to override via constructor or direct assignment, avoiding the broken `patch("...daemon.SESSIONS_PATH")` pattern.
2. **Save only on structural changes** — `_update_session_state` returns `True` when `state` or `subagents` actually changed (not on `last_event`-only updates like `tool_use` when already `working`). This avoids excessive disk writes from frequent `PreToolUse` hooks.
3. **Immediate eviction on startup** — Stale loaded sessions are evicted in `__init__`, not after a 30s delay.

- [ ] **Step 1: Write failing tests**

Add to end of `host/tests/test_session_state.py`:

```python
# --- Session state persistence ---


def make_daemon_with_path(sessions_path):
    """Create a test daemon that uses a custom sessions file path."""
    d = ClawdDaemon(sim_only=True)
    d._transports.clear()
    d._transport_queues.clear()
    d._sessions_path = sessions_path
    return d


@pytest.mark.asyncio
async def test_daemon_persists_on_handle_message(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert path.exists()
    data = json.loads(path.read_text())
    assert "s1" in data
    assert data["s1"]["state"] == "registered"


@pytest.mark.asyncio
async def test_daemon_does_not_persist_on_last_event_only_update(tmp_path):
    """tool_use when already working only updates last_event — no disk write."""
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    d._persist_sessions()  # initial save
    mtime_before = path.stat().st_mtime_ns
    # Small delay to ensure mtime would differ
    import time as _time; _time.sleep(0.01)
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    mtime_after = path.stat().st_mtime_ns
    assert mtime_before == mtime_after  # no write happened


@pytest.mark.asyncio
async def test_daemon_persists_on_state_transition(tmp_path):
    """thinking → working is a structural change — should persist."""
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    data = json.loads(path.read_text())
    assert data["s1"]["state"] == "working"


def test_daemon_persists_on_eviction(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "idle", "last_event": time.time() - 9999}
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    data = json.loads(path.read_text())
    assert "s1" not in data


def test_daemon_loads_on_init(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "s1": {"state": "working", "last_event": time.time()},
    }))
    d = ClawdDaemon(sim_only=True, sessions_path=path)
    d._transports.clear()
    d._transport_queues.clear()
    assert "s1" in d._session_states
    assert d._session_states["s1"]["state"] == "working"


def test_daemon_loads_subagents_as_sets(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "s1": {
            "state": "idle",
            "last_event": time.time(),
            "subagents": ["a1", "a2"],
        },
    }))
    d = ClawdDaemon(sim_only=True, sessions_path=path)
    d._transports.clear()
    d._transport_queues.clear()
    assert d._session_states["s1"]["subagents"] == {"a1", "a2"}
    assert isinstance(d._session_states["s1"]["subagents"], set)


def test_daemon_startup_display_state_from_loaded_sessions(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "s1": {"state": "working", "last_event": time.time()},
    }))
    d = ClawdDaemon(sim_only=True, sessions_path=path)
    d._transports.clear()
    d._transport_queues.clear()
    assert d._compute_display_state() == "working_1"


def test_daemon_evicts_stale_sessions_on_startup(tmp_path):
    """Stale sessions from disk are evicted immediately, not after 30s."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "stale": {"state": "working", "last_event": time.time() - 9999},
        "fresh": {"state": "idle", "last_event": time.time()},
    }))
    d = ClawdDaemon(sim_only=True, sessions_path=path)
    d._transports.clear()
    d._transport_queues.clear()
    assert "stale" not in d._session_states
    assert "fresh" in d._session_states


@pytest.mark.asyncio
async def test_session_end_persists_removal(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    data = json.loads(path.read_text())
    assert "s1" not in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "persist or loads or evicts_stale_sessions_on"`
Expected: FAIL — `_persist_sessions` doesn't exist, `sessions_path` constructor param doesn't exist

- [ ] **Step 3: Implement persistence in daemon.py**

**3a. Add import** at top of `daemon.py` (after line 18, the `transport` import):
```python
from .session_store import save_sessions, load_sessions, SESSIONS_PATH
```

**3b. Add `sessions_path` to `__init__`** — add parameter and store as instance variable:

Update `__init__` signature (line 87-93) to:
```python
    def __init__(
        self,
        observer: Optional["DaemonObserver"] = None,
        headless: bool = True,
        sim_port: int = 0,
        sim_only: bool = False,
        sessions_path: Path = SESSIONS_PATH,
    ):
```

Replace line 125 (`self._session_states: dict[str, dict] = {}`) with:
```python
        self._sessions_path = sessions_path
        self._session_states: dict[str, dict] = load_sessions(self._sessions_path)
```

Add immediate eviction after line 127 (`self._session_staleness_timeout: float = 600.0`):
```python
        self._evict_stale_sessions()
```

**3c. Make `_update_session_state` return `bool`** — `True` when state or subagents changed structurally (not just `last_event`):

Update signature (line 182):
```python
    def _update_session_state(self, event: str, hook: str, session_id: str, agent_id: str = "") -> bool:
```

Capture previous state before the mutation logic (after `now = time.time()`):
```python
        prev = self._session_states.get(session_id)
        prev_state = prev["state"] if prev else None
        prev_subagents = prev.get("subagents", set()).copy() if prev else None
```

At the end of the method (after all the `elif` branches), add:
```python
        cur = self._session_states.get(session_id)
        if cur is None:
            return prev is not None  # session was removed
        return cur["state"] != prev_state or cur.get("subagents", set()) != (prev_subagents or set())
```

**3d. Add `_persist_sessions` method** (after `_evict_stale_sessions`):
```python
    def _persist_sessions(self) -> None:
        save_sessions(self._session_states, self._sessions_path)
```

**3e. Conditional persist in `_handle_message`** — replace the bare `_update_session_state` call (line 142) and add persist after display state broadcast:

Replace line 142:
```python
        changed = self._update_session_state(event, hook, session_id, msg.get("agent_id", ""))
```

After the `_broadcast_display_state_if_changed` block (after line 164), add:
```python
        if changed:
            self._persist_sessions()
```

**3f. Persist after eviction** — add at the end of `_evict_stale_sessions` (after the for loop that deletes stale sessions), guarded by whether anything was evicted:

Replace the existing `_evict_stale_sessions` with:
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
        if stale:
            self._persist_sessions()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat(daemon): persist session state to disk for restart recovery"
```

### Task 3: Update documentation

**Files:**
- Modify: `TODO.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update TODO.md**

Add a new completed section after "Subagent Tracking (v1.2.0)":

```markdown
## Session State Persistence (v1.2.0) — Complete

- [x] **Atomic session state save/load** — `save_sessions()`/`load_sessions()` in `session_store.py` serialize session state dict to `~/.clawd-tank/sessions.json` with set↔list conversion. Atomic writes via temp file + `os.replace`.
- [x] **Smart persistence** — Session state saved only on structural changes (state transitions, subagent add/remove), not on every `last_event` timestamp update. Reduces disk writes during heavy tool use.
- [x] **Daemon startup recovery** — Loads saved sessions on init with immediate staleness eviction. Restarting the menu bar app immediately shows correct animation for running Claude Code sessions.
```

- [ ] **Step 2: Update CLAUDE.md**

In the "Session State Model" section, add after the subagent tracking bullet:

```
- **Session persistence**: Session state is saved atomically to `~/.clawd-tank/sessions.json` on structural state changes. Daemon loads saved state on startup with immediate stale session eviction, so restarting the menu bar app preserves the correct display state for running Claude Code sessions.
```

- [ ] **Step 3: Commit**

```bash
git add TODO.md CLAUDE.md
git commit -m "docs: document session state persistence"
```
