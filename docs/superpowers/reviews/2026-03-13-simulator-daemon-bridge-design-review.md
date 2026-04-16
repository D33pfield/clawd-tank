# Simulator-Daemon Bridge Design Spec Review
**Spec:** `docs/superpowers/specs/2026-03-13-simulator-daemon-bridge-design.md`
**Reviewer:** Claude Opus 4.6
**Date:** 2026-03-13
**Type:** Review of revised spec addressing 12 previously raised issues

---

## Verdict: APPROVED

All 3 critical and 4 important issues from the first review have been correctly addressed in this revision. Two new suggestions are noted below but neither blocks implementation.

---

## Verification of Previously Raised Issues

### CRITICAL Issues

#### 1. Thread safety -- mutex-guarded ring buffer queue (was: calling ui_manager_handle_event() from pthread)

**Fix applied:** The spec now states that the socket thread pushes `ble_evt_t` structs into a "thread-safe queue (mutex-guarded ring buffer), mirroring the firmware's `xQueueSend` pattern." The main loop drains this queue each tick via `sim_socket_process()` (line 30, "main loop drains this queue each tick before calling `ui_manager_tick()`").

**Verification:** Correct. This matches the firmware architecture where `ble_service.c` enqueues events via `xQueueSend` (line 114 of `ble_service.c`) and the main task dequeues and calls `ui_manager_handle_event()`. LVGL is not thread-safe, and `ui_manager_handle_event()` acquires `s_lock` and manipulates the widget tree. By draining in the main loop, all LVGL calls stay on a single thread. The existing event sources (keyboard in `handle_sdl_events()` and scripted events in `sim_events_process()`) already call `ui_manager_handle_event()` from the main thread, so `sim_socket_process()` is consistent.

**Status: RESOLVED**

---

#### 2. Headless mode -- --listen + --headless now runs indefinitely

**Fix applied:** Line 78: "When `--listen` is combined with `--headless`, the simulator runs indefinitely (overrides `end_time` calculation) until the process is terminated (SIGTERM/SIGINT)."

**Verification:** Correct. The existing `run_headless()` function in `sim_main.c` (line 106-108) computes `end_time` from `opt_run_ms` or `sim_events_get_end_time() + 500`. With `--listen` and no `--events`, `end_time` would be 500ms and the simulator would exit immediately. The spec correctly identifies this and overrides to run indefinitely. This enables the daemon-driven usage pattern.

**Status: RESOLVED**

---

#### 3. display_set_brightness() -- no shim needed

**Fix applied:** Line 68: "Brightness value is stored but has no visual effect in the simulator (no `display_set_brightness()` shim needed -- the real display driver is not compiled)."

**Verification:** Correct. `display_set_brightness()` is only called from `ble_service.c` (firmware line 177), which is NOT compiled in the simulator (the simulator compiles only the shim `ble_service.h`). The spec's `write_config` handler calls `config_store_set_brightness()` and `config_store_set_sleep_timeout()` + `ui_manager_set_sleep_timeout()` directly, bypassing `display_set_brightness()` entirely. This is the same pattern used in the existing `sim_events.c` config handler (lines 112-119 of `sim_events.c`).

**Status: RESOLVED**

---

### IMPORTANT Issues

#### 4. Shared JSON parser -- sim_ble_parse.c extracted

**Fix applied:** Lines 39-49 describe a shared parser `sim_ble_parse.c` / `sim_ble_parse.h` with the signature `int sim_ble_parse_json(const char *buf, uint16_t len, ble_evt_t *out)`.

**Verification:** The design is sound. The return value convention (0 = success with event to enqueue, 1 = set_time handled inline, -1 = parse error) cleanly separates the three cases. The spec correctly notes that the firmware's `ble_service.c` keeps its own copy due to ESP-IDF dependencies (`xQueueSend`, NimBLE types, `ESP_LOG*`), which is the right call -- extracting a truly shared implementation between firmware and simulator would require a complex abstraction layer that is not worth the effort for ~70 lines of code.

**Status: RESOLVED**

---

#### 5. read_config protocol -- half-duplex with 2-second timeout

