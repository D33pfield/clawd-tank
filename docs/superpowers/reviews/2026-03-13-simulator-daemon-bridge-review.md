# Simulator-Daemon Bridge Code Review

**Date:** 2026-03-13
**Commits:** fb533fe..fa9c5ff (11 commits)
**Scope:** Full simulator-daemon TCP bridge feature: C TCP listener, Python SimClient transport, multi-transport daemon refactor, menubar migration

---

## Code Review Summary

This is a well-architected feature that enables the Python daemon to drive the SDL2 simulator over TCP, completing the Claude Code hooks -> daemon -> display pipeline without real hardware. The implementation spans ~2,400 lines across C (simulator) and Python (host), with clean separation of concerns and solid test coverage. The commit history shows iterative refinement through review feedback.

Overall assessment: **Good quality, production-ready with minor issues.** The architecture is clean and extensible, the thread-safety approach is sound on both sides, and the tests cover the important paths. A few items below merit attention.

---

## Critical Issues

None found.

---

## High Priority Issues

### H1. TCP listener binds to INADDR_ANY -- exposes simulator to network

**File:** `simulator/sim_socket.c`, line 200

The TCP listener binds to `0.0.0.0` (all interfaces), meaning any machine on the network can connect and inject notifications into the simulator. This is a development tool, but on shared networks or when running on a laptop at a cafe, this could allow arbitrary JSON injection that triggers UI state changes.

```c
.sin_addr.s_addr = htonl(INADDR_ANY),
```

**Recommendation:** Bind to `INADDR_LOOPBACK` (`127.0.0.1`) instead. The daemon's `SimClient` already connects to `127.0.0.1` by default. If remote access is ever needed, it can be opt-in via a `--listen-addr` flag.

```c
.sin_addr.s_addr = htonl(INADDR_LOOPBACK),
```

### H2. `uint16_t` truncation for line_len passed to parser

**File:** `simulator/sim_socket.c`, lines 125-128

```c
int line_len = (int)(newline - line_start);
if (line_len > 0) {
    ble_evt_t evt;
    int rc = sim_ble_parse_json(line_start, (uint16_t)line_len, &evt);
```

The receive buffer is 4096 bytes. A line can be up to 4095 bytes, but `uint16_t` maxes at 65535 -- so there is no actual truncation risk *today*. However, `sim_ble_parse_json` takes `uint16_t len`, matching the firmware's BLE 512-byte MTU constraint. If the buffer size ever increases past 65535 or if this interface is reused elsewhere, the cast would silently truncate. This is low-risk given the current 4KB buffer but worth noting.

**Recommendation:** Either document that `sim_ble_parse_json` accepts `uint16_t` because it mirrors the firmware API (and the buffer is intentionally small), or change its signature to accept `size_t` since the simulator is not constrained by BLE MTU.

### H3. Config write from SimClient lacks `action` field wrapping

**File:** `host/clawd_tank_daemon/sim_client.py`, line 107
**File:** `host/clawd_tank_menubar/app.py`, lines 197-198 and 211-212

`SimClient.write_config()` delegates to `write_notification()`, which just sends the payload as-is with a newline. The menubar app sends:
```python
payload = json.dumps({"brightness": value})
```

But the simulator's `handle_config_action()` in `sim_socket.c` (line 87-88) expects an `"action": "write_config"` field in the JSON:
```c
} else if (strcmp(action->valuestring, "write_config") == 0) {
```

And `sim_ble_parse_json` routes to config handling (return 2) only when it sees `"action": "write_config"`.

**However**, looking at the test `test_write_config` (line 83-84), it sends with the action field:
```python
result = await client.write_config(
    json.dumps({"action": "write_config", "brightness": 200})
)
```

The issue is the **menubar app** sends payloads *without* the `"action"` field when writing config. The BLE path (`ble_client.py` line 115-131) writes directly to the config GATT characteristic, which is a separate endpoint that does not need an action discriminator. But `SimClient.write_config` goes through the same TCP channel as notifications, so it **must** include `"action": "write_config"` for the simulator to route it correctly.

**This means brightness and sleep timeout changes from the menubar will be silently dropped when connected to the simulator transport.** The BLE transport will still work, but the sim transport will parse the JSON, find no `"action"` field, and return -1 from `sim_ble_parse_json`, logging a parse error.

**Recommendation:** Either:
1. Have `SimClient.write_config()` wrap the payload by injecting `"action": "write_config"`, or
2. Have `daemon.write_config()` wrap the payload before calling the transport, or
3. Have the menubar app always include `"action": "write_config"` in config payloads.

Option (1) is cleanest since it keeps the wrapping in the transport layer that needs it:
```python
async def write_config(self, payload: str) -> bool:
    """Send a config write payload. Wraps with action field for TCP protocol."""
    data = json.loads(payload)
    data["action"] = "write_config"
    return await self.write_notification(json.dumps(data))
```

---

## Medium Priority Issues

### M1. `_handle_disconnect` does not close writer -- potential resource leak

**File:** `host/clawd_tank_daemon/sim_client.py`, lines 109-114

```python
def _handle_disconnect(self) -> None:
    """Clean up state and notify on disconnect."""
    self._writer = None
    self._reader = None
    if self._on_disconnect_cb:
        self._on_disconnect_cb()
```

When a write fails and `_handle_disconnect` is called, the writer is set to None without calling `self._writer.close()`. The underlying socket and transport are abandoned rather than properly shut down. While Python's GC will eventually clean up, this could leave sockets in TIME_WAIT longer than necessary and may produce ResourceWarning in debug mode.

**Recommendation:** Close the writer before nulling it:
```python
def _handle_disconnect(self) -> None:
    if self._writer and not self._writer.is_closing():
        self._writer.close()
    self._writer = None
    self._reader = None
    if self._on_disconnect_cb:
        self._on_disconnect_cb()
```

### M2. Concurrent client connections are serialized but old client gets no disconnect event

**File:** `simulator/sim_socket.c`, lines 164-183

The listener thread handles one client at a time (`handle_client` blocks). If a second daemon connects while the first is still active, the second connection blocks in `accept()` until the first disconnects. This is fine for single-daemon use.

However, if the first client hangs (e.g., daemon process killed without FIN), the second connection will block indefinitely. The `listen()` backlog is 1, so a third would be refused.

**Recommendation:** Consider setting `SO_KEEPALIVE` on the client socket (or `TCP_KEEPINTVL`/`TCP_KEEPCNT` on macOS) to detect dead connections within a reasonable timeout. Or add a `recv` timeout so a stale client does not block forever:
```c
struct timeval tv = { .tv_sec = 30, .tv_usec = 0 };
setsockopt(client_fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
```

### M3. `settimeofday()` in parser is a side effect that may require privileges

**File:** `simulator/sim_ble_parse.c`, lines 51-53

The `set_time` handler calls `settimeofday()` which on macOS requires root privileges. This will silently fail (return -1 with EPERM) when running the simulator as a normal user. The log message "System time set to epoch..." is printed regardless of whether it succeeded.

**Recommendation:** Check the return value:
```c
if (settimeofday(&tv, NULL) == 0) {
    printf("[tcp] System time set to epoch %lld\n", (long long)tv.tv_sec);
} else {
    printf("[tcp] settimeofday failed (errno=%d), time display may be wrong\n", errno);
}
```

Or, since this is a simulator, consider storing the time offset in a global variable rather than modifying the system clock.

### M4. `read_config` timeout race with interleaved writes

**File:** `host/clawd_tank_daemon/sim_client.py`, lines 87-103

`read_config` sends a request and reads the next line from the reader. If another coroutine calls `write_notification` concurrently (which does not read), the lock protects against concurrent access. However, if the simulator sends any unsolicited data (unlikely now but possible if the protocol evolves), `readline()` could consume it instead of the config response.

This is acceptable for the current protocol (simulator only responds to read_config), but worth documenting as a constraint.

### M5. No test for `daemon.read_config()` and `daemon.write_config()` public API

**File:** `host/tests/test_daemon.py`