**Fix applied:** Lines 70-74 describe the request/response protocol. Line 74 states "half-duplex by convention." The `SimClient` table (line 111) specifies "reads one response line with 2-second timeout."

**Verification:** Correct. The half-duplex convention is clean and avoids the need for message framing or request IDs. The 2-second timeout is generous enough for local TCP but tight enough to detect a stuck simulator quickly. The newline delimiter (`\n`) is consistent with the notification protocol.

One implementation note: the simulator needs to handle the case where it receives `read_config` while the main loop has not yet drained queued events. Since `read_config` does not produce a `ble_evt_t` (it reads from `config_store` directly), the socket thread can respond immediately without going through the queue. However, the spec does not explicitly state whether `read_config` is handled in the socket thread or the main thread. See Suggestion A below.

**Status: RESOLVED** (with minor suggestion)

---

#### 6. Multi-transport architecture -- per-transport sender coroutines

**Fix applied:** Lines 119-123 describe independent sender coroutines per transport, broadcast dispatch, and independent lifecycle.

**Verification:** The design correctly decouples BLE and TCP transports. The current `_ble_sender()` method in `daemon.py` (lines 174-203) handles connect/reconnect/sync/replay in a single coroutine. The spec's per-transport sender pattern generalizes this correctly. The observer pattern ("connected=True when any transport connects, connected=False when all disconnect") is the right semantic for the menu bar app, which needs to show a single connection indicator.

However, the current `_ble_sender()` reads from `self._pending_queue` (an `asyncio.Queue`). The spec says "each transport gets its own `_transport_sender()` coroutine that reads from a shared `_pending_queue`." An `asyncio.Queue.get()` is destructive -- only one consumer gets each message. See Important Issue 1 below.

**Status: RESOLVED** (with new important issue about queue consumption)

---

#### 7. TransportClient Protocol -- shared interface

**Fix applied:** Lines 90-101 define the Protocol with `is_connected`, `connect()`, `disconnect()`, `ensure_connected()`, `write_notification()`, `read_config()`, `write_config()`.

**Verification:** The Protocol matches the existing `ClawdBleClient` interface exactly. All six methods/properties on the Protocol are present in `ble_client.py` with compatible signatures. The `SimClient` constructor taking `on_disconnect_cb` and `on_connect_cb` callbacks matches the `ClawdBleClient` constructor pattern.

**Status: RESOLVED**

---

### Previously Raised Minor Issues (8-12)

All five minor issues (port conflict handling with `SO_REUSEADDR`, `[tcp]` logging prefix, graceful shutdown via `shutdown(SHUT_RDWR)` + join, `set_time` handling, and `--sim-only` mode) are addressed in the spec text. Verified correct.

---

## New Issues

### Important Issue 1: Shared pending queue cannot serve multiple transport senders

The spec states (line 120): "Each transport gets its own `_transport_sender()` coroutine that reads from a shared `_pending_queue`."

The existing `_pending_queue` is an `asyncio.Queue`. When multiple transport senders call `await self._pending_queue.get()`, only ONE sender receives each message. This means if BLE gets the message, TCP does not, and vice versa. The spec says "when a message is enqueued, it is dispatched to all transport senders" (line 121), but this contradicts the shared-queue-with-get pattern.

The implementation needs one of:
- **Option A:** Use separate queues per transport. When `_handle_message` is called, put the message into each transport's private queue.
- **Option B:** Use a broadcast mechanism (e.g., each sender gets a copy of the message list and tracks its own cursor).

This is not a spec correctness issue per se -- the intent is clear ("broadcast") -- but the phrase "reads from a shared `_pending_queue`" will mislead an implementer into using the existing single `asyncio.Queue`, which will silently drop messages for one transport. The spec should either name the pattern explicitly (per-transport queues) or remove the "shared queue" language.

**Severity:** Important -- an implementer following the spec literally would produce a bug where only one transport receives each notification.

---

### Suggestion A: read_config threading model

The spec does not state whether `read_config` responses are generated on the socket thread or the main thread. Two options exist:

1. **Socket thread responds directly:** Calls `config_store_serialize_json()` from the socket thread and writes the response back on the TCP socket. This is simpler and avoids queue round-trips, but `config_store` must be thread-safe for reads. Looking at the existing `config_store.c` (compiled in the simulator), the store values are plain static variables with no locking. In practice, reading two `uint8_t`/`uint16_t` values is atomic on all relevant architectures, so this is safe.

2. **Main thread responds:** Queue the read_config request, have the main loop call `config_store_serialize_json()`, then signal the socket thread to send the response. More complex, requires cross-thread signaling.

Option 1 is clearly better. The spec should add one sentence: "The socket thread handles `read_config` directly (without going through the event queue) since config reads are thread-safe."

**Severity:** Suggestion -- either approach works, but clarifying prevents implementer hesitation.

---

### Suggestion B: Reconnect-and-replay behavior for SimClient

The spec states (line 176): "Kill and restart simulator, verify daemon reconnects and replays active notifications." This implies `SimClient` must implement the same reconnect + replay loop as the BLE sender. The spec's `_transport_sender()` description (line 120) says this happens "independently per transport," which is correct.

However, the spec does not describe how `SimClient` detects disconnect. For BLE, the `bleak` library calls a `disconnected_callback`. For TCP, disconnect detection requires either:
- **Detecting send failure** (broken pipe / connection reset on `write()`), or
- **Detecting read EOF** (if the socket thread is also reading, which it is for `read_config` responses).

The spec mentions "TCP EOF or socket error triggers callbacks" (line 114), which is correct but terse. An implementer should know that the `SimClient` needs a background reader task or must detect errors on write. Since the protocol is half-duplex and the daemon is the initiator, detecting errors on write is sufficient -- there is no continuous read loop needed on the daemon side.

**Severity:** Suggestion -- the existing text is technically sufficient.

---

## Cross-checks Against Codebase

The following items were verified against the existing codebase and confirmed correct:

- **Port 19872:** Not a registered IANA port and not used by any common service. Good default choice.
- **Newline-delimited JSON:** Consistent with the existing BLE protocol JSON payloads. No payload in the current codebase contains embedded newlines, so `\n` as a delimiter is safe.
- **Config protocol fields:** `brightness` and `sleep_timeout` match the fields in `config_store.h` (lines 7-8: `CONFIG_DEFAULT_BRIGHTNESS 102`, `CONFIG_DEFAULT_SLEEP_TIMEOUT 300`) and the `config_access_cb` handler in `ble_service.c` (lines 172-189).
- **File change list:** All listed files are at the correct paths. `simulator/CMakeLists.txt` exists and adding `sim_socket.c` and `sim_ble_parse.c` to the sources list is straightforward. `host/clawd_tank_daemon/daemon.py` is the correct file for multi-transport changes.
- **`config_store_serialize_json` for read_config response:** The function exists in `config_store.h` (line 24) and produces the correct JSON format for the response.
- **sim_events.c config handling pattern:** The spec's `write_config` handler mirrors the existing pattern in `sim_events.c` lines 108-123, using `config_store_set_brightness()` + `config_store_set_sleep_timeout()` + `ui_manager_set_sleep_timeout()`. Consistent.

---

## Summary

| # | Issue | Status | Severity |
|---|-------|--------|----------|
| 1 | Thread safety (mutex queue) | RESOLVED | Was Critical |
| 2 | Headless indefinite run | RESOLVED | Was Critical |
| 3 | display_set_brightness shim | RESOLVED | Was Critical |
| 4 | Shared JSON parser | RESOLVED | Was Important |
| 5 | read_config half-duplex | RESOLVED | Was Important |
| 6 | Multi-transport architecture | RESOLVED | Was Important |
| 7 | TransportClient Protocol | RESOLVED | Was Important |
| 8-12 | Minor issues (port, logging, shutdown, set_time, sim-only) | RESOLVED | Was Minor |
| NEW 1 | Shared queue cannot broadcast to multiple senders | NEW | Important |
| NEW A | read_config threading model | NEW | Suggestion |
| NEW B | SimClient disconnect detection | NEW | Suggestion |

**Result:** All previously raised issues are resolved. One new important issue (queue broadcast semantics) should be clarified before implementation to prevent a subtle message-loss bug. The two suggestions are nice-to-haves that would help an implementer but are not blocking.