The new public methods `read_config()` and `write_config()` on `ClawdDaemon` are not directly tested. They iterate over transports, which is the new behavior, but no test verifies this routing logic (e.g., that `read_config` returns the first connected transport's config, or that `write_config` writes to all connected transports).

**Recommendation:** Add tests:
```python
@pytest.mark.asyncio
async def test_read_config_from_first_connected():
    daemon = ClawdDaemon()
    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    mock_transport.read_config = AsyncMock(return_value={"brightness": 100})
    daemon._transports["ble"] = mock_transport
    config = await daemon.read_config()
    assert config == {"brightness": 100}

@pytest.mark.asyncio
async def test_write_config_to_all_connected():
    daemon = ClawdDaemon(sim_port=19872)
    for name in daemon._transports:
        mock = AsyncMock()
        mock.is_connected = True
        mock.write_config = AsyncMock(return_value=True)
        daemon._transports[name] = mock
    result = await daemon.write_config('{"brightness": 200}')
    assert result is True
    for mock in daemon._transports.values():
        mock.write_config.assert_called_once()
```

### M6. Double-parse of config JSON on socket thread

**File:** `simulator/sim_socket.c`, lines 68-103

The `handle_config_action` function re-parses the JSON buffer that was already parsed by `sim_ble_parse_json`. The comment on line 72 acknowledges this as a "pragmatic trade-off." This is fine for a cold path, but there is a subtle issue: between the two parses, the buffer has been null-terminated at the newline position (line 124: `*newline = '\0'`), so the second parse sees a null-terminated string. This works correctly. Just noting it is intentional.

---

## Low Priority Suggestions

### L1. Magic port number could be a shared constant

The port `19872` appears in:
- `simulator/sim_socket.h` as `SIM_SOCKET_DEFAULT_PORT`
- `host/clawd_tank_daemon/sim_client.py` as `SIM_DEFAULT_PORT`

These are necessarily separate constants (C vs Python), but they should stay in sync. Consider adding a comment in each noting the counterpart.

### L2. Unused import in `daemon.py`

**File:** `host/clawd_tank_daemon/daemon.py`, line 12

```python
from typing import Optional, Protocol, runtime_checkable
```

`Protocol` and `runtime_checkable` are imported but not used in this file -- `DaemonObserver` uses them, but `DaemonObserver` is defined locally while the imports may have been leftover from the refactor. Actually, `DaemonObserver` on line 27 does use both `@runtime_checkable` and `Protocol`. Confirmed correct, no issue.

### L3. Queue reset on shutdown

**File:** `simulator/sim_socket.c`

When `sim_socket_shutdown()` is called, the queue head/tail/count statics are not reset. If `sim_socket_init()` were called again after shutdown (which does not happen today), stale events could be processed. Consider zeroing queue state in `sim_socket_init()`.

### L4. `_on_quit` still accesses `_shutdown` directly

**File:** `host/clawd_tank_menubar/app.py`, line 232

```python
future = asyncio.run_coroutine_threadsafe(
    self._daemon._shutdown(), self._loop
)
```

The commit `fa9c5ff` migrated `read_config`, `write_config`, and `reconnect` to public API methods, but `_shutdown` is still accessed as a private method. Consider adding a public `shutdown()` method to `ClawdDaemon` for consistency, or document that `_shutdown` is semi-public.

### L5. `TransportClient.disconnect()` return type

**File:** `host/clawd_tank_daemon/transport.py`, line 15

```python
async def disconnect(self) -> None: ...
```

The Protocol is clean and minimal. One note: `BleClient.disconnect()` and `SimClient.disconnect()` both return `None`, which matches. Good.

---

## Positive Highlights

1. **Clean TransportClient Protocol.** The `TransportClient` Protocol in `transport.py` is exactly the right abstraction -- minimal (6 methods), uses Python's structural typing, and both `ClawdBleClient` and `SimClient` implement it without needing explicit inheritance. This is textbook Python protocol design.

2. **Thread-safe queue in C is well-implemented.** The ring buffer with pthread mutex in `sim_socket.c` is correct, simple, and efficient. The head/tail/count tracking is clean, and the mutex lock scope is tight (no locking during I/O).

3. **Newline-delimited JSON protocol reuse.** Using the same JSON format as BLE GATT writes (just newline-delimited over TCP) was an excellent decision. It means the simulator's parser (`sim_ble_parse.c`) mirrors the firmware's `parse_notification_json` exactly, reducing protocol divergence.

4. **Per-transport queues.** The design of giving each transport its own `asyncio.Queue` and sender task is clean. Transports operate independently -- a slow BLE reconnect does not block simulator delivery, and vice versa. This is the right architecture for multi-transport.

5. **Comprehensive test coverage on the Python side.** 8 tests for `SimClient` covering connect, write, disconnect, retry, callbacks, and failure cases. The daemon tests were properly updated for multi-transport. The `test_replay_active_concurrent_mutation_is_safe` test is particularly well-thought-out.

6. **Buffer overflow protection in TCP reader.** The oversized-line detection at `sim_socket.c:151-154` prevents the `recv` from being called with `nbytes=0`, which would cause a misleading disconnect. Good defensive coding.

7. **Incremental commit history.** The 11-commit sequence shows a disciplined development process: plan first, build bottom-up (parser, socket, integration, transport, client, daemon refactor, menubar migration), with fix-up commits addressing review feedback at each layer.

8. **Faithful parity with firmware parser.** Comparing `sim_ble_parse.c` with `firmware/main/ble_service.c:50-117`, the logic is identical for all action types. The only difference is the output mechanism (return code vs `xQueueSend`), which is exactly right.

---

## Recommendations

1. **Fix H3 (config write action field) before merging** -- this is a functional bug that will cause config writes to fail silently over the simulator transport.

2. **Fix H1 (bind to loopback)** -- a one-line change that eliminates network exposure.

3. **Add M5 tests for public config/reconnect API** -- these are the new daemon entry points that the menubar depends on.

4. **Consider M3 (settimeofday check)** -- users will likely see confusing time display without understanding why.

5. The overall architecture is clean and extensible. Adding a new transport (e.g., WebSocket for a browser-based viewer) would be straightforward: implement `TransportClient`, register in `ClawdDaemon.__init__`, done. Well designed.
